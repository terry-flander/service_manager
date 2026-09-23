"""
routes/workshop_booking.py

Public endpoints for the Pista Bikes workshop booking form.
No authentication required — protected by PISTA_BOOKING_SECRET.

Endpoints:
  GET  /workshop/available-dates   → JSON list of open dates with capacity
  GET  /workshop/date-info         → single date capacity
  POST /workshop/request           → create workshop_booking job + send ack
"""
import os
import re
import logging
import secrets
from datetime import date as _date, datetime as _dt
from flask import Blueprint, request, jsonify, make_response

workshop_booking_bp = Blueprint('workshop_booking', __name__)
log = logging.getLogger('app')


def _secret():
    """Read Pista booking secret from settings, fall back to env."""
    try:
        from models import get_settings
        s = get_settings().get('pista_booking_secret', '').strip()
        if s and s != 'change-me':
            return s
    except Exception:
        pass
    return os.environ.get('PISTA_BOOKING_SECRET', 'change-me')


def _cors(origin):
    """Return CORS headers if origin is permitted."""
    try:
        from models import get_settings
        s = get_settings()
        allowed = set()
        for o in (s.get('pista_cors_origins', '') or '').split(','):
            o = o.strip()
            if o:
                allowed.add(o)
        app_url = (s.get('app_url', '') or '').rstrip('/')
        if app_url:
            allowed.add(app_url)
    except Exception:
        allowed = set()

    if origin in allowed or not allowed:  # allow all if not configured
        return {
            'Access-Control-Allow-Origin':  origin or '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        }
    return {}


def _make_response(data, status=200, origin=''):
    resp = make_response(jsonify(data), status)
    for k, v in _cors(origin).items():
        resp.headers[k] = v
    return resp


# ── CORS preflight ────────────────────────────────────────────────────────────
@workshop_booking_bp.route('/workshop/<path:p>', methods=['OPTIONS'])
def workshop_preflight(p):
    origin = request.headers.get('Origin', '')
    resp = make_response('', 204)
    for k, v in _cors(origin).items():
        resp.headers[k] = v
    return resp


# ── Available dates ───────────────────────────────────────────────────────────
@workshop_booking_bp.route('/workshop/available-dates')
def workshop_available_dates():
    from models import get_workshop_available_dates
    origin  = request.headers.get('Origin', '')
    from_dt = request.args.get('from')
    weeks   = min(int(request.args.get('weeks', 8)), 16)
    try:
        dates = get_workshop_available_dates(from_dt, weeks=weeks)
    except Exception as e:
        log.error(f'workshop_available_dates error: {e}')
        return _make_response({'error': str(e)}, 500, origin)
    return _make_response(dates, 200, origin)


# ── Single date info ──────────────────────────────────────────────────────────
@workshop_booking_bp.route('/workshop/date-info')
def workshop_date_info():
    from models import get_workshop_capacity
    origin = request.headers.get('Origin', '')
    d      = request.args.get('date', '')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', d):
        return _make_response({'error': 'date required (YYYY-MM-DD)'}, 400, origin)
    cap = get_workshop_capacity(d)
    return _make_response(cap, 200, origin)


# ── Submit booking request ────────────────────────────────────────────────────
@workshop_booking_bp.route('/workshop/request', methods=['POST'])
def workshop_request():
    from models import get_db, get_workshop_capacity, get_settings
    from email_sender import send_reply, is_sendable_email
    origin = request.headers.get('Origin', '')
    data   = request.get_json(silent=True) or {}

    # Secret check
    if data.get('_secret', '') != _secret():
        log.warning(f'Workshop booking secret mismatch from {request.remote_addr}')
        return _make_response({'error': 'Forbidden'}, 403, origin)

    # Validate required fields
    name     = (data.get('name') or '').strip()
    email    = (data.get('email') or '').strip()
    phone    = (data.get('phone') or '').strip()
    req_date = (data.get('requested_date') or '').strip()
    bike     = (data.get('bike_description') or '').strip()
    desc     = (data.get('description') or '').strip()

    errors = []
    if not name:             errors.append('name required')
    if not email:            errors.append('email required')
    if not phone:            errors.append('phone required')
    if not is_sendable_email(email): errors.append('valid email required')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', req_date):
        errors.append('requested_date required (YYYY-MM-DD)')
    if errors:
        return _make_response({'error': ', '.join(errors)}, 400, origin)

    with get_db() as conn:
        # Check capacity
        cap = get_workshop_capacity(req_date, conn=conn)
        if not cap['is_open']:
            return _make_response({'error': 'Sorry, we are not open on that date.'}, 409, origin)
        if cap['remaining'] <= 0:
            return _make_response({'error': 'Sorry, that date is fully booked.'}, 409, origin)

        settings = get_settings(conn)

        # Get or create customer
        cust = conn.execute(
            "SELECT id FROM customers WHERE email=?", (email,)).fetchone()
        if cust:
            cust_id = cust['id']
            conn.execute(
                "UPDATE customers SET name=?, phone=? WHERE id=?",
                (name, phone, cust_id))
        else:
            conn.execute(
                "INSERT INTO customers (name, email, phone, suburb, address) VALUES (?,?,?,?,?)",
                (name, email, phone, '', ''))
            cust_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Generate reference WB-NNNN
        prefix = 'WB'
        try:
            from routes.jobs import _get_job_types_for_prefix
        except ImportError:
            pass
        # Find next reference
        last = conn.execute(
            "SELECT reference FROM jobs WHERE reference LIKE ? ORDER BY id DESC LIMIT 1",
            (f'{prefix}-%',)).fetchone()
        if last:
            try:
                num = int(last['reference'].split('-')[1]) + 1
            except Exception:
                num = 1
        else:
            num = 1
        ref = f'{prefix}-{num:04d}'

        # Portal token
        portal_token = secrets.token_urlsafe(24)

        conn.execute("""
            INSERT INTO jobs
            (reference, customer_id, customer_name, customer_email, customer_phone,
             job_type, status, scheduled_date, bike_description, description,
             region_id, tax_inclusive, add_to_calendar, portal_token,
             web_source, web_ref)
            VALUES (?,?,?,?,?,?,?,?,?,?,1,1,0,?,?,?)
        """, (ref, cust_id, name, email, phone,
              'workshop_booking', 'pending', req_date, bike, desc,
              portal_token, 'pista_booking', ''))
        job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # ── Synthetic email_imports row — makes booking visible in email list ──
        import uuid as _uuid
        _app_url = settings.get('app_url','localhost').replace('https://','').replace('http://','').split('/')[0]
        synthetic_id = f"<wb-{ref}-{_uuid.uuid4().hex[:8]}@{_app_url}>"
        body_for_log = (
            f"Workshop Booking Request\n\n"
            f"Name:  {name}\n"
            f"Email: {email}\n"
            f"Phone: {phone}\n"
            f"Date:  {req_date}\n"
            f"Bike:  {bike or 'Not specified'}\n\n"
            f"{desc or ''}"
        ).strip()
        conn.execute("""
            INSERT INTO email_imports
                (message_id, thread_id, subject, sender, body,
                 imported_at, received_at, job_id, status, read)
            VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?, 'ok', 1)
        """, (synthetic_id, synthetic_id,
              f"Workshop Booking - {name} - {req_date}",
              email, body_for_log, job_id))
        conn.commit()

        log.info(f'Workshop booking created: {ref} for {email} on {req_date}')

        # ── Send acknowledgement to customer ─────────────────────────────────
        try:
            biz       = settings.get('business_name', 'Pista Bikes')
            biz_phone = settings.get('business_phone', '')
            biz_email_addr = settings.get('business_email', '')
            first     = name.split()[0]

            try:
                nice_date = _date.fromisoformat(req_date).strftime('%-d %B %Y')
            except Exception:
                nice_date = req_date

            subject = f'Workshop Booking Request Received - {ref}'
            text    = (
                f'Hi {first},\n\n'
                f'Thanks for your workshop booking request at {biz}!\n\n'
                f'We have received your request for {nice_date} and will '
                f'confirm your appointment shortly.\n\n'
                f'Reference: {ref}\n'
                f'Bike: {bike or "Not specified"}\n\n'
                f'Need to reach us?\n'
                + (f'Phone: {biz_phone}\n' if biz_phone else '')
                + (f'Email: {biz_email_addr}\n' if biz_email_addr else '')
                + f'\n{biz}\n'
            )
            html = (
                '<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;color:#222;">'
                f'<p>Hi {first},</p>'
                f'<p>Thanks for your workshop booking request at <strong>{biz}</strong>!</p>'
                f'<p>We have received your request for <strong>{nice_date}</strong> and will confirm your appointment shortly.</p>'
                '<table style="border:1px solid #ddd;border-radius:6px;padding:12px 16px;margin:16px 0;background:#f9f9f9;">'
                f'<tr><td style="padding:4px 12px 4px 0;color:#666;">Reference</td><td><strong>{ref}</strong></td></tr>'
                f'<tr><td style="padding:4px 12px 4px 0;color:#666;">Bike</td><td>{bike or "Not specified"}</td></tr>'
                f'<tr><td style="padding:4px 12px 4px 0;color:#666;">Requested date</td><td>{nice_date}</td></tr>'
                '</table>'
                + (f'<p>Phone: <a href="tel:{biz_phone.replace(" ","")}">{biz_phone}</a></p>' if biz_phone else '')
                + (f'<p>Email: <a href="mailto:{biz_email_addr}">{biz_email_addr}</a></p>' if biz_email_addr else '')
                + f'<p style="color:#666;font-size:0.9em;">{biz}</p>'
                '</body></html>'
            )

            if is_sendable_email(email):
                ack_msg_id = send_reply(
                    to_address=email,
                    subject=subject,
                    body_text=text,
                    body_html=html,
                    extra_headers={'Auto-Submitted': 'auto-replied', 'X-Job-Ref': ref},
                )
                if ack_msg_id:
                    with get_db() as _c2:
                        _c2.execute("""
                            INSERT INTO email_replies
                                (job_id, message_id, in_reply_to, subject,
                                 to_address, body, sent_at)
                            VALUES (?,?,?,?,?,?,datetime('now'))
                        """, (job_id, ack_msg_id, synthetic_id,
                              subject, email, text))
                        _c2.commit()
                log.info(f'Workshop booking ack sent to {email} for {ref}')

        except Exception as e:
            log.error(f'Workshop booking ack email failed for {ref}: {e}')

    return _make_response({
        'ok':        True,
        'reference': ref,
        'date':      req_date,
        'message':   f'Booking request received. Reference: {ref}',
    }, 200, origin)
