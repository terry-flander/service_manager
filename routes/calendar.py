from flask import Blueprint, render_template, jsonify, request, session
from models import get_db

calendar_bp = Blueprint('calendar', __name__)

STATUS_COLOR_DEFAULTS = {
    'pending':     '#f59e0b',
    'scheduled':   '#3b82f6',
    'in_progress': '#8b5cf6',
    'complete':    '#10b981',
    'invoiced':    '#6b7280',
    'paid':        '#10b981',
    'void':        '#ef4444',
}

def _get_status_colors(conn):
    colors = dict(STATUS_COLOR_DEFAULTS)
    for s in colors:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (f'status_color_{s}',)).fetchone()
        if row:
            colors[s] = row['value']
    return colors


@calendar_bp.route('/calendar')
def index():
    user_id = session.get('user_id')
    with get_db() as conn:
        cal_view, cal_date = _get_cal_prefs(conn, user_id)
    return render_template('calendar/index.html',
                           cal_view=cal_view, cal_date=cal_date)



def _get_cal_prefs(conn, user_id):
    """Return (view, date) for the user, or defaults."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?",
        (f'cal_prefs_{user_id}',)).fetchone()
    if row:
        import json as _json
        try:
            p = _json.loads(row['value'])
            return p.get('view', 'dayGridMonth'), p.get('date', '')
        except Exception:
            pass
    return 'dayGridMonth', ''


@calendar_bp.route('/calendar/prefs', methods=['POST'])
def save_prefs():
    """Save the user's calendar view and date preference."""
    import json as _json
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'ok': False}), 401
    data = request.get_json()
    view = data.get('view', 'dayGridMonth')
    date = data.get('date', '')
    # Validate view name
    if view not in ('dayGridMonth', 'timeGridWeek', 'timeGridDay'):
        view = 'dayGridMonth'
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f'cal_prefs_{user_id}', _json.dumps({'view': view, 'date': date})))
        conn.commit()
    return jsonify({'ok': True})


@calendar_bp.route('/calendar/events')
def events():
    with get_db() as conn:
        jobs = conn.execute("""
            SELECT j.id, j.reference, j.customer_name, j.customer_phone,
                   j.scheduled_date, j.scheduled_time, j.end_time,
                   j.status, j.address, j.suburb, r.name as region_name
            FROM jobs j JOIN regions r ON j.region_id = r.id
            WHERE j.scheduled_date IS NOT NULL
            AND j.job_type = 'booking'
            AND j.status != 'void'
        """).fetchall()

    # Fetch region_dates — displayed as canary yellow all-day background events
    with get_db() as conn:
        region_dates = conn.execute("""
            SELECT rd.date, rd.status, r.id as region_id, r.name as region_name
            FROM region_dates rd
            JOIN regions r ON rd.region_id = r.id
            ORDER BY rd.date
        """).fetchall()

    with get_db() as conn:
        status_colors = _get_status_colors(conn)
    CANARY = '#FFEF00'
    result = []

    for rd in region_dates:
        result.append({
            'id':      f"rd|{rd['region_id']}|{rd['date']}",
            'title':   rd['region_name'],
            'start':   rd['date'],
            'allDay':  True,
            'color':     CANARY,
            'textColor': '#1a1a1a',
            'extendedProps': {
                'type':   'region_date',
                'status': rd['status'],
                'region': rd['region_name'],
            }
        })
    for job in jobs:
        # Build ISO 8601 start — include time if set, date-only otherwise
        if job['scheduled_time']:
            start   = f"{job['scheduled_date']}T{job['scheduled_time']}:00"
            all_day = False
            end     = f"{job['scheduled_date']}T{job['end_time']}:00" if job['end_time'] else None
        else:
            start   = job['scheduled_date']
            all_day = True
            end     = None

        location = job['address'] or job['suburb'] or ''
        result.append({
            'id':      job['id'],
            'title':   f"{job['reference']} — {job['customer_name']}",
            'start':   start,
            'allDay':  all_day,
            'color':   status_colors.get(job['status'], '#3b82f6'),
            'end':     end,
            'url':     f"/jobs/{job['id']}",
            'extendedProps': {
                'region':  job['region_name'],
                'status':  job['status'],
                'address': location,
                'phone':   job['customer_phone'] or '',
                'time':    job['scheduled_time'] or '',
            }
        })

    # Custom calendar events
    with get_db() as conn:
        cal_events = conn.execute(
            "SELECT * FROM calendar_events ORDER BY date, start_time"
        ).fetchall()
    for ev in cal_events:
        if ev['start_time']:
            start = f"{ev['date']}T{ev['start_time']}:00"
            end   = f"{ev['date']}T{ev['end_time']}:00" if ev['end_time'] else None
            all_day = False
        else:
            start   = ev['date']
            end     = None
            all_day = True
        result.append({
            'id':    f"ce|{ev['id']}",
            'title': ev['title'],
            'start': start,
            'end':   end,
            'allDay': all_day,
            'color': ev['color'] or '#6366f1',
            'extendedProps': {
                'type':        'cal_event',
                'description': ev['description'] or '',
                'address':     ev['address']     or '',
                'event_id':    ev['id'],
            }
        })
    return jsonify(result)


@calendar_bp.route('/calendar/move-job', methods=['POST'])
def move_job():
    """
    Called when a job event is dragged to a new position.
    Payload: { id, date, time }
      date — ISO date string (YYYY-MM-DD)
      time — HH:MM string or null (when dropped onto all-day slot)
    """
    data     = request.get_json()
    job_id   = int(data['id'])
    new_date = data.get('date')
    new_time = data.get('time')     # None when dropped to all-day row
    new_end  = data.get('end_time') # None when all-day

    with get_db() as conn:
        conn.execute(
            "UPDATE jobs SET scheduled_date=?, scheduled_time=?, end_time=? WHERE id=?",
            (new_date, new_time, new_end, job_id))
        conn.commit()
        job = conn.execute(
            "SELECT reference, scheduled_date, scheduled_time, end_time FROM jobs WHERE id=?",
            (job_id,)).fetchone()

    return jsonify({
        'ok':             True,
        'reference':      job['reference'],
        'scheduled_date': job['scheduled_date'],
        'scheduled_time': job['scheduled_time'],
        'end_time':       job['end_time'],
    })


@calendar_bp.route('/calendar/move-region-date', methods=['POST'])
def move_region_date():
    """
    Called when a region_date background event is dragged to a new date.
    Payload: { id, date }   id is the region_dates.id (from rd-{region_id}-{date})
    """
    data     = request.get_json()
    raw_id   = data['id']            # e.g. "rd-3-2026-04-07"
    new_date = data['date']

    # Parse composite id: "rd|region_id|YYYY-MM-DD"
    _, rid, old_date = raw_id.split('|')
    region_id = int(rid)

    with get_db() as conn:
        conn.execute(
            "UPDATE region_dates SET date=? WHERE region_id=? AND date=?",
            (new_date, region_id, old_date))
        conn.commit()

    return jsonify({'ok': True, 'region_id': region_id, 'new_date': new_date})


# ── Calendar event CRUD ───────────────────────────────────────────────────────

@calendar_bp.route('/calendar/events/new', methods=['POST'])
def new_event():
    data = request.get_json()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO calendar_events
                (date, start_time, end_time, title, description, address, color)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get('date', ''),
            data.get('start_time') or None,
            data.get('end_time')   or None,
            data.get('title', 'New Event'),
            data.get('description', ''),
            data.get('address', ''),
            data.get('color', '#6366f1'),
        ))
        ev_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    return jsonify({'ok': True, 'id': ev_id})


@calendar_bp.route('/calendar/events/<int:ev_id>', methods=['GET'])
def get_event(ev_id):
    with get_db() as conn:
        ev = conn.execute(
            "SELECT * FROM calendar_events WHERE id=?", (ev_id,)).fetchone()
    if not ev:
        return jsonify({'error': 'not found'}), 404
    return jsonify(dict(ev))


@calendar_bp.route('/calendar/events/<int:ev_id>', methods=['PUT'])
def update_event(ev_id):
    data = request.get_json()
    with get_db() as conn:
        conn.execute("""
            UPDATE calendar_events
            SET date=?, start_time=?, end_time=?, title=?,
                description=?, address=?, color=?,
                updated_at=datetime('now')
            WHERE id=?
        """, (
            data.get('date', ''),
            data.get('start_time') or None,
            data.get('end_time')   or None,
            data.get('title', ''),
            data.get('description', ''),
            data.get('address', ''),
            data.get('color', '#6366f1'),
            ev_id,
        ))
        conn.commit()
    return jsonify({'ok': True})


@calendar_bp.route('/calendar/events/<int:ev_id>', methods=['DELETE'])
def delete_event(ev_id):
    with get_db() as conn:
        conn.execute("DELETE FROM calendar_events WHERE id=?", (ev_id,))
        conn.commit()
    return jsonify({'ok': True})


@calendar_bp.route('/calendar/move-event', methods=['POST'])
def move_event():
    """Called when a custom calendar event is dragged to new date/time."""
    data     = request.get_json()
    ev_id    = int(data['id'])
    new_date = data.get('date')
    new_start = data.get('start_time')
    new_end   = data.get('end_time')
    with get_db() as conn:
        conn.execute(
            "UPDATE calendar_events SET date=?, start_time=?, end_time=? WHERE id=?",
            (new_date, new_start, new_end, ev_id))
        conn.commit()
    return jsonify({'ok': True})
