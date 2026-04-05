from flask import Blueprint, render_template, jsonify, request
from models import get_db

calendar_bp = Blueprint('calendar', __name__)

STATUS_COLORS = {
    'pending':     '#f59e0b',
    'scheduled':   '#3b82f6',
    'in_progress': '#8b5cf6',
    'complete':    '#10b981',
    'invoiced':    '#6b7280',
}


@calendar_bp.route('/calendar')
def index():
    return render_template('calendar/index.html')


@calendar_bp.route('/calendar/events')
def events():
    with get_db() as conn:
        jobs = conn.execute("""
            SELECT j.id, j.reference, j.customer_name,
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
            'color':   STATUS_COLORS.get(job['status'], '#3b82f6'),
            'end':     end,
            'url':     f"/jobs/{job['id']}",
            'extendedProps': {
                'region':  job['region_name'],
                'status':  job['status'],
                'address': location,
                'time':    job['scheduled_time'] or '',
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
