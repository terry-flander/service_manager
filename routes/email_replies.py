"""
routes/email_replies.py — Email template management and job reply compose/send.
"""
import re
import uuid
import time
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


def _substitute(text, job, customer=None):
    """
    Replace {{field}} placeholders with job/customer values.

    Available fields:
      {{customer_name}}          {{customer_email}}   {{customer_phone}}
      {{suburb}}                 {{address}}          {{reference}}
      {{scheduled_date}}         {{scheduled_time}}   {{end_time}}
      {{scheduled_date_formatted}}  — e.g. Friday, 1 May 2026
      {{scheduled_time_formatted}}  — e.g. 9:00am to 10:00am
      {{service_types}}          {{description}}      {{region_name}}
    """
    raw_date   = job['scheduled_date'] or ''
    raw_start  = job['scheduled_time'] or ''
    raw_end    = (job['end_time'] if 'end_time' in job.keys() else '') or ''
    full_name  = job['customer_name'] or ''
    first_name = full_name.split()[0] if full_name.strip() else ''

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
    }

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

        try:
            from email_sender import send_reply
            msg_id = send_reply(
                to_address  = to_addr,
                subject     = subject,
                body_text   = body,
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

        # Record in email_replies
        with get_db() as conn:
            conn.execute("""
                INSERT INTO email_replies
                    (job_id, message_id, in_reply_to, subject,
                     to_address, body, sent_by, template_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, msg_id, in_reply_to, subject,
                  to_addr, body, session.get('user_id'),
                  int(tmpl_id) if tmpl_id else None))
            conn.commit()

        flash(f'Reply sent to {to_addr}.', 'success')
        return redirect(url_for('jobs.job_detail', job_id=job_id))

    # ── Preview step ──────────────────────────────────────────────────────────
    if request.method == 'POST' and 'preview' in request.form:
        tmpl_id = request.form.get('template_id', '')
        subject = request.form.get('subject', '').strip()
        body    = request.form.get('body', '').strip()

        # Apply substitution to body only — subject is locked to thread subject
        body = _substitute(body, job)

        return render_template('jobs/reply_compose.html',
                               job=job, templates=templates,
                               preview={
                                   'to':      job['customer_email'] or '',
                                   'subject': thread_subject,
                                   'body':    body,
                               },
                               template_id=tmpl_id,
                               thread_subject=thread_subject)

    # ── GET — template picker ─────────────────────────────────────────────────
    return render_template('jobs/reply_compose.html',
                           job=job, templates=templates,
                           preview=None, template_id=None,
                           thread_subject=thread_subject)


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
    ])
