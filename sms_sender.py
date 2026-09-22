"""
sms_sender.py — Twilio SMS sending for ServiceDesk.

Reads credentials from environment:
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_SENDER      — alphanumeric ID (e.g. FlyingBike) or E.164 number

Sender ID can also be set via settings table key 'sms_sender' which overrides env.
"""
import os
import re
import logging

log = logging.getLogger('app')

TWILIO_SID    = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_TOKEN  = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_SENDER = os.environ.get('TWILIO_SENDER', '')


def _normalise_au_number(raw):
    """Normalise an Australian mobile to E.164 (+61XXXXXXXXX). Returns None if invalid."""
    if not raw:
        return None
    digits = re.sub(r'[^\d+]', '', raw.strip())
    if re.match(r'^\+614\d{8}$', digits):   return digits           # already +614XXXXXXXX
    if re.match(r'^\+61\d{9}$',  digits):   return digits           # other +61 (landline ok)
    if re.match(r'^04\d{8}$',    digits):   return '+61' + digits[1:]  # 04XXXXXXXX
    if re.match(r'^4\d{8}$',     digits):   return '+61' + digits      # 4XXXXXXXX
    if re.match(r'^614\d{8}$',   digits):   return '+' + digits        # 614XXXXXXXX no +
    return None


def is_sendable_mobile(raw):
    """Return True if the number can be normalised to E.164."""
    return _normalise_au_number(raw) is not None


def render_sms_body(template_body, job, settings=None):
    """Replace {{variable}} placeholders. Available: name, ref, date, time, total, phone, business."""
    if settings is None:
        from models import get_settings
        settings = get_settings()

    first_name = (job.get('customer_name') or '').split()[0] if job.get('customer_name') else ''
    sched_date = job.get('scheduled_date') or ''
    sched_time = job.get('scheduled_time') or ''

    if sched_date:
        try:
            from datetime import date as _date
            sched_date = _date.fromisoformat(sched_date).strftime('%-d %b %Y')
        except Exception:
            pass

    total = f"{job['total']:.2f}" if job.get('total') else ''

    replacements = {
        'name':     first_name or job.get('customer_name', ''),
        'ref':      job.get('reference', ''),
        'date':     sched_date,
        'time':     sched_time,
        'total':    total,
        'phone':    settings.get('business_phone', ''),
        'business': settings.get('business_name', ''),
    }
    body = template_body
    for key, val in replacements.items():
        body = body.replace('{{' + key + '}}', val or '')
    return body.strip()


def send_sms(to_raw, body, job_id=None, sent_by=None):
    """Send an SMS via Twilio. Returns (success, twilio_sid, error_msg)."""
    if not TWILIO_SID or not TWILIO_TOKEN:
        msg = 'Twilio credentials not configured (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN)'
        log.error(f'SMS: {msg}')
        return False, None, msg

    # Settings DB sender overrides env
    sender = TWILIO_SENDER
    try:
        from models import get_settings
        db_sender = get_settings().get('sms_sender', '').strip()
        if db_sender:
            sender = db_sender
    except Exception:
        pass

    if not sender:
        msg = 'No SMS sender configured — set TWILIO_SENDER in .env or Sender ID in Business Settings'
        log.error(f'SMS: {msg}')
        return False, None, msg

    to_number = _normalise_au_number(to_raw)
    if not to_number:
        msg = f'Cannot normalise phone number: {to_raw!r}'
        log.warning(f'SMS: {msg}')
        _log(job_id, to_raw, body, 'failed', None, msg, sent_by)
        return False, None, msg

    try:
        from twilio.rest import Client
        message = Client(TWILIO_SID, TWILIO_TOKEN).messages.create(
            body=body, from_=sender, to=to_number)
        log.info(f'SMS sent to {to_number} job={job_id} sid={message.sid}')
        _log(job_id, to_number, body, message.status or 'sent', message.sid, None, sent_by)
        return True, message.sid, None
    except Exception as e:
        msg = str(e)
        log.error(f'SMS error to {to_number}: {msg}')
        _log(job_id, to_number, body, 'failed', None, msg, sent_by)
        return False, None, msg


def _log(job_id, to_number, body, status, sid, error_msg, sent_by):
    try:
        from models import get_db
        with get_db() as conn:
            conn.execute("""
                INSERT INTO sms_log (job_id, to_number, body, status, twilio_sid, error_msg, sent_by)
                VALUES (?,?,?,?,?,?,?)
            """, (job_id, to_number, body, status, sid, error_msg, sent_by))
            conn.commit()
    except Exception as e:
        log.error(f'sms_log write failed: {e}')


def get_sms_enabled():
    try:
        from models import get_settings
        return get_settings().get('sms_enabled', '0') == '1'
    except Exception:
        return False
