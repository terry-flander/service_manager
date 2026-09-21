"""
routes/booking.py — Public booking form submission endpoint.

Receives JSON from the static booking form, creates a job directly in the DB,
sends a Gmail notification and an HTML acknowledgement to the customer.
CORS origins and booking secret are configurable via settings / .env.
"""
import os
import re
import logging
import datetime
from flask import Blueprint, request, jsonify, make_response

booking_bp = Blueprint('booking', __name__)
log = logging.getLogger('app')

# These can be overridden by env vars; booking_cors_origins setting supplements them
TFB_SECRET = os.environ.get('TFB_BOOKING_SECRET', 'change-me')
NOTIFY_TO  = os.environ.get('GMAIL_USER', '')


def _get_allowed_origins():
    """Return set of allowed CORS origins from settings + hardcoded fallback."""
    from models import get_settings
    s = get_settings()
    origins = set()
    # From settings: comma-separated list
    extra = s.get('booking_cors_origins', '')
    if extra:
        for o in extra.split(','):
            o = o.strip()
            if o:
                origins.add(o)
    # App URL always allowed
    app_url = s.get('app_url', '').rstrip('/')
    if app_url:
        origins.add(app_url)
    return origins


def _cors_headers(origin):
    """Return CORS headers if origin is allowed, else empty dict."""
    allowed_origins = _get_allowed_origins()
    allowed = origin in allowed_origins
    if allowed:
        return {
            'Access-Control-Allow-Origin':  origin,
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Max-Age':       '86400',
        }
    log.warning(f"CORS blocked origin: {repr(origin)}")
    return {}


@booking_bp.route('/booking/submit', methods=['OPTIONS', 'POST'],
                  strict_slashes=False)
def submit():
    origin = request.headers.get('Origin', '')
    cors   = _cors_headers(origin)

    # Preflight — respond immediately, never redirect
    if request.method == 'OPTIONS':
        resp = make_response('', 204)
        for k, v in cors.items():
            resp.headers[k] = v
        return resp

    def error(msg, code=400):
        resp = make_response(jsonify({'ok': False, 'message': msg}), code)
        for k, v in cors.items():
            resp.headers[k] = v
        return resp

    def ok(msg='Booking received.'):
        resp = make_response(jsonify({'ok': True, 'message': msg}), 200)
        for k, v in cors.items():
            resp.headers[k] = v
        return resp

    # Parse JSON body
    data = request.get_json(silent=True) or {}

    # Secret check
    if data.get('_secret', '') != TFB_SECRET:
        log.warning(f"Booking secret mismatch from {request.remote_addr}")
        return error('Unauthorised.', 403)

    # Extract and validate fields
    name             = (data.get('name') or '').strip()
    email            = (data.get('email') or '').strip().lower()
    phone            = (data.get('phone') or '').strip()
    suburb           = (data.get('suburb') or '').strip()
    services_raw     = data.get('services') or ''
    bike_description = (data.get('bike_description') or '').strip()
    message          = (data.get('message') or '').strip()

    # Coerce services to a string
    if isinstance(services_raw, list):
        services = ', '.join(s for s in services_raw if s)
    else:
        services = str(services_raw).strip()

    if not name or not email or not services:
        return error('Please fill in all required fields.')
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        return error('Please enter a valid email address.')

    from models import get_db
    from routes.jobs import upsert_customer, generate_reference, recalc_job_totals

    try:
        with get_db() as conn:
            # Find or create customer
            customer_id, _ = upsert_customer(
                conn, name, email, phone, suburb, '')

            # Look up region from suburb, fall back to first region
            row = conn.execute(
                "SELECT region_id FROM suburbs WHERE LOWER(name)=LOWER(?)",
                (suburb,)).fetchone()
            if row:
                region_id = row['region_id']
            else:
                region_id = conn.execute(
                    "SELECT id FROM regions ORDER BY id LIMIT 1"
                ).fetchone()['id']

            # Create the booking job
            ref = generate_reference('booking', conn)
            conn.execute("""
                INSERT INTO jobs (
                    reference, job_type, customer_id, customer_name,
                    customer_email, customer_phone, suburb, address,
                    description, bike_description, service_types,
                    region_id, tax_inclusive, status, notes)
                VALUES (?, 'booking', ?, ?, ?, ?, ?, '', ?, ?, ?, ?, 1,
                        'pending', ?)
            """, (ref, customer_id, name, email, phone, suburb,
                  message, bike_description, services, region_id,
                  "Submitted via booking form"))
            conn.commit()
            job_id = conn.execute(
                "SELECT id FROM jobs WHERE reference=?", (ref,)).fetchone()['id']

            # Add default job parts for each service type (same as email poller)
            if services:
                for stype in [s.strip() for s in services.split(',') if s.strip()]:
                    part = conn.execute(
                        """SELECT id, name, part_number, unit_cost FROM parts
                           WHERE LOWER(name)=LOWER(?) AND active=1 LIMIT 1""",
                        (stype,)).fetchone()
                    if part:
                        conn.execute(
                            """INSERT INTO job_parts
                               (job_id, part_id, description, part_number, quantity, unit_cost)
                               VALUES (?, ?, ?, ?, 1, ?)""",
                            (job_id, part['id'], part['name'],
                             part['part_number'] or '', part['unit_cost']))
                conn.commit()
                recalc_job_totals(conn, job_id)

            # Create an email_imports row so the submission appears in
            # the email log and thread, and triggers the unread indicator
            import uuid as _uuid
            synthetic_id = (
                f"<booking-{ref}-{_uuid.uuid4().hex[:8]}@{__import__('models').get_settings().get('app_url','localhost').replace('https://','').replace('http://','').split('/')[0]}>")
            body_for_log = _notification_text(
                name, email, phone, suburb, services, bike_description, message)
            conn.execute("""
                INSERT INTO email_imports
                    (message_id, thread_id, subject, sender, body,
                     imported_at, received_at, job_id, status, read)
                VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'),
                        ?, 'ok', 1)
            """, (synthetic_id, synthetic_id,
                  f"{name} - {suburb}" if suburb else f"Booking - {name}",
                  email, body_for_log, job_id))
            conn.commit()

        log.info(f"Booking form: created job {ref} for {name} <{email}>")

    except Exception as e:
        log.error(f"Booking form job creation failed: {e}")
        return error('An error occurred. Please call 0403 225 135.', 500)

    # Job is created directly — send notification to info@ so it appears
    # in Gmail Open Bookings label for visibility. The poller skips
    # self-sent emails so it won't try to create a duplicate job.
    log.info(f"Booking form: job {ref} created for {name} <{email}> — {services}")
    try:
        from email_sender import send_reply
        notify_body = _notification_text(
            name, email, phone, suburb, services, bike_description, message)
        notify_subject = f"[Booking] {name} - {suburb}" if suburb else f"[Booking] {name}"
        notify_msg_id = send_reply(
            to_address=NOTIFY_TO,
            subject=notify_subject,
            body_text=notify_body,
        )
        # Update the synthetic email_imports row with the real message_id
        # so replies to the notification email chain back to this job
        if notify_msg_id:
            with get_db() as conn:
                conn.execute(
                    "UPDATE email_imports SET message_id=?, thread_id=? "
                    "WHERE job_id=? AND status='ok' ORDER BY id DESC LIMIT 1",
                    (notify_msg_id, notify_msg_id, job_id))
                conn.commit()
        log.info(f"Booking notification sent to {NOTIFY_TO} for {ref}")
    except Exception as e:
        log.error(f"Booking notification send failed for {ref}: {e}")

    # Send acknowledgement to customer and store in email_replies for thread
    try:
        from email_sender import send_reply, is_sendable_email
        from models import get_settings as _gs
        _s = _gs()
        biz = _s.get('business_name', 'us')
        if not is_sendable_email(email):
            log.warning(f"Booking ack skipped — no valid email for {ref}")
        else:
            ack_html = _acknowledgement_html(name, services, _s)
            ack_text = _acknowledgement_text(name, services, _s)
            ack_subject = f"Your booking with {biz}"
            ack_msg_id  = send_reply(
                to_address=email,
                subject=ack_subject,
                body_text=ack_text,
                body_html=ack_html,
                extra_headers={'Auto-Submitted': 'auto-replied',
                               'X-TFB-Type': 'booking-ack'},
            )
            log.info(f"Booking acknowledgement sent to {email}")
    except Exception as e:
        log.error(f"Booking acknowledgement send failed for {ref}: {e}")
        # Don't fail — job is created

    return ok('Booking received.')


# ── Email templates ──────────────────────────────────────────────────────────

def _notification_text(name, email, phone, suburb, services,
                        bike_description, message):
    from datetime import datetime
    date_str = datetime.now().strftime('%A %-d %B %Y, %-I:%M %p')
    phone_display = phone or 'Not provided'
    bike_display  = bike_description or 'Not provided'
    msg_display   = message or 'None'
    return (
        f"New booking request received on {date_str}\n\n"
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Phone: {phone_display}\n"
        f"Suburb: {suburb}\n\n"
        f"Service Type:\n{services}\n\n"
        f"Bike Description:\n{bike_display}\n\n"
        f"Message:\n{msg_display}\n"
    )


def _acknowledgement_text(name, services, settings=None):
    if settings is None:
        from models import get_settings
        settings = get_settings()
    first   = name.split()[0] if name else name
    biz     = settings.get('business_name', 'us')
    phone   = settings.get('business_phone', '')
    email   = settings.get('business_email', '')
    website = settings.get('business_website', '')
    tagline = settings.get('business_tagline', '')
    lines = [
        f"Hi {first},\n",
        f"Thanks for your booking request! We've received it and will be "
        f"in touch shortly to confirm a time.\n",
        f"What you requested:\n{services}\n",
        "Need to reach us?",
    ]
    if phone:   lines.append(f"Phone: {phone}")
    if email:   lines.append(f"Email: {email}")
    lines.append("")
    footer = biz
    if tagline: footer += f" — {tagline}"
    lines.append(footer)
    if website: lines.append(website)
    return "\n".join(lines) + "\n"


def _acknowledgement_html(name, services, settings=None):
    if settings is None:
        from models import get_settings
        settings = get_settings()
    import html as _html
    from datetime import date as _date
    first   = _html.escape(name.split()[0] if name else name)
    svc     = _html.escape(services)
    year    = _date.today().year
    biz     = _html.escape(settings.get('business_name', 'us'))
    tagline = _html.escape(settings.get('business_tagline', ''))
    phone   = _html.escape(settings.get('business_phone', ''))
    email_a = _html.escape(settings.get('business_email', ''))
    website = _html.escape(settings.get('business_website', ''))
    insta   = _html.escape(settings.get('business_instagram', ''))
    phone_row = (f"""<table cellpadding="0" cellspacing="0" style="margin-bottom:10px;">
    <tr><td style="font-size:18px;padding-right:12px;vertical-align:top;padding-top:2px;">📞</td>
    <td><p style="margin:0;font-size:13px;color:#888;">Phone</p>
    <a href="tel:{phone.replace(' ','')}" style="font-size:15px;font-weight:700;color:#2d6a35;text-decoration:none;">{phone}</a></td></tr>
  </table>""") if phone else ''
    email_row = (f"""<table cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
    <tr><td style="font-size:18px;padding-right:12px;vertical-align:top;padding-top:2px;">✉️</td>
    <td><p style="margin:0;font-size:13px;color:#888;">Email</p>
    <a href="mailto:{email_a}" style="font-size:15px;font-weight:700;color:#2d6a35;text-decoration:none;">{email_a}</a></td></tr>
  </table>""") if email_a else ''
    website_btn = (f"""<div style="text-align:center;">
    <a href="{website}" style="display:inline-block;background:#e8a020;color:#0f1710;padding:14px 36px;border-radius:6px;font-weight:700;font-size:14px;letter-spacing:.08em;text-transform:uppercase;text-decoration:none;">Visit Our Website</a>
  </div>""") if website else ''
    insta_row = (f'<a href="{insta}" style="color:#e8a020;text-decoration:none;font-size:13px;margin:0 8px;">Instagram</a>') if insta else ''

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f4f0;font-family:Helvetica Neue,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f0;padding:32px 0;">
<tr><td align="center"><table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
<tr><td style="background:#0f1710;border-radius:10px 10px 0 0;padding:32px 36px;text-align:center;">
  <p style="margin:0;font-size:26px;font-weight:900;color:#f5f7f2;">🚲 {biz}</p>
  {f'<p style="margin:8px 0 0;font-size:13px;color:#8fa88a;letter-spacing:.15em;text-transform:uppercase;">{tagline}</p>' if tagline else ''}
</td></tr>
<tr><td style="background:linear-gradient(90deg,#2d6a35,#3d8f47);height:4px;"></td></tr>
<tr><td style="background:#fff;padding:40px 36px;">
  <p style="margin:0 0 20px;font-size:22px;font-weight:700;color:#0f1710;">Thanks, {first}! 👋</p>
  <p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.7;">We've received your service booking request and will be in touch shortly to confirm a time that works for you.</p>
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f9f5;border:1px solid #d0e4d0;border-radius:8px;margin:24px 0;overflow:hidden;">
    <tr><td style="padding:16px 20px;border-bottom:1px solid #d0e4d0;"><p style="margin:0;font-size:11px;font-weight:700;color:#2d6a35;letter-spacing:.15em;text-transform:uppercase;">What You Requested</p></td></tr>
    <tr><td style="padding:16px 20px;"><p style="margin:0;font-size:14px;color:#1a1a1a;line-height:1.6;">{svc}</p></td></tr>
  </table>
  <p style="margin:0 0 16px;font-size:15px;color:#444;line-height:1.7;">If you need to reach us in the meantime:</p>
  {phone_row}
  {email_row}
  {website_btn}
</td></tr>
<tr><td style="background:#0f1710;border-radius:0 0 10px 10px;padding:24px 36px;">
  {f'<p style="margin:0 0 8px;text-align:center;font-size:13px;color:#8fa88a;">Follow us for updates</p><p style="margin:0;text-align:center;">{insta_row}</p>' if insta else ''}
  <p style="margin:16px 0 0;text-align:center;font-size:11px;color:#4a5e4b;">
    © {year} {biz}
    {f'&nbsp;·&nbsp;<a href="{website}/contact-us/" style="color:#4a5e4b;">Terms &amp; Conditions</a>' if website else ''}
  </p>
</td></tr>
</table></td></tr></table>
</body></html>"""




