"""
gcal_sync.py — Push booking/rental jobs to Google Calendar.

Uses the same OAuth2 refresh token as the Gmail poller (see
gmail_oauth_setup.py / email_poller.py). The token must include the
'https://www.googleapis.com/auth/calendar.events' scope.

Environment variables (reuses existing Gmail OAuth credentials):
  GMAIL_CLIENT_ID
  GMAIL_CLIENT_SECRET
  GMAIL_REFRESH_TOKEN
  GCAL_CALENDAR_ID      Calendar to write to. Defaults to GMAIL_USER
                        (info@theflyingbike.com.au is the primary calendar
                        for that account, so its email IS the calendar ID).
  BASE_URL              Used to build the ServiceDesk link in the event
                        description, e.g. https://3.27.91.236

This module never raises on failure — every public function returns
None / False on error and logs via the standard logging module, so a
Google API hiccup never blocks a job save or delete.
"""
import os
import json
import logging
import urllib.request
import urllib.parse
import urllib.error

log = logging.getLogger('app')  # use Flask app logger so errors appear in docker logs

CALENDAR_API = 'https://www.googleapis.com/calendar/v3'


# ── OAuth2 — reuses Gmail credentials ───────────────────────────────────────

def _get_access_token():
    """Exchange the shared refresh token for a fresh access token."""
    client_id     = os.environ.get('GMAIL_CLIENT_ID', '')
    client_secret = os.environ.get('GMAIL_CLIENT_SECRET', '')
    refresh_token = os.environ.get('GMAIL_REFRESH_TOKEN', '')

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
            tokens = json.loads(resp.read())
            token = tokens.get('access_token')
            if not token:
                log.error(f"OAuth2 response had no access_token: {tokens}")
            return token
    except urllib.error.HTTPError as e:
        log.error(f"Token refresh HTTP {e.code}: {e.read().decode()}")
        return None
    except Exception as e:
        log.error(f"Failed to refresh access token: {e}")
        return None


def _calendar_id():
    return (os.environ.get('GCAL_CALENDAR_ID', '').strip()
            or os.environ.get('GMAIL_USER', '').strip())


def _base_url():
    return os.environ.get('BASE_URL', 'https://3.27.91.236').rstrip('/')


# Google Calendar's fixed event colour palette — hex must match
# templates/jobs/status_colors.html exactly.
GCAL_COLOR_IDS = {
    '#7986cb': '1',   # Lavender
    '#33b679': '2',   # Sage
    '#8e24aa': '3',   # Grape
    '#e67c73': '4',   # Flamingo
    '#f6bf26': '5',   # Banana
    '#f4511e': '6',   # Tangerine
    '#039be5': '7',   # Peacock
    '#616161': '8',   # Graphite
    '#3f51b5': '9',   # Blueberry
    '#0b8043': '10',  # Basil
    '#d50000': '11',  # Tomato
}
# Reverse lookup — Google colorId -> hex, used when displaying events that
# originated in Google Calendar (no local status to look up a colour from).
GCAL_COLOR_ID_TO_HEX = {v: k for k, v in GCAL_COLOR_IDS.items()}
GCAL_DEFAULT_HEX = '#3f51b5'  # Google's default event colour (Blueberry-ish)


def _color_id_for_status(status):
    """Look up the saved hex colour for a job status and map it to
    Google's numeric colorId. Returns None if no match (job keeps
    Google's default colour rather than failing the whole sync)."""
    try:
        from models import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (f'status_color_{status}',)).fetchone()
        if not row:
            return None
        return GCAL_COLOR_IDS.get(row['value'].strip().lower())
    except Exception as e:
        log.error(f"Could not resolve colour for status '{status}': {e}")
        return None


# ── Low-level REST helpers ───────────────────────────────────────────────────

def _request(method, path, body=None):
    """Make an authenticated request to the Calendar API. Returns parsed
    JSON dict on success, or None on any failure (logged)."""
    token = _get_access_token()
    if not token:
        return None

    cal_id = _calendar_id()
    if not cal_id:
        log.error("No calendar ID configured (GCAL_CALENDAR_ID or GMAIL_USER)")
        return None

    url = f"{CALENDAR_API}/calendars/{urllib.parse.quote(cal_id, safe='')}{path}"
    data = json.dumps(body).encode() if body is not None else None

    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode()
        log.error(f"Calendar API {method} {path} -> HTTP {e.code}: {body_txt}")
        return None
    except Exception as e:
        log.error(f"Calendar API {method} {path} -> {e}")
        return None


# ── Event content building ───────────────────────────────────────────────────

def _escape(s):
    return (s or '').strip()


def _build_event_body(job):
    """Build the Google Calendar event payload from a job row (dict-like)."""
    jt = job['job_type']

    first_name = (job['customer_name'] or '').split(' ')[0].strip() or job['customer_name'] or ''
    service_types = job['service_types'] or ''
    summary = f"{first_name} – {service_types}" if service_types else first_name

    job_url      = f"{_base_url()}/jobs/{job['id']}"
    thread_url   = f"{_base_url()}/email/thread/{job['id']}/view"
    address      = job['address'] or ''
    suburb       = job['suburb'] or ''
    dest         = (address + (', ' + suburb if suburb else '')).strip()
    dir_url      = ('https://www.google.com/maps/dir/?api=1&destination=' + urllib.parse.quote_plus(dest)) if dest else ''
    description_lines = [
        f'<a href="{job_url}">ServiceDesk Job</a>',
        f'<a href="{thread_url}">Email Thread</a>',
        (f'<a href="{dir_url}">Get Directions</a>' if dir_url else ''),
        '',
        f"Name: {_escape(job['customer_name'])}",
        f"Email: {_escape(job['customer_email'])}",
        f"Phone: {_escape(job['customer_phone'])}",
        f"Suburb: {_escape(job['suburb'])}",
        f"Service Type: {service_types}",
        '',
        'Message:',
        _escape(job['description']),
    ]
    description = '\n'.join(line for line in description_lines if line is not None)

    event = {
        'summary':     summary,
        'location':    _escape(job['address']),
        'description': description,
        'reminders':   {'useDefault': False, 'overrides': []},
    }

    color_id = _color_id_for_status(job['status'])
    if color_id:
        event['colorId'] = color_id

    if jt == 'booking':
        date = job['scheduled_date']
        start_time = job['scheduled_time']
        end_time = job['end_time']
        if not date:
            return None  # nothing to schedule yet
        if start_time:
            event['start'] = {'dateTime': f"{date}T{start_time}:00", 'timeZone': 'Australia/Melbourne'}
            event['end']   = {'dateTime': f"{date}T{end_time or start_time}:00", 'timeZone': 'Australia/Melbourne'}
        else:
            # No time set — treat as all-day for that single date
            event['start'] = {'date': date}
            event['end']   = {'date': _add_days(date, 1)}
    else:  # rental — single multi-day all-day event spanning Start -> End
        start_date = job['scheduled_date']
        end_date   = job['end_date'] or job['scheduled_date']
        if not start_date:
            return None
        event['start'] = {'date': start_date}
        # Google's end.date is exclusive — add 1 day so the event visually
        # covers through the end date inclusive.
        event['end'] = {'date': _add_days(end_date, 1)}

    return event


def _add_days(date_str, n):
    from datetime import datetime, timedelta
    dt = datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=n)
    return dt.strftime('%Y-%m-%d')


# ── Public API ───────────────────────────────────────────────────────────────

def upsert_calendar_event(job):
    """Create or update the Google Calendar event for a job.

    `job` must support dict-style access to: id, job_type, customer_name,
    customer_email, customer_phone, suburb, service_types, address,
    description, scheduled_date, scheduled_time, end_time, end_date,
    gcal_event_id.

    Returns the event_id on success, or None on failure (including the
    case where the job has no date set yet — nothing to push).
    """
    body = _build_event_body(job)
    if body is None:
        log.warning(f"Job {job['id']}: no scheduled date — skipping calendar push")
        return None

    existing_id = job['gcal_event_id']
    if existing_id:
        result = _request('PATCH', f'/events/{existing_id}', body)
        if result is not None:
            return result.get('id', existing_id)
        # Patch failed — event may have been deleted on the Google side.
        # Fall through to create a fresh one.
        log.warning(f"Job {job['id']}: patch failed for event {existing_id}, creating new event")

    result = _request('POST', '/events', body)
    if result is not None:
        return result.get('id')
    return None


def delete_calendar_event(event_id):
    """Delete a Google Calendar event by ID. Returns True on success
    (including 'already gone' which Google reports as 410/404 — treated
    as success since the desired end state is achieved either way)."""
    if not event_id:
        return True
    token = _get_access_token()
    if not token:
        return False
    cal_id = _calendar_id()
    if not cal_id:
        return False

    url = f"{CALENDAR_API}/calendars/{urllib.parse.quote(cal_id, safe='')}/events/{event_id}"
    req = urllib.request.Request(
        url, method='DELETE',
        headers={'Authorization': f'Bearer {token}'}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return True  # already gone — fine
        log.error(f"Calendar delete -> HTTP {e.code}: {e.read().decode()}")
        return False
    except Exception as e:
        log.error(f"Calendar delete -> {e}")
        return False


def upsert_region_date_event(region_name, date):
    """Create a Banana-coloured, all-day Google Calendar event for a
    region date. Region dates have no editable content beyond their
    date/region, so this only ever creates — never patches. Returns the
    event_id on success, or None on failure."""
    body = {
        'summary': region_name,
        'start': {'date': date},
        'end': {'date': _add_days(date, 1)},
        'colorId': '5',  # Banana — fixed, not looked up from status settings
    }
    result = _request('POST', '/events', body)
    if result is not None:
        return result.get('id')
    return None


def list_calendar_events(time_min, time_max):
    """Fetch all events on the calendar within [time_min, time_max)
    (ISO date or datetime strings). singleEvents=true means Google
    expands any recurring series into individual occurrences for us —
    no RRULE parsing needed on our side.

    Returns a list of dicts: [{id, summary, start, end, all_day,
    description, location, color_hex}], or [] on failure.
    """
    token = _get_access_token()
    if not token:
        return []
    cal_id = _calendar_id()
    if not cal_id:
        return []

    params = urllib.parse.urlencode({
        'timeMin': time_min,
        'timeMax': time_max,
        'singleEvents': 'true',
        'orderBy': 'startTime',
        'maxResults': '250',
    })
    url = (f"{CALENDAR_API}/calendars/{urllib.parse.quote(cal_id, safe='')}"
           f"/events?{params}")
    req = urllib.request.Request(
        url, headers={'Authorization': f'Bearer {token}'}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        log.error(f"Calendar list -> HTTP {e.code}: {e.read().decode()}")
        return []
    except Exception as e:
        log.error(f"Calendar list -> {e}")
        return []

    out = []
    for item in data.get('items', []):
        if item.get('status') == 'cancelled':
            continue
        start_obj = item.get('start', {})
        end_obj   = item.get('end', {})
        all_day   = 'date' in start_obj
        out.append({
            'id':          item.get('id'),
            'summary':     item.get('summary', '(no title)'),
            'start':       start_obj.get('date') or start_obj.get('dateTime'),
            'end':         end_obj.get('date') or end_obj.get('dateTime'),
            'all_day':     all_day,
            'description': item.get('description', ''),
            'location':    item.get('location', ''),
            'color_hex':   GCAL_COLOR_ID_TO_HEX.get(item.get('colorId'), GCAL_DEFAULT_HEX),
        })
    return out


def test_connection():
    """Create then immediately delete a throwaway test event, to verify
    credentials/calendar ID are working. Returns (True, '') on success
    or (False, error_message) on failure."""
    body = {
        'summary': 'ServiceDesk — connection test (safe to ignore)',
        'description': 'This event was created automatically to test the '
                        'Google Calendar connection and will be deleted immediately.',
        'start': {'date': '2099-01-01'},
        'end':   {'date': '2099-01-02'},
    }
    result = _request('POST', '/events', body)
    if result is None:
        return False, 'Could not create test event — check credentials and Calendar ID.'
    event_id = result.get('id')
    if event_id:
        delete_calendar_event(event_id)
    return True, ''
