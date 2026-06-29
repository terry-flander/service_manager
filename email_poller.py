"""
email_poller.py — Polls Gmail via IMAP+OAuth2 for new booking emails.

Uses OAuth2 refresh token (no App Password required).
Run gmail_oauth_setup.py once to generate the refresh token.

Environment variables:
  GMAIL_USER            e.g. info@theflyingbike.com.au
  GMAIL_CLIENT_ID       OAuth2 client ID from Google Cloud Console
  GMAIL_CLIENT_SECRET   OAuth2 client secret
  GMAIL_REFRESH_TOKEN   Long-lived refresh token (from gmail_oauth_setup.py)
  GMAIL_LABEL           Gmail label, default: Booking Email/Open Bookings
  GMAIL_POLL_MINUTES    Poll interval in minutes, default: 5
"""
import imaplib
import email
import email.header
import re
import time
import logging
import threading
import os
import json
import base64
import urllib.request
import urllib.parse
from email.utils import parseaddr

log = logging.getLogger('email_poller')


def _parse_received_date(msg):
    """Parse the email Date: header into an ISO datetime string (local time)."""
    from email.utils import parsedate_to_datetime
    raw = msg.get('Date', '')
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        # Convert to UTC naive then format
        import datetime as _dt
        if dt.tzinfo:
            dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return None

SERVICE_TYPES = [
    'General Service',
    'eBike Service',
    'Tribe/Cargo Bike Service',
    '3 or More Bikes',
    'Other',
]

SERVICE_KEYWORDS = {
    # Matched in priority order — checked top to bottom
    # eBike must come BEFORE cargo/tribe to catch 'e-cargo bike'
    'eBike Service':            ['ebike', 'e-bike', 'electric bike', 'e-cargo',
                                 'ebike', 'e bike', 'ecargo', 'electric'],
    'Tribe/Cargo Bike Service': ['tribe', 'longtail', 'long tail', 'cargo bike',
                                 'longtail', 'bakfiets'],
    '3 or More Bikes':          ['3 or more', '3+ bikes', 'three or more',
                                 '4 bikes', '5 bikes', 'fleet',
                                 '3 bikes', 'three bikes', 'four bikes',
                                 '36 bikes',  # school/fleet
                                 ],
    'General Service':          ['general service', 'service', 'tune', 'repair',
                                 'overhaul', 'brake', 'gear', 'tyre', 'tube',
                                 'chain', 'derailleur', 'assemble', 'setup',
                                 'check'],
}


# ── OAuth2 ────────────────────────────────────────────────────────────────────

def _get_access_token():
    """Exchange refresh token for a fresh access token."""
    client_id     = os.environ.get('GMAIL_CLIENT_ID', '')
    client_secret = os.environ.get('GMAIL_CLIENT_SECRET', '')
    refresh_token = os.environ.get('GMAIL_REFRESH_TOKEN', '')

    log.info(f"OAuth2: client_id={'set (' + client_id[:20] + '...)' if client_id else 'MISSING'}")
    log.info(f"OAuth2: client_secret={'set' if client_secret else 'MISSING'}")
    log.info(f"OAuth2: refresh_token={'set (' + refresh_token[:10] + '...)' if refresh_token else 'MISSING'}")

    if not all([client_id, client_secret, refresh_token]):
        log.error("Missing GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, or GMAIL_REFRESH_TOKEN")
        return None

    data = urllib.parse.urlencode({
        'client_id':     client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type':    'refresh_token',
    }).encode()

    req = urllib.request.Request(
        'https://oauth2.googleapis.com/token',
        data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            tokens = json.loads(body)
            token = tokens.get('access_token')
            log.info(f"OAuth2: access_token={'obtained (' + str(len(token)) + ' chars)' if token else 'MISSING from response'}")
            if not token:
                log.error(f"OAuth2 response had no access_token: {tokens}")
            return token
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error(f"Token refresh HTTP {e.code}: {body}")
        return None
    except Exception as e:
        log.error(f"Failed to refresh access token: {e}")
        return None


def _imap_connect(user, access_token):
    """Connect to Gmail IMAP using XOAUTH2.
    Returns an authenticated imaplib.IMAP4_SSL connection.
    """
    raw  = f"user={user}\x01auth=Bearer {access_token}\x01\x01".encode()
    conn = imaplib.IMAP4_SSL('imap.gmail.com', 993)
    conn.authenticate('XOAUTH2', lambda challenge: raw)
    log.info(f"IMAP authenticated as {user}")
    return conn


def _decode_header(h):
    if not h:
        return ''
    parts = email.header.decode_header(h)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            decoded.append(part)
    return ' '.join(decoded)


def _html_to_text(html):
    """Convert HTML to plain text — strip tags, decode entities, normalise whitespace."""
    import html as _html_mod
    # Remove style and script blocks entirely
    text = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', html,
                  flags=re.DOTALL | re.IGNORECASE)
    # Block elements → newlines
    text = re.sub(r'<(br|p|div|tr|li|h[1-6])[^>]*>', '\n', text,
                  flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode HTML entities (&amp; &nbsp; &#39; etc.)
    text = _html_mod.unescape(text)
    # Collapse runs of blank lines to max two, normalise spaces within lines
    lines = [re.sub(r'[ \t]+', ' ', ln).strip() for ln in text.splitlines()]
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines))
    return result.strip()


def _get_text_body(msg):
    """Extract plain-text body from an email.Message object.

    Preference order:
      1. text/plain parts (concatenated, non-attachment)
      2. text/html parts converted to plain text (fallback)
      3. Non-multipart payload decoded directly

    Returns (body, attachment_names) — attachment_names is a list of
    filenames for any attached parts. The files themselves are never
    saved; this is visibility only. Callers append a bracketed note to
    the *stored* body separately, after any field-parsing on the raw
    body has already happened, so the note never leaks into a parsed
    field like the customer's message.
    """
    plain_parts = []
    html_parts  = []
    attachment_names = []

    if msg.is_multipart():
        for part in msg.walk():
            ct   = (part.get_content_type() or '').lower()
            disp = str(part.get('Content-Disposition', '')).lower()
            if 'attachment' in disp:
                filename = part.get_filename() or f'(unnamed {ct})'
                attachment_names.append(filename)
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or 'utf-8'
            text = payload.decode(charset, errors='replace')
            if ct == 'text/plain':
                plain_parts.append(text)
            elif ct == 'text/html':
                html_parts.append(text)
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            charset = msg.get_content_charset() or 'utf-8'
            text = payload.decode(charset, errors='replace')
            ct = (msg.get_content_type() or '').lower()
            if ct == 'text/html':
                html_parts.append(text)
            else:
                plain_parts.append(text)

    if plain_parts:
        body = '\n'.join(plain_parts).strip()
    elif html_parts:
        body = _html_to_text('\n'.join(html_parts))
    else:
        body = ''

    return body, attachment_names


def _attachment_note(attachment_names):
    """Build the bracketed attachment note to append to a stored body,
    or '' if there were none."""
    if not attachment_names:
        return ''
    count = len(attachment_names)
    label = 'attachment' if count == 1 else 'attachments'
    return f"[{count} {label}: {', '.join(attachment_names)}]"


def _strip_footer(text):
    """Remove contact-form footer and standard email signature separator."""
    text = re.sub(r'\n--\s*\n.*', '', text, flags=re.DOTALL)
    text = re.sub(r'\nThis e-?mail was sent from a contact form.*', '', text,
                  flags=re.DOTALL | re.IGNORECASE)
    return text


def _extract_message(text):
    """Extract a multi-line message body, stopping at the next field label or end of text."""
    all_stops = (r'(?:Name|Email|Phone|Mobile|Suburb|Address|Location|'
                 r'Service Type|Service|Message Body|Message|From)\s*[:\-]')
    for label in ('Message Body', 'Message'):
        pat = re.compile(
            r'(?:' + re.escape(label) + r')\s*[:\-]\s*(.*?)(?=\s*(?:' + all_stops + r')|\s*$)',
            re.IGNORECASE | re.DOTALL
        )
        m = pat.search(text)
        if m:
            val = re.sub(r'<[^>]+>', ' ', m.group(1)).strip()
            if val:
                return val
    return ''


def _extract_field(text, *labels):
    """
    Extract value after a label, stopping at next known label or end of line.
    No DOTALL — each field lives on its own line so suburb can't bleed into message.
    """
    all_stops = (r'(?:Name|Email|Phone|Mobile|Suburb|Address|Location|'
                 r'Service Type|Service|Message Body|Message|From)\s*[:\-]')
    for label in labels:
        pat = re.compile(
            r'(?:' + re.escape(label) + r')\s*[:\-]\s*([^\r\n]+?)(?=\s*(?:' + all_stops + r')|\s*$)',
            re.IGNORECASE | re.MULTILINE
        )
        m = pat.search(text)
        if m:
            val = re.sub(r'<[^>]+>', ' ', m.group(1))
            val = re.sub(r'\s+', ' ', val).strip()
            if val:
                return val
    return ''


def _extract_email(text):
    m = re.search(r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b', text)
    return m.group(1).strip() if m else ''


def _detect_service_types(text):
    import re as _re
    text_lower = text.lower()

    found = []

    # Count "N x " or "N bikes" patterns to detect 3+ bikes
    counts = _re.findall(r'(\d+)\s*(?:x|bikes?)', text_lower)
    if counts and sum(int(c) for c in counts) >= 3:
        found.append('3 or More Bikes')

    # Check keyword lists in order
    for stype, kws in SERVICE_KEYWORDS.items():
        if stype == '3 or More Bikes' and stype in found:
            continue  # already detected above
        if any(kw in text_lower for kw in kws):
            if stype not in found:
                found.append(stype)

    # Remove General Service if more specific types found
    # (keeps it only when truly nothing else matches)
    specific = [f for f in found if f != 'General Service']
    if specific and 'General Service' in found:
        found.remove('General Service')
        # Re-add General Service only if service/tune/repair mentioned
        # alongside specific type (common: "full service on my ebike")
        if any(kw in text_lower for kw in ['service', 'repair', 'tune', 'overhaul']):
            found.insert(0, 'General Service')

    return ', '.join(found) if found else 'General Service'


def _parse_email(msg):
    subject    = _decode_header(msg.get('Subject', ''))
    from_raw   = _decode_header(msg.get('From', ''))
    from_name, from_email = parseaddr(from_raw)
    from_name  = from_name.strip()
    from_email = from_email.strip().lower()

    body, attachment_names = _get_text_body(msg)
    body_norm = re.sub(r'\bMessage Body\s*:', 'Message:', body, flags=re.IGNORECASE)
    body_norm = re.sub(r'\bMobile\s*:', 'Phone:', body_norm, flags=re.IGNORECASE)
    body_norm = _strip_footer(body_norm)

    name    = _extract_field(body_norm, 'Name', 'From') or from_name
    email_  = _extract_field(body_norm, 'Email') or _extract_email(body_norm) or from_email
    phone   = _extract_field(body_norm, 'Phone')
    suburb  = _extract_field(body_norm, 'Suburb', 'Location')
    message = _extract_message(body_norm) or body_norm.strip()[:1000]
    svc_raw = _extract_field(body_norm, 'Service Type', 'Service Types', 'Service')

    if svc_raw:
        # First try exact canonical matches (website form sends these)
        found = [st for st in SERVICE_TYPES if st.lower() in svc_raw.lower()]
        if found:
            service_types = ', '.join(found)
        else:
            # Fall back to keyword detection on the service field + message
            service_types = _detect_service_types(svc_raw + ' ' + message)
    else:
        service_types = _detect_service_types(message)

    phone  = re.sub(r'\D', '', phone)[:10]
    name   = re.sub(r'\s*<[^>]*@[^>]*>', '', name).strip() or from_name
    suburb = re.sub(r'\s*\d{4}\s*$', '', suburb).strip().title()

    if not email_ and phone:
        email_ = f"noemail_{phone}@import.local"

    stored_body = body
    note = _attachment_note(attachment_names)
    if note:
        stored_body = f"{body}\n\n{note}" if body else note

    return {
        'name':          name or 'Unknown',
        'email':         email_.lower(),
        'phone':         phone,
        'suburb':        suburb,
        'message':       message[:1000],
        'body':          stored_body,     # full plain-text body for email_imports
        'service_types': service_types,
        'subject':       subject,
        'from_name':     from_name,
        'from_email':    from_email,
        'received_at':   None,  # filled in by poll loop from msg headers
    }


# ── Job creation ──────────────────────────────────────────────────────────────


def _find_job_for_thread(conn, in_reply_to, references, from_email):
    """
    Return the job_id of the first imported email in this thread, or None.

    Strategy (in order):
      1. Match In-Reply-To against a known message_id in email_imports
      2. Match any References header ID against email_imports
      3. Match from_email against a job's customer_email (most recent job)
    """
    # Check In-Reply-To
    if in_reply_to:
        row = conn.execute(
            "SELECT job_id FROM email_imports WHERE message_id=? AND job_id IS NOT NULL",
            (in_reply_to.strip(),)).fetchone()
        if row:
            return row['job_id']

    # Check References (space-separated list of message IDs)
    if references:
        for ref_id in references.split():
            ref_id = ref_id.strip()
            if ref_id:
                row = conn.execute(
                    "SELECT job_id FROM email_imports "
                    "WHERE message_id=? AND job_id IS NOT NULL",
                    (ref_id,)).fetchone()
                if row:
                    return row['job_id']

    # Fall back: customer email match — return most recent pending job
    if from_email and 'import.local' not in from_email:
        row = conn.execute("""
            SELECT j.id FROM jobs j
            JOIN customers c ON c.id = j.customer_id
            WHERE LOWER(c.email) = LOWER(?)
              AND j.status IN ('pending', 'scheduled', 'in_progress')
            ORDER BY j.id DESC LIMIT 1
        """, (from_email,)).fetchone()
        if row:
            return row['id']

    return None


def _log_thread_email(conn, message_id, thread_id, in_reply_to,
                      subject, sender, body, job_id, received_at=None):
    """Record a follow-up email against an existing job — no new job created."""
    conn.execute("""
        INSERT OR IGNORE INTO email_imports
            (message_id, thread_id, in_reply_to, subject, sender, body,
             imported_at, received_at, job_id, status, read)
        VALUES (?, ?, ?, ?, ?, ?, coalesce(?,datetime('now')),
                ?, ?, 'thread', 1)
    """, (message_id, thread_id, in_reply_to, subject, sender, body[:4000],
            received_at, received_at, job_id))
    conn.commit()
    log.info(f"Logged thread email {message_id[:40]} against job_id={job_id}")


def _already_imported(conn, message_id):
    return conn.execute(
        "SELECT id FROM email_imports WHERE message_id=?",
        (message_id,)).fetchone() is not None


def _create_job(conn, parsed, message_id, thread_id=None, in_reply_to=None):
    import sqlite3 as _sqlite3
    from routes.jobs import upsert_customer, generate_reference

    customer_id, stored_address = upsert_customer(
        conn, parsed['name'], parsed['email'],
        parsed['phone'], parsed['suburb'], ''
    )

    row = conn.execute(
        "SELECT region_id FROM suburbs WHERE LOWER(name)=LOWER(?)",
        (parsed['suburb'],)).fetchone()
    region_id = row['region_id'] if row else \
        conn.execute("SELECT id FROM regions ORDER BY id LIMIT 1").fetchone()['id']

    for attempt in range(5):
        ref = generate_reference('booking', conn)
        try:
            conn.execute("""
                INSERT INTO jobs (
                    reference, job_type, customer_id,
                    customer_name, customer_email, customer_phone,
                    suburb, address, description,
                    service_types, region_id, tax_inclusive,
                    status, notes)
                VALUES (?, 'booking', ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, 1, 'pending', ?)
            """, (ref, customer_id,
                  parsed['name'], parsed['email'], parsed['phone'],
                  parsed['suburb'], stored_address or parsed['suburb'],
                  parsed['message'], parsed['service_types'], region_id,
                  f"Imported from email: {parsed['subject']}"))

            job_id = conn.execute(
                "SELECT id FROM jobs WHERE reference=?", (ref,)).fetchone()['id']
            conn.execute("""
                INSERT INTO email_imports
                    (message_id, thread_id, in_reply_to, subject, sender,
                     body, imported_at, received_at, job_id, status, read)
                VALUES (?, ?, ?, ?, ?, ?, coalesce(?,datetime('now')),
                        ?, ?, 'ok', 1)
            """, (message_id, thread_id, in_reply_to,
                    parsed['subject'], parsed['from_email'],
                    parsed.get('body', parsed['message'])[:8000],
                    parsed.get('received_at'), parsed.get('received_at'),
                    job_id))
            conn.commit()

            # Auto-add a job_part for each service type, same as new_job form
            if parsed['service_types']:
                for stype in [s.strip() for s in parsed['service_types'].split(',') if s.strip()]:
                    part = conn.execute(
                        """SELECT id, name, part_number, unit_cost FROM parts
                           WHERE LOWER(name) = LOWER(?) AND active = 1 LIMIT 1""",
                        (stype,)).fetchone()
                    if part:
                        conn.execute(
                            """INSERT INTO job_parts
                               (job_id, part_id, description, part_number, quantity, unit_cost)
                               VALUES (?, ?, ?, ?, 1, ?)""",
                            (job_id, part['id'], part['name'],
                             part['part_number'] or '', part['unit_cost']))
                conn.commit()
                from routes.jobs import recalc_job_totals
                recalc_job_totals(conn, job_id)

            log.info(f"Created job {ref} from email {message_id[:40]}")
            return job_id

        except _sqlite3.IntegrityError as e:
            if 'reference' in str(e) and attempt < 4:
                conn.rollback()
                continue
            raise
    return None



def _poll_inbox_replies(imap, app):
    """
    Search INBOX for all unread messages and attempt to match each
    to an existing job thread via headers or subject.
    Only marks as read and logs if a job match is found.
    Returns count of messages processed.
    """
    status, _ = imap.select('INBOX')
    if status != 'OK':
        log.warning("Could not select INBOX for reply scanning")
        return 0

    # Fetch ALL unread — let thread-matching logic decide what belongs to us
    status, data = imap.search(None, 'UNSEEN')
    if status != 'OK':
        log.warning("INBOX: UNSEEN search failed")
        return 0

    ids = data[0].split() if data[0] else []
    log.info(f"INBOX: {len(ids)} UNSEEN message(s)")

    if not ids:
        # Gmail marks messages read when opened elsewhere.
        # Fall back to SINCE 2 days — _already_imported() skips anything seen before.
        from datetime import datetime, timedelta
        since = (datetime.now() - timedelta(days=2)).strftime('%d-%b-%Y')
        status2, data2 = imap.search(None, f'SINCE {since}')
        if status2 == 'OK' and data2[0]:
            ids = data2[0].split()
            log.info(f"INBOX: UNSEEN=0, falling back to SINCE {since} "
                     f"({len(ids)} message(s) to check)")
        else:
            log.info("INBOX: no messages to check")
            return 0

    processed = 0

    for num in ids:
        status, msg_data = imap.fetch(num, '(RFC822)')
        if status != 'OK':
            continue
        msg = email.message_from_bytes(msg_data[0][1])

        message_id  = (msg.get('Message-ID') or '').strip() or \
                      f"{msg.get('Date','')}_{msg.get('From','')}"
        in_reply_to = (msg.get('In-Reply-To') or '').strip()
        references  = (msg.get('References')  or '').strip()
        received_at = _parse_received_date(msg)
        subject     = _decode_header(msg.get('Subject', ''))
        from_raw    = _decode_header(msg.get('From', ''))
        from_name, from_email = parseaddr(from_raw)
        from_email  = from_email.strip().lower()

        with app.app_context():
            from models import get_db
            with get_db() as db_conn:
                if _already_imported(db_conn, message_id):
                    log.debug(f"INBOX: already imported {message_id[:40]}")
                    continue

                body, attachment_names = _get_text_body(msg)
                note = _attachment_note(attachment_names)
                if note:
                    body = f"{body}\n\n{note}" if body else note
                thread_id = in_reply_to or message_id

                # Strategy 1: thread headers (In-Reply-To / References)
                existing_job_id = _find_job_for_thread(
                    db_conn, in_reply_to, references, from_email)

                # Strategy 2: strip Re:/Fwd: prefixes, match bare subject
                if not existing_job_id and subject:
                    base_subj = subject
                    for prefix in ('re:', 'fwd:', 'fw:'):
                        while base_subj.lower().startswith(prefix):
                            base_subj = base_subj[len(prefix):].strip()
                    if base_subj:
                        row = db_conn.execute("""
                            SELECT ei.job_id FROM email_imports ei
                            WHERE ei.job_id IS NOT NULL
                              AND ei.status = 'ok'
                              AND LOWER(TRIM(REPLACE(REPLACE(REPLACE(
                                  ei.subject,'Re: ',''),'Fwd: ',''),'FW: ','')))
                                  = LOWER(?)
                            ORDER BY ei.imported_at DESC LIMIT 1
                        """, (base_subj,)).fetchone()
                        if row:
                            existing_job_id = row['job_id']
                            log.info(f"INBOX: subject match '{base_subj[:40]}' "
                                     f"-> job_id={existing_job_id}")

                if existing_job_id:
                    _log_thread_email(
                        db_conn, message_id, thread_id, in_reply_to,
                        subject, from_email, body, existing_job_id, received_at)
                    processed += 1
                    log.info(f"INBOX reply logged for job {existing_job_id} "
                             f"from {from_email}")
                    # Mark read only when matched — leave others unread
                    imap.store(num, '+FLAGS', '\\Seen')
                else:
                    log.debug(f"INBOX: no job match for '{subject[:40]}' "
                              f"from {from_email} — leaving unread")

    return processed

# ── IMAP polling ──────────────────────────────────────────────────────────────

def poll_once(app, force=False):
    user  = os.environ.get('GMAIL_USER', '')
    label = os.environ.get('GMAIL_LABEL', 'Booking Email/Open Bookings')

    if not user or not os.environ.get('GMAIL_REFRESH_TOKEN'):
        log.warning("GMAIL credentials not configured — skipping poll")
        return 0

    # Check if polling is enabled in settings (skipped when force=True)
    if not force:
        with app.app_context():
            from models import get_db
            with get_db() as _conn:
                row = _conn.execute(
                    "SELECT value FROM settings WHERE key='email_polling'"
                ).fetchone()
                if row and row['value'] == 'off':
                    log.info("Email polling is disabled — skipping")
                    return 0

    access_token = _get_access_token()
    if not access_token:
        return 0

    imported = 0
    try:
        imap = _imap_connect(user, access_token)

        status, _ = imap.select(f'"{label}"')
        if status != 'OK':
            log.error(f"Could not select label '{label}'")
            imap.logout()
            return 0

        status, data = imap.search(None, 'UNSEEN')
        if status == 'OK' and data[0]:
            ids = data[0].split()
            log.info(f"IMAP connected as {user}, label='{label}'")
            log.info(f"Found {len(ids)} unread message(s) in '{label}'")

            for num in ids:
                status, msg_data = imap.fetch(num, '(RFC822)')
                if status != 'OK':
                    continue
                msg = email.message_from_bytes(msg_data[0][1])

                message_id = msg.get('Message-ID', '').strip() or \
                             f"{msg.get('Date','')}_{msg.get('From','')}"

                # Extract thread-tracking headers and date
                in_reply_to  = (msg.get('In-Reply-To') or '').strip()
                references   = (msg.get('References')  or '').strip()
                received_at  = _parse_received_date(msg)
                # Gmail thread ID (X-GM-THRID) — requires FETCH X-GM-THRID
                thread_id   = in_reply_to or message_id

                with app.app_context():
                    from models import get_db
                    with get_db() as db_conn:
                        if _already_imported(db_conn, message_id):
                            continue

                        parsed = _parse_email(msg)
                        parsed['received_at'] = received_at
                        body   = parsed['body']  # already includes attachment note, if any

                        # Is this a reply in an existing thread?
                        existing_job_id = _find_job_for_thread(
                            db_conn, in_reply_to, references, parsed['from_email'])

                        if existing_job_id:
                            # Follow-up email — log it, no new job
                            _log_thread_email(
                                db_conn, message_id, thread_id, in_reply_to,
                                parsed['subject'], parsed['from_email'],
                                body, existing_job_id, received_at)
                            log.info(f"Thread follow-up logged for job {existing_job_id}")
                            imap.store(num, '+FLAGS', '\\Seen')
                        else:
                            # New booking — create job
                            if parsed['name'] == 'Unknown':
                                log.warning(f"Could not parse name from {message_id[:40]}")
                                db_conn.execute("""
                                    INSERT OR IGNORE INTO email_imports
                                        (message_id, thread_id, subject, sender,
                                         body, status)
                                    VALUES (?, ?, ?, ?, ?, 'parse_error')
                                """, (message_id, thread_id, parsed['subject'],
                                        parsed['from_email'], body[:4000]))
                                db_conn.commit()
                                # Leave unread — could not parse, needs manual review
                                imap.store(num, '-FLAGS', '\\Seen')
                                continue
                            if _create_job(db_conn, parsed, message_id,
                                           thread_id, in_reply_to):
                                imported += 1
                                imap.store(num, '+FLAGS', '\\Seen')
                            else:
                                # Job creation failed — restore unread
                                imap.store(num, '-FLAGS', '\\Seen')
        else:
            log.info(f"No unread messages in '{label}'")

        # Also scan INBOX for customer replies
        inbox_count = _poll_inbox_replies(imap, app)
        if inbox_count:
            imported += inbox_count
            log.info(f"INBOX: {inbox_count} reply/replies logged")

        imap.logout()

    except imaplib.IMAP4.error as e:
        log.error(f"IMAP error: {e}")
    except Exception as e:
        log.exception(f"poll_once error: {e}")

    return imported


def start_poller(app):
    interval = int(os.environ.get('GMAIL_POLL_MINUTES', '5')) * 60

    def run():
        log.info(f"Email poller started — interval {interval}s")
        while True:
            try:
                n = poll_once(app)
                if n:
                    log.info(f"Imported {n} job(s) from email")
            except Exception as e:
                log.exception(f"Poller error: {e}")
            time.sleep(interval)

    t = threading.Thread(target=run, daemon=True, name='email-poller')
    t.start()
    return t
