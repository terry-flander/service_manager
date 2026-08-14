"""
routes/mechanic.py — Mechanic field view for The Flying Bike ServiceDesk.

A stripped-down, portrait-optimised interface for field mechanics.
Accessible to all logged-in users via the mobile nav toggle.
"""
import secrets
import logging
from datetime import date, timedelta
from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, session)
from models import get_db

mechanic_bp = Blueprint('mechanic', __name__)
log = logging.getLogger('app')

PAGE_SIZE = 20  # jobs per page for endless scroll


def _get_schedule_entries(anchor_date, direction='forward', cursor_date=None,
                          cursor_time=None, cursor_id=None):
    """
    Fetch PAGE_SIZE schedule entries starting from anchor or cursor.
    Returns jobs + calendar events + region dates + gcal events
    as a unified list sorted by date/time.
    direction: 'forward' (future) or 'backward' (past)
    """
    with get_db() as conn:
        # Determine date window
        if cursor_date:
            if direction == 'forward':
                date_filter = f"j.scheduled_date > '{cursor_date}'"
            else:
                date_filter = f"j.scheduled_date < '{cursor_date}'"
        else:
            date_filter = f"j.scheduled_date >= '{anchor_date}'"

        # Jobs
        jobs = conn.execute(f"""
            SELECT j.id, j.reference, j.status, j.job_type,
                   j.customer_name, j.customer_phone,
                   j.address, j.suburb,
                   j.scheduled_date, j.scheduled_time, j.end_time,
                   j.description, j.total,
                   j.customer_email, j.customer_id,
                   j.portal_token
            FROM jobs j
            WHERE j.scheduled_date IS NOT NULL
              AND {date_filter}
              AND j.job_type IN ('booking', 'rental')
              AND j.status != 'lost'
            ORDER BY j.scheduled_date {'ASC' if direction == 'forward' else 'DESC'},
                     j.scheduled_time ASC,
                     j.id ASC
            LIMIT {PAGE_SIZE}
        """).fetchall()

        # Calendar events (custom events) for same date range
        if jobs:
            dates = list({j['scheduled_date'] for j in jobs})
            ph = ','.join('?' * len(dates))
            cal_events = conn.execute(f"""
                SELECT id, date, title, start_time, end_time,
                       description, address, color,
                       CASE WHEN start_time IS NULL THEN 1 ELSE 0 END as all_day
                FROM calendar_events
                WHERE date IN ({ph})
                ORDER BY date, start_time
            """, dates).fetchall()
        else:
            cal_events = []

        # GCal external events
        gcal_events = []
        try:
            gcal_enabled_row = conn.execute(
                "SELECT value FROM settings WHERE key='gcal_enabled'").fetchone()
            if gcal_enabled_row and gcal_enabled_row['value'] == '1' and jobs:
                from gcal_sync import list_calendar_events
                import datetime as _dt
                min_d = min(j['scheduled_date'] for j in jobs)
                max_d = max(j['scheduled_date'] for j in jobs)
                # get known IDs to exclude
                known_ids = set()
                for row in conn.execute(
                        "SELECT gcal_event_id FROM jobs WHERE gcal_event_id IS NOT NULL"):
                    known_ids.add(row['gcal_event_id'])
                for row in conn.execute(
                        "SELECT gcal_event_id FROM region_dates WHERE gcal_event_id IS NOT NULL"):
                    known_ids.add(row['gcal_event_id'])
                time_min = f"{min_d}T00:00:00Z"
                time_max = f"{max_d}T23:59:59Z"
                for gev in list_calendar_events(time_min, time_max):
                    if gev['id'] not in known_ids:
                        gcal_events.append({
                            'date':      (gev.get('start', {}).get('date') or
                                          gev.get('start', {}).get('dateTime', '')[:10]),
                            'title':     gev.get('summary', ''),
                            'all_day':   'date' in gev.get('start', {}),
                            'start_time': gev.get('start', {}).get('dateTime', ''),
                            'description': gev.get('description', ''),
                            'color':     gev.get('colorHex', '#6366f1'),
                            'external':  True,
                        })
        except Exception as _e:
            log.debug(f"Mechanic GCal fetch skipped: {_e}")

    return [dict(j) for j in jobs], \
           [dict(e) for e in cal_events], \
           gcal_events


@mechanic_bp.route('/mechanic/')
def schedule():
    """Main mechanic schedule view."""
    today = date.today().isoformat()
    jobs, cal_events, gcal_events = _get_schedule_entries(today)
    return render_template('mechanic/schedule.html',
                           jobs=jobs,
                           cal_events=cal_events,
                           gcal_events=gcal_events,
                           today=today,
                           page_size=PAGE_SIZE,
                           theme=session.get('theme', 'dark'))


@mechanic_bp.route('/mechanic/schedule-page')
def schedule_page():
    """AJAX: fetch next/previous page of schedule entries."""
    cursor_date = request.args.get('cursor_date', date.today().isoformat())
    direction   = request.args.get('direction', 'forward')
    today       = date.today().isoformat()

    jobs, cal_events, gcal_events = _get_schedule_entries(
        today, direction=direction, cursor_date=cursor_date)

    entries = _build_entry_list(jobs, cal_events, gcal_events)
    return jsonify({
        'entries':   entries,
        'has_more':  len(jobs) == PAGE_SIZE,
        'direction': direction,
    })


def _build_entry_list(jobs, cal_events, gcal_events):
    """Merge all entry types into a sorted list with type tags."""
    entries = []

    for j in jobs:
        entries.append({
            'type':           'job',
            'id':             j['id'],
            'date':           j['scheduled_date'],
            'time':           j['scheduled_time'] or '',
            'end_time':       j['end_time'] or '',
            'status':         j['status'],
            'job_type':       j['job_type'],
            'customer_name':  j['customer_name'],
            'customer_phone': j['customer_phone'] or '',
            'customer_email': j['customer_email'] or '',
            'address':        j['address'] or '',
            'suburb':         j['suburb'] or '',
            'reference':      j['reference'],
            'portal_token':   j['portal_token'] or '',
        })

    for e in cal_events:
        entries.append({
            'type':        'cal_event',
            'date':        e['date'],
            'time':        e['start_time'] or '',
            'end_time':    e['end_time'] or '',
            'title':       e['title'],
            'description': e['description'] or '',
            'address':     e['address'] or '',
            'color':       e['color'] or '#6366f1',
            'all_day':     bool(e['all_day']),
        })

    for g in gcal_events:
        entries.append({
            'type':        'gcal',
            'date':        g['date'],
            'time':        g.get('start_time', ''),
            'title':       g['title'],
            'description': g.get('description', ''),
            'all_day':     g.get('all_day', False),
            'color':       g.get('color', '#6366f1'),
        })

    entries.sort(key=lambda e: (e['date'], e.get('time', '') or ''))
    return entries


@mechanic_bp.route('/mechanic/job/<int:job_id>')
def job_view(job_id):
    """Mechanic job detail view."""
    with get_db() as conn:
        job = conn.execute("""
            SELECT j.*, r.name as region_name
            FROM jobs j
            LEFT JOIN regions r ON r.id = j.region_id
            WHERE j.id = ?
        """, (job_id,)).fetchone()
        if not job:
            return "Job not found", 404

        job_parts = conn.execute(
            "SELECT * FROM job_parts WHERE job_id=? ORDER BY id",
            (job_id,)).fetchall()

        contacts = []
        if job['customer_id']:
            contacts = conn.execute(
                "SELECT name, phone, notes FROM customer_contacts "
                "WHERE customer_id=? ORDER BY name",
                (job['customer_id'],)).fetchall()

        # Ensure portal token exists
        from routes.jobs import _get_or_create_portal_token
        portal_token = _get_or_create_portal_token(conn, job_id)

        # Parts for search
        parts = conn.execute(
            "SELECT id, name, part_number, unit_cost FROM parts "
            "WHERE active=1 ORDER BY name").fetchall()

    return render_template('mechanic/job.html',
                           job=dict(job),
                           job_parts=[dict(p) for p in job_parts],
                           contacts=[dict(c) for c in contacts],
                           parts=[dict(p) for p in parts],
                           portal_token=portal_token,
                           theme=session.get('theme', 'dark'))


@mechanic_bp.route('/mechanic/job/<int:job_id>/part/<int:jp_id>/update', methods=['POST'])
def update_part(job_id, jp_id):
    """Update a job part field — returns JSON."""
    from routes.jobs import recalc_job_totals
    data  = request.get_json() or {}
    field = data.get('field')
    value = data.get('value')
    allowed = {'description', 'quantity', 'unit_cost'}
    if field not in allowed:
        return jsonify({'ok': False, 'error': 'Invalid field'}), 400
    with get_db() as conn:
        if field in ('quantity', 'unit_cost'):
            conn.execute(
                f"UPDATE job_parts SET {field}=? WHERE id=? AND job_id=?",
                (float(value), jp_id, job_id))
        else:
            conn.execute(
                f"UPDATE job_parts SET {field}=? WHERE id=? AND job_id=?",
                (str(value), jp_id, job_id))
        conn.commit()
        recalc_job_totals(conn, job_id)
        job = conn.execute("SELECT total FROM jobs WHERE id=?", (job_id,)).fetchone()
    return jsonify({'ok': True, 'total': job['total'] or 0})


@mechanic_bp.route('/mechanic/job/<int:job_id>/part/<int:jp_id>/delete', methods=['POST'])
def delete_part(job_id, jp_id):
    """Delete a job part — returns JSON."""
    from routes.jobs import recalc_job_totals
    with get_db() as conn:
        conn.execute(
            "DELETE FROM job_parts WHERE id=? AND job_id=?", (jp_id, job_id))
        conn.commit()
        recalc_job_totals(conn, job_id)
        job = conn.execute("SELECT total FROM jobs WHERE id=?", (job_id,)).fetchone()
    return jsonify({'ok': True, 'total': job['total'] or 0})


@mechanic_bp.route('/mechanic/job/<int:job_id>/add-part', methods=['POST'])
def add_part(job_id):
    """Add a part to a job — returns JSON for mechanic view fetch calls."""
    from routes.jobs import recalc_job_totals
    part_id     = request.form.get('part_id', '').strip()
    description = request.form.get('description', '').strip()
    unit_cost   = float(request.form.get('unit_cost') or 0)
    quantity    = float(request.form.get('quantity') or 1)

    with get_db() as conn:
        if part_id:
            part = conn.execute(
                "SELECT * FROM parts WHERE id=?", (int(part_id),)).fetchone()
            if part:
                description = description or part['name']
                unit_cost   = unit_cost or part['unit_cost']
                conn.execute("""
                    INSERT INTO job_parts
                        (job_id, part_id, description, part_number, quantity, unit_cost)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (job_id, part['id'], description,
                      part['part_number'] or '', quantity, unit_cost))
        else:
            if not description:
                return jsonify({'ok': False, 'error': 'Description required'}), 400
            conn.execute("""
                INSERT INTO job_parts
                    (job_id, part_id, description, part_number, quantity, unit_cost)
                VALUES (?, NULL, ?, '', ?, ?)
            """, (job_id, description, quantity, unit_cost))
        conn.commit()
        recalc_job_totals(conn, job_id)
        job = conn.execute("SELECT total FROM jobs WHERE id=?", (job_id,)).fetchone()

    return jsonify({'ok': True, 'total': job['total'] or 0})


@mechanic_bp.route('/mechanic/job/<int:job_id>/notes', methods=['POST'])
def save_notes(job_id):
    """Auto-save internal notes."""
    data  = request.get_json() or {}
    notes = data.get('notes', '')
    with get_db() as conn:
        conn.execute("UPDATE jobs SET notes=? WHERE id=?", (notes, job_id))
        conn.commit()
    return jsonify({'ok': True})


@mechanic_bp.route('/mechanic/job/<int:job_id>/complete', methods=['POST'])
def complete_job(job_id):
    """Mark job complete with payment type, or reopen."""
    data         = request.get_json() or {}
    action       = data.get('action')          # 'reopen' | 'cash' | 'eftpos' | 'nti'
    today        = date.today().isoformat()

    with get_db() as conn:
        job = conn.execute(
            "SELECT status, total FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return jsonify({'ok': False, 'error': 'Not found'}), 404

        if action == 'reopen':
            conn.execute(
                "UPDATE jobs SET status='in_progress' WHERE id=?", (job_id,))

        elif action in ('cash', 'eftpos'):
            payment_type = 'Cash' if action == 'cash' else 'EFTPOS'
            conn.execute("""
                UPDATE jobs
                SET status='paid', payment_type=?, paid_date=?, amount_paid=?
                WHERE id=?
            """, (payment_type, today, job['total'] or 0, job_id))

        elif action == 'nti':
            conn.execute("""
                UPDATE jobs
                SET status='complete', invoice_number='NTI'
                WHERE id=?
            """, (job_id,))

        else:
            return jsonify({'ok': False, 'error': 'Unknown action'}), 400

        conn.commit()
        updated = conn.execute(
            "SELECT status, invoice_number FROM jobs WHERE id=?",
            (job_id,)).fetchone()

    return jsonify({'ok': True, 'status': updated['status'],
                    'invoice_number': updated['invoice_number'] or ''})


@mechanic_bp.route('/mechanic/job/<int:job_id>/thread')
def job_thread(job_id):
    """Read-only email thread view."""
    with get_db() as conn:
        job = conn.execute(
            "SELECT id, reference, customer_name FROM jobs WHERE id=?",
            (job_id,)).fetchone()
        if not job:
            return "Not found", 404
        msgs = conn.execute("""
            SELECT 'inbound' as direction, imported_at as ts,
                   sender as from_addr, subject, body
            FROM email_imports WHERE job_id=?
            UNION ALL
            SELECT 'outbound' as direction, sent_at as ts,
                   to_address as from_addr, subject, body
            FROM email_replies WHERE job_id=?
            ORDER BY ts ASC
        """, (job_id, job_id)).fetchall()
    return render_template('mechanic/thread.html',
                           job=dict(job),
                           messages=[dict(m) for m in msgs],
                           theme=session.get('theme', 'dark'))


@mechanic_bp.route('/mechanic/toggle', methods=['POST'])
def toggle_mode():
    """Toggle mechanic mode on/off in session."""
    currently_on = session.get('mechanic_mode', False)
    session['mechanic_mode'] = not currently_on
    if session['mechanic_mode']:
        # Entering mechanic mode — always go to schedule
        return redirect(url_for('mechanic.schedule'))
    else:
        # Leaving mechanic mode — go to jobs list
        return redirect(url_for('jobs.index'))
