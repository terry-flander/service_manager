"""
routes/email_replies.py — Email template management and job reply compose/send.
"""
import re
import uuid
import time
import urllib.parse
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, session, jsonify)
from models import get_db

email_replies_bp = Blueprint('email_replies', __name__)

# ── Template field substitution ───────────────────────────────────────────────

def _fmt_date(iso):
    """'2026-05-01' → 'Friday, 1 May 2026'"""
    if not iso:
        return ''
    try:
        from datetime import datetime
        d = datetime.strptime(iso[:10], '%Y-%m-%d')
        return d.strftime('%A, %-d %B %Y')
    except ValueError:
        return iso


def _fmt_time_range(start, end):
    """
    '09:00', '10:00' → '9:00am to 10:00am'
    '13:30', '14:30' → '1:30pm to 2:30pm'
    """
    def _to_ampm(t):
        if not t:
            return ''
        try:
            from datetime import datetime
            dt = datetime.strptime(t[:5], '%H:%M')
            # %-I strips leading zero on Linux; use lstrip for safety
            return dt.strftime('%I:%M%p').lstrip('0').lower()
        except ValueError:
            return t
    start_fmt = _to_ampm(start)
    end_fmt   = _to_ampm(end)
    if start_fmt and end_fmt:
        return f"{start_fmt} to {end_fmt}"
    return start_fmt or end_fmt


def _build_feedback_link(job, full_name):
    """Build a pre-filled Google Form feedback link for this job/customer,
    using a URL template stored in settings (key 'feedback_form_url_template').

    The template should contain {name} and {reference} placeholders matching
    the entry.XXXXXXX query params from the Form's pre-filled link, e.g.:
      https://docs.google.com/forms/d/e/FORM_ID/viewform?usp=pp_url&entry.111={name}&entry.222={reference}

    Returns '' if no template has been configured yet.
    """
    try:
        from models import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='feedback_form_url_template'"
            ).fetchone()
        if not row or not row['value']:
            return ''
        template = row['value']
        return template.format(
            name=urllib.parse.quote_plus(full_name or ''),
            reference=urllib.parse.quote_plus(job['reference'] or ''),
        )
    except Exception:
        return ''


def _substitute(text, job, customer=None, totals=None, is_html=False):
    """
    Replace {{field}} placeholders with job/customer values.

    When is_html=True, field VALUES are HTML-escaped before insertion
    (so a customer name containing & or < can't break the markup), and
    {{feedback_link}} renders as a real <a href> tag instead of a bare
    URL. The template's own HTML markup is never escaped — only the
    substituted values are.

    Available fields:
      {{first_name}}             {{customer_name}}    {{customer_email}}
      {{customer_phone}}         {{suburb}}           {{address}}
      {{reference}}              {{description}}      {{region_name}}
      {{scheduled_date}}         {{scheduled_time}}   {{end_time}}
      {{scheduled_date_formatted}}  — e.g. Friday, 1 May 2026
      {{scheduled_time_formatted}}  — e.g. 9:00am to 10:00am
      {{service_types}}          {{invoice_pdf}}
      {{job_total}}              {{amount_due}}
      {{feedback_link}}
    """
    raw_date   = job['scheduled_date'] or ''
    raw_start  = job['scheduled_time'] or ''
    raw_end    = (job['end_time'] if 'end_time' in job.keys() else '') or ''
    full_name  = job['customer_name'] or ''
    first_name = full_name.split()[0] if full_name.strip() else ''

    # Totals — read from the job's stored subtotal/gst/total (kept in
    # sync by recalc_job_totals() on every job_parts/job change) unless
    # the caller already supplied a totals dict.
    if totals is None:
        try:
            _total = job['total'] or 0.0
            _paid = float(job['amount_paid'] or 0)
            totals = {'job_total': _total, 'amount_due': max(_total - _paid, 0)}
        except Exception:
            totals = {'job_total': 0.0, 'amount_due': 0.0}

    def _fmt_money(v):
        return f"${v:,.2f}"

    fields = {
        'first_name':                first_name,
        'customer_name':             full_name,
        'customer_email':            job['customer_email'] or '',
        'customer_phone':            job['customer_phone'] or '',
        'suburb':                    job['suburb']         or '',
        'address':                   job['address']        or '',
        'reference':                 job['reference']      or '',
        'scheduled_date':            raw_date,
        'scheduled_time':            raw_start,
        'end_time':                  raw_end,
        'scheduled_date_formatted':  _fmt_date(raw_date),
        'scheduled_time_formatted':  _fmt_time_range(raw_start, raw_end),
        'service_types':             job['service_types']  or '',
        'description':               job['description']    or '',
        'region_name':               (job['region_name'] if 'region_name' in job.keys() else '') or '',
        'invoice_pdf':               '[Invoice PDF attached]',
        'job_total':                 _fmt_money(totals.get('job_total', 0)),
        'amount_due':                _fmt_money(totals.get('amount_due', 0)),
        'feedback_link':             _build_feedback_link(job, full_name),
    }

    if is_html:
        import html as _html_mod
        for key, val in list(fields.items()):
            if key == 'feedback_link' and val:
                escaped_url = _html_mod.escape(val, quote=True)
                fields[key] = f'<a href="{escaped_url}">{escaped_url}</a>'
            else:
                fields[key] = _html_mod.escape(str(val))

    def replacer(m):
        key = m.group(1).strip()
        return fields.get(key, m.group(0))  # leave unknown placeholders as-is

    return re.sub(r'\{\{(\w+)\}\}', replacer, text)


def _get_thread_refs(conn, job_id):
    """
    Return (in_reply_to, references) for replying to the most recent message
    on a job's thread.
    """
    # Most recent message on this job (inbound or outbound)
    latest_in = conn.execute("""
        SELECT message_id FROM email_imports
        WHERE job_id=? ORDER BY imported_at DESC LIMIT 1
    """, (job_id,)).fetchone()

    latest_out = conn.execute("""
        SELECT message_id FROM email_replies
        WHERE job_id=? ORDER BY sent_at DESC LIMIT 1
    """, (job_id,)).fetchone()

    # All message IDs for References header
    all_in = conn.execute(
        "SELECT message_id FROM email_imports WHERE job_id=? ORDER BY imported_at",
        (job_id,)).fetchall()
    all_out = conn.execute(
        "SELECT message_id FROM email_replies WHERE job_id=? ORDER BY sent_at",
        (job_id,)).fetchall()

    all_ids = [r['message_id'] for r in all_in if r['message_id']] + \
              [r['message_id'] for r in all_out if r['message_id']]

    # in_reply_to = most recent message in thread
    if latest_out and latest_in:
        # whichever is more recent
        in_reply_to = latest_out['message_id'] or latest_in['message_id']
    elif latest_out:
        in_reply_to = latest_out['message_id']
    elif latest_in:
        in_reply_to = latest_in['message_id']
    else:
        in_reply_to = None

    references = ' '.join(all_ids)
    return in_reply_to, references


# ── Email Templates CRUD ──────────────────────────────────────────────────────

@email_replies_bp.route('/email-templates')
def templates_index():
    with get_db() as conn:
        templates = conn.execute(
            "SELECT * FROM email_templates ORDER BY name").fetchall()
    return render_template('email_templates/index.html', templates=templates)


@email_replies_bp.route('/email-templates/new', methods=['GET', 'POST'])
def new_template():
    if request.method == 'POST':
        name    = request.form.get('name', '').strip()
        subject = request.form.get('subject', '').strip()
        body    = request.form.get('body', '').strip()
        if not name or not subject or not body:
            flash('Name, subject and body are all required.', 'danger')
            return render_template('email_templates/form.html',
                                   tmpl=request.form, is_new=True)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO email_templates (name, subject, body) VALUES (?,?,?)",
                (name, subject, body))
            conn.commit()
        flash(f'Template "{name}" created.', 'success')
        return redirect(url_for('email_replies.templates_index'))
    return render_template('email_templates/form.html', tmpl=None, is_new=True)


@email_replies_bp.route('/email-templates/<int:tmpl_id>/edit', methods=['GET', 'POST'])
def edit_template(tmpl_id):
    with get_db() as conn:
        tmpl = conn.execute(
            "SELECT * FROM email_templates WHERE id=?", (tmpl_id,)).fetchone()
    if not tmpl:
        return "Template not found", 404

    if request.method == 'POST':
        name    = request.form.get('name', '').strip()
        subject = request.form.get('subject', '').strip()
        body    = request.form.get('body', '').strip()
        if not name or not subject or not body:
            flash('Name, subject and body are all required.', 'danger')
            return render_template('email_templates/form.html',
                                   tmpl=request.form, is_new=False, tmpl_id=tmpl_id)
        with get_db() as conn:
            conn.execute("""
                UPDATE email_templates
                SET name=?, subject=?, body=?,
                    updated_at=datetime('now')
                WHERE id=?
            """, (name, subject, body, tmpl_id))
            conn.commit()
        flash(f'Template "{name}" updated.', 'success')
        return redirect(url_for('email_replies.templates_index'))

    return render_template('email_templates/form.html',
                           tmpl=tmpl, is_new=False, tmpl_id=tmpl_id)


@email_replies_bp.route('/email-templates/<int:tmpl_id>/delete', methods=['POST'])
def delete_template(tmpl_id):
    with get_db() as conn:
        tmpl = conn.execute(
            "SELECT name FROM email_templates WHERE id=?", (tmpl_id,)).fetchone()
        if tmpl:
            conn.execute("DELETE FROM email_templates WHERE id=?", (tmpl_id,))
            conn.commit()
            flash(f'Template "{tmpl["name"]}" deleted.', 'success')
    return redirect(url_for('email_replies.templates_index'))


# ── Reply compose ─────────────────────────────────────────────────────────────

@email_replies_bp.route('/jobs/<int:job_id>/reply', methods=['GET', 'POST'])
def compose_reply(job_id):
    """
    GET  — show template picker + compose form
    POST (preview) — apply substitution, show editable preview
    POST (send)    — send the email, record in email_replies
    """
    with get_db() as conn:
        job = conn.execute("""
            SELECT j.*, r.name as region_name
            FROM jobs j JOIN regions r ON r.id=j.region_id
            WHERE j.id=?
        """, (job_id,)).fetchone()
        if not job:
            return "Job not found", 404
        templates = conn.execute(
            "SELECT * FROM email_templates ORDER BY name").fetchall()

        # Get the original subject from the first inbound email on this job
        orig = conn.execute("""
            SELECT subject FROM email_imports
            WHERE job_id=? AND status='ok'
            ORDER BY imported_at ASC LIMIT 1
        """, (job_id,)).fetchone()
        original_subject = orig['subject'] if orig else ''

    # Build the thread subject — Gmail requires exact match to keep thread
    def _thread_subject(subj):
        """Ensure subject starts with Re: for thread continuation."""
        if not subj:
            return 'Re: Your booking with The Flying Bike'
        return subj if subj.lower().startswith('re:') else f"Re: {subj}"

    thread_subject = _thread_subject(original_subject)

    # ── Send step ─────────────────────────────────────────────────────────────
    if request.method == 'POST' and 'send' in request.form:
        to_addr = request.form.get('to_address', '').strip()
        subject = request.form.get('subject', '').strip()
        body    = request.form.get('body', '').strip()
        tmpl_id = request.form.get('template_id') or None

        if not to_addr or not subject or not body:
            flash('To, Subject and Body are all required.', 'danger')
            return render_template('jobs/reply_compose.html',
                                   job=job, templates=templates,
                                   preview={'to': to_addr, 'subject': subject,
                                            'body': body},
                                   template_id=tmpl_id,
                                   thread_subject=thread_subject)

        with get_db() as conn:
            in_reply_to, references = _get_thread_refs(conn, job_id)

        # Check if invoice PDF attachment is requested
        has_invoice = '{{invoice_pdf}}' in body or '[Invoice PDF attached]' in body
        # Remove the placeholder from the HTML body
        body_html_clean = body.replace('{{invoice_pdf}}', '').replace('[Invoice PDF attached]', '').strip()
        from email_sender import _html_to_plain_fallback
        body_text_clean = _html_to_plain_fallback(body_html_clean)

        try:
            if has_invoice:
                # Generate the invoice PDF
                from invoice_pdf import generate_invoice_pdf
                with get_db() as _inv_conn:
                    _jp = _inv_conn.execute(
                        "SELECT * FROM job_parts WHERE job_id=? ORDER BY id",
                        (job_id,)).fetchall()
                _tax  = bool(job['tax_inclusive'])
                _sub, _gst, _tot = job['subtotal'] or 0.0, job['gst'] or 0.0, job['total'] or 0.0
                _buf  = generate_invoice_pdf(job, _jp, _tax, _sub, _gst, _tot)
                _fname = f"INV-{job['reference'].lower()}.pdf"
                from email_sender import send_reply_with_attachment
                msg_id = send_reply_with_attachment(
                    to_address          = to_addr,
                    subject             = subject,
                    body_text           = body_text_clean,
                    body_html           = body_html_clean,
                    attachment_bytes    = _buf.read(),
                    attachment_filename = _fname,
                    in_reply_to         = in_reply_to,
                    references          = references,
                )
            else:
                from email_sender import send_reply
                msg_id = send_reply(
                    to_address  = to_addr,
                    subject     = subject,
                    body_text   = body_text_clean,
                    body_html   = body_html_clean,
                    in_reply_to = in_reply_to,
                    references  = references,
                )
        except Exception as e:
            flash(f'Send failed: {e}', 'danger')
            return render_template('jobs/reply_compose.html',
                                   job=job, templates=templates,
                                   preview={'to': to_addr, 'subject': subject,
                                            'body': body},
                                   template_id=tmpl_id,
                                   thread_subject=thread_subject)

        # Record in email_replies — store the plain-text fallback, not the
        # HTML, so the in-app thread view (which escapes bodies as plain
        # text) keeps working exactly as before. The HTML version is only
        # used for the actual outgoing email.
        with get_db() as conn:
            conn.execute("""
                INSERT INTO email_replies
                    (job_id, message_id, in_reply_to, subject,
                     to_address, body, sent_by, template_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, msg_id, in_reply_to, subject,
                  to_addr, body_text_clean, session.get('user_id'),
                  int(tmpl_id) if tmpl_id else None))
            conn.commit()

        flash(f'Reply sent to {to_addr}.', 'success')
        return redirect(url_for('jobs.job_detail', job_id=job_id))

    # ── Preview step ──────────────────────────────────────────────────────────
    if request.method == 'POST' and 'preview' in request.form:
        tmpl_id = request.form.get('template_id', '')
        subject = request.form.get('subject', '').strip()
        body    = request.form.get('body', '').strip()

        # Apply substitution to body only — template body is now HTML
        body = _substitute(body, job, is_html=True)
        has_invoice = '{{invoice_pdf}}' in body or '[Invoice PDF attached]' in body

        from flask import url_for as _uf
        invoice_url = _uf('invoice.pdf_invoice', job_id=job_id) if has_invoice else None

        return render_template('jobs/reply_compose.html',
                               job=job, templates=templates,
                               preview={
                                   'to':      job['customer_email'] or '',
                                   'subject': thread_subject,
                                   'body':    body,
                               },
                               template_id=tmpl_id,
                               thread_subject=thread_subject,
                               invoice_url=invoice_url)

    # ── GET — template picker ─────────────────────────────────────────────────
    return render_template('jobs/reply_compose.html',
                           job=job, templates=templates,
                           preview=None, template_id=None,
                           thread_subject=thread_subject,
                           invoice_url=None)


# ── Reply Modal API ────────────────────────────────────────────────────────────

@email_replies_bp.route('/jobs/<int:job_id>/compose/templates')
def compose_template_list(job_id):
    """Return all email templates (id, name) for the reply modal dropdown."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name FROM email_templates ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])


@email_replies_bp.route('/jobs/<int:job_id>/compose/template/<int:tmpl_id>')
def compose_template_fetch(job_id, tmpl_id):
    """Return a single template with all substitution fields already replaced
    using the current job's data. Used by the reply modal to fill the editor
    when a template is selected."""
    with get_db() as conn:
        job = conn.execute("""
            SELECT j.*, r.name as region_name
            FROM jobs j JOIN regions r ON r.id=j.region_id
            WHERE j.id=?
        """, (job_id,)).fetchone()
        if not job:
            return jsonify({'ok': False, 'error': 'Job not found'}), 404
        tmpl = conn.execute(
            "SELECT * FROM email_templates WHERE id=?", (tmpl_id,)).fetchone()
        if not tmpl:
            return jsonify({'ok': False, 'error': 'Template not found'}), 404

        orig = conn.execute("""
            SELECT subject FROM email_imports
            WHERE job_id=? AND status='ok'
            ORDER BY imported_at ASC LIMIT 1
        """, (job_id,)).fetchone()

    original_subject = orig['subject'] if orig else ''
    def _thread_subject(s):
        if not s: return 'Re: Your booking with The Flying Bike'
        return s if s.lower().startswith('re:') else f'Re: {s}'

    subject_val = _substitute(tmpl['subject'], job)
    body_html   = _substitute(tmpl['body'], job, is_html=True)

    return jsonify({
        'ok':      True,
        'subject': subject_val,
        'body':    body_html,
        'thread_subject': _thread_subject(original_subject),
        'has_invoice': '{{invoice_pdf}}' in tmpl['body'] or
                       '[Invoice PDF attached]' in body_html,
    })


@email_replies_bp.route('/jobs/<int:job_id>/compose/send', methods=['POST'])
def compose_send(job_id):
    """Send a reply from the in-page reply modal. Accepts JSON."""
    from flask import jsonify as _j
    data     = request.get_json() or {}
    to_addr    = (data.get('to_address') or '').strip()
    subject    = (data.get('subject') or '').strip()
    body       = (data.get('body') or '').strip()
    tmpl_id    = data.get('template_id') or None
    contact_id = data.get('contact_id') or None

    if not to_addr or not subject or not body:
        return _j({'ok': False, 'error': 'To, Subject and Body are all required.'}), 400

    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return _j({'ok': False, 'error': 'Job not found'}), 404
        in_reply_to, references = _get_thread_refs(conn, job_id)

    has_invoice = '[Invoice PDF attached]' in body
    body_html_clean = body.replace('[Invoice PDF attached]', '').strip()
    from email_sender import _html_to_plain_fallback
    body_text = _html_to_plain_fallback(body_html_clean)

    try:
        if has_invoice:
            from invoice_pdf import generate_invoice_pdf
            with get_db() as _c:
                _jp = _c.execute(
                    "SELECT * FROM job_parts WHERE job_id=? ORDER BY id",
                    (job_id,)).fetchall()
            _buf = generate_invoice_pdf(
                job, _jp, bool(job['tax_inclusive']),
                job['subtotal'] or 0.0, job['gst'] or 0.0, job['total'] or 0.0)
            from email_sender import send_reply_with_attachment
            msg_id = send_reply_with_attachment(
                to_address=to_addr, subject=subject,
                body_text=body_text, body_html=body_html_clean,
                attachment_bytes=_buf.read(),
                attachment_filename=f"INV-{job['reference'].lower()}.pdf",
                in_reply_to=in_reply_to, references=references)
        else:
            from email_sender import send_reply
            msg_id = send_reply(
                to_address=to_addr, subject=subject,
                body_text=body_text, body_html=body_html_clean,
                in_reply_to=in_reply_to, references=references)
    except Exception as e:
        return _j({'ok': False, 'error': f'Send failed: {e}'}), 500

    with get_db() as conn:
        conn.execute("""
            INSERT INTO email_replies
                (job_id, message_id, in_reply_to, subject,
                 to_address, body, sent_by, template_id, contact_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, msg_id, in_reply_to, subject,
              to_addr, body_text, session.get('user_id'),
              int(tmpl_id) if tmpl_id else None,
              int(contact_id) if contact_id else None))
        conn.commit()

    return _j({'ok': True})


@email_replies_bp.route('/email-templates/preview-fields')
def preview_fields():
    """Return the complete list of available template substitution fields."""
    return jsonify([
        # Customer
        'first_name',
        'customer_name',
        'customer_email',
        'customer_phone',
        # Location
        'suburb',
        'address',
        'region_name',
        # Job
        'reference',
        'description',
        'service_types',
        # Schedule — raw
        'scheduled_date',
        'scheduled_time',
        'end_time',
        # Schedule — formatted
        'scheduled_date_formatted',   # e.g. Friday, 1 May 2026
        'scheduled_time_formatted',   # e.g. 9:00am to 10:00am
        'invoice_pdf',               # attaches the PDF invoice
        'job_total',                 # e.g. $150.00
        'amount_due',                # e.g. $150.00 (total minus any payment)
        'feedback_link',             # pre-filled Google Form link (if configured)
    ])


@email_replies_bp.route('/jobs/<int:job_id>/send-feedback-email', methods=['POST'])
def send_feedback_email(job_id):
    """One-click send of the 'Thank You' template (or whichever template
    name is configured) — no preview step, used from the mark-as-paid flow.
    """
    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return jsonify({'ok': False, 'error': 'Job not found'}), 404

        tmpl_row = conn.execute(
            "SELECT value FROM settings WHERE key='feedback_email_template_name'"
        ).fetchone()
        tmpl_name = (tmpl_row['value'] if tmpl_row else None) or 'Thank You'

        tmpl = conn.execute(
            "SELECT * FROM email_templates WHERE name=?", (tmpl_name,)).fetchone()
        if not tmpl:
            return jsonify({
                'ok': False,
                'error': f"No email template named '{tmpl_name}' found. "
                         f"Create one in Email Templates first."
            }), 400

        if not job['customer_email']:
            return jsonify({'ok': False, 'error': 'Customer has no email address on file'}), 400

        subject  = _substitute(tmpl['subject'], job)
        body_html = _substitute(tmpl['body'], job, is_html=True)

        from email_sender import _html_to_plain_fallback
        body_text = _html_to_plain_fallback(body_html)

        in_reply_to, references = _get_thread_refs(conn, job_id)

    try:
        from email_sender import send_reply
        msg_id = send_reply(
            to_address  = job['customer_email'],
            subject     = subject,
            body_text   = body_text,
            body_html   = body_html,
            in_reply_to = in_reply_to,
            references  = references,
        )
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Send failed: {e}'}), 500

    # Store the plain-text fallback in email_replies — the in-app thread
    # view escapes bodies as plain text, so storing HTML there would show
    # literal tags. The HTML is only used for the actual outgoing email.
    with get_db() as conn:
        conn.execute("""
            INSERT INTO email_replies
                (job_id, message_id, in_reply_to, subject,
                 to_address, body, sent_by, template_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, msg_id, in_reply_to, subject,
              job['customer_email'], body_text, session.get('user_id'), tmpl['id']))
        conn.commit()

    return jsonify({'ok': True})
