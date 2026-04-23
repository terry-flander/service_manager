from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import get_db
from datetime import date

jobs_bp = Blueprint('jobs', __name__)

TIME_SLOTS  = ['07:00', '07:30', '08:00', '08:30', '09:00', '09:30', '10:00', '10:30', '11:00', '11:30', '12:00', '12:30', '13:00', '13:30', '14:00', '14:30', '15:00', '15:30', '16:00', '16:30', '17:00', '17:30', '18:00', '18:30', '19:00', '19:30']

TIME_LABELS = {
    '07:00': '7:00 AM',
    '07:30': '7:30 AM',
    '08:00': '8:00 AM',
    '08:30': '8:30 AM',
    '09:00': '9:00 AM',
    '09:30': '9:30 AM',
    '10:00': '10:00 AM',
    '10:30': '10:30 AM',
    '11:00': '11:00 AM',
    '11:30': '11:30 AM',
    '12:00': '12:00 PM',
    '12:30': '12:30 PM',
    '13:00': '1:00 PM',
    '13:30': '1:30 PM',
    '14:00': '2:00 PM',
    '14:30': '2:30 PM',
    '15:00': '3:00 PM',
    '15:30': '3:30 PM',
    '16:00': '4:00 PM',
    '16:30': '4:30 PM',
    '17:00': '5:00 PM',
    '17:30': '5:30 PM',
    '18:00': '6:00 PM',
    '18:30': '6:30 PM',
    '19:00': '7:00 PM',
    '19:30': '7:30 PM'
}

SERVICE_TYPES = [
    'General Service',
    'eBike Service',
    'Tribe/Cargo Bike Service',
    '3 or More Bikes',
    'Other',
]

JOB_TYPES = {
    'booking':  {'label': 'Booking',  'prefix': 'FB'},
    'workshop': {'label': 'Workshop', 'prefix': 'PB'},
}


def generate_reference(job_type, conn):
    """Generate a unique reference within an existing connection.
    Scans ALL references with this prefix (regardless of job_type column)
    to guarantee no collision even if job_type is ever mismatched.
    """
    prefix = JOB_TYPES[job_type]['prefix']
    # Match prefix + hyphen at start of reference string
    like = f'{prefix}-%'
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(reference, ?) AS INTEGER)) as max_n "
        "FROM jobs WHERE reference LIKE ?",
        (len(prefix) + 2, like)).fetchone()
    next_id = (row['max_n'] or 0) + 1
    return f'{prefix}-{next_id:04d}'


def upsert_customer(conn, name, email, phone, suburb, address=''):
    """
    Find or create a customer by email.
    Returns (customer_id, customer_address).
    """
    email = (email or '').strip().lower()
    name  = (name  or '').strip()
    if not email:
        existing = conn.execute(
            "SELECT id, address FROM customers WHERE name=?", (name,)).fetchone()
        if existing:
            return existing['id'], existing['address'] or ''
        email = f"unknown_{name.lower().replace(' ','_')}@unknown.local"

    existing = conn.execute(
        "SELECT id, address FROM customers WHERE email=?", (email,)).fetchone()
    if existing:
        new_address = (address or '').strip() or existing['address'] or ''
        conn.execute("""
            UPDATE customers SET name=?, phone=?, suburb=?, address=? WHERE id=?
        """, (name, (phone or '').strip(), (suburb or '').strip(),
              new_address, existing['id']))
        return existing['id'], new_address
    else:
        conn.execute("""
            INSERT INTO customers (email, name, phone, suburb, address)
            VALUES (?, ?, ?, ?, ?)
        """, (email, name, (phone or '').strip(),
              (suburb or '').strip(), (address or '').strip()))
        return conn.execute(
            "SELECT id FROM customers WHERE email=?", (email,)).fetchone()['id'], \
               (address or '').strip()


@jobs_bp.route('/')
def index():
    status    = request.args.get('status', '')
    region_id = request.args.get('region_id', '')
    job_type  = request.args.get('job_type', '')
    with get_db() as conn:
        query = """
            SELECT j.*, r.name as region_name,
                   COALESCE((SELECT SUM(quantity*unit_cost)
                             FROM job_parts WHERE job_id=j.id), 0) as total,
                   c.id as cust_id
            FROM jobs j
            JOIN regions r ON j.region_id = r.id
            LEFT JOIN customers c ON j.customer_id = c.id
            WHERE 1=1
        """
        # Exclude void unless explicitly filtered to void
        if job_type != 'void' and status != 'void':
            query += " AND j.status != 'void'"
        params = []
        if status:
            query += " AND j.status = ?"
            params.append(status)
        if region_id:
            query += " AND j.region_id = ?"
            params.append(int(region_id))
        if job_type:
            query += " AND j.job_type = ?"
            params.append(job_type)
        query += " ORDER BY j.scheduled_date ASC, j.scheduled_time ASC, j.id DESC"
        jobs    = conn.execute(query, params).fetchall()
        regions = conn.execute("SELECT * FROM regions ORDER BY name").fetchall()
    return render_template('jobs/index.html', jobs=jobs, regions=regions,
                           status=status, region_id=region_id, job_type=job_type,
                           TIME_LABELS=TIME_LABELS, JOB_TYPES=JOB_TYPES)


@jobs_bp.route('/jobs/new', methods=['GET', 'POST'])
def new_job():
    with get_db() as conn:
        regions = conn.execute("SELECT * FROM regions ORDER BY name").fetchall()
    if request.method == 'POST':
        region_id  = int(request.form['region_id'])
        suburb     = request.form.get('suburb', '').strip()
        job_type   = request.form.get('job_type', 'booking')
        sched_date = request.form.get('scheduled_date') or None
        # Workshop jobs have no time slot
        sched_time = request.form.get('scheduled_time') or None
        end_time   = request.form.get('end_time') or None
        if job_type == 'workshop':
            sched_time = None
            end_time   = None
        cust_name  = request.form['customer_name']
        cust_email = request.form.get('customer_email', '').strip()
        cust_phone = request.form.get('customer_phone', '').strip()

        cust_address = request.form.get('customer_address', '').strip()
        import sqlite3 as _sqlite3
        for _attempt in range(5):
            with get_db() as conn:
                ref = generate_reference(job_type, conn)
                customer_id, stored_address = upsert_customer(
                    conn, cust_name, cust_email, cust_phone, suburb, cust_address)
                explicit_address = request.form.get('address', '').strip()
                job_address = explicit_address or stored_address or suburb
                try:
                    conn.execute("""
                        INSERT INTO jobs (reference, job_type, customer_id, customer_name,
                            customer_email, customer_phone, suburb, address, description,
                            bike_description, service_types, region_id, tax_inclusive,
                            scheduled_date, scheduled_time, end_time, status, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """, (ref, job_type, customer_id, cust_name, cust_email, cust_phone,
                          suburb, job_address, request.form.get('description', ''),
                          request.form.get('bike_description', ''),
                          ', '.join(request.form.getlist('service_types')),
                          region_id,
                          1 if request.form.get('tax_inclusive', '1') == '1' else 0,
                          sched_date, sched_time, end_time,
                          request.form.get('notes', '')))
                    job_id = conn.execute(
                        "SELECT id FROM jobs WHERE reference=?", (ref,)).fetchone()['id']
                    conn.commit()
                    break  # success
                except _sqlite3.IntegrityError as e:
                    if 'reference' in str(e) and _attempt < 4:
                        conn.rollback()
                        continue  # retry with next sequence number
                    raise

        msg = f'{JOB_TYPES[job_type]["label"]} {ref} created'
        if sched_date:
            msg += f', scheduled for {sched_date}'
            if sched_time:
                msg += f' at {TIME_LABELS.get(sched_time, sched_time)}'
        flash(msg + '.', 'success')
        return redirect(url_for('jobs.job_detail', job_id=job_id))

    with get_db() as conn:
        suburbs_list = conn.execute("""
            SELECT s.name, s.region_id, r.name as region_name
            FROM suburbs s JOIN regions r ON s.region_id=r.id
            ORDER BY s.name
        """).fetchall()
    return render_template('jobs/new.html', regions=regions,
                           TIME_SLOTS=TIME_SLOTS, TIME_LABELS=TIME_LABELS,
                           JOB_TYPES=JOB_TYPES, SERVICE_TYPES=SERVICE_TYPES,
                           suburbs_list=suburbs_list,
                           today=date.today().isoformat())


@jobs_bp.route('/jobs/<int:job_id>')
def job_detail(job_id):
    with get_db() as conn:
        job = conn.execute("""
            SELECT j.*, r.name as region_name, r.visit_day,
                   c.id as cust_id
            FROM jobs j
            JOIN regions r ON j.region_id=r.id
            LEFT JOIN customers c ON j.customer_id=c.id
            WHERE j.id=?
        """, (job_id,)).fetchone()
        if not job:
            return "Job not found", 404
        job_parts = conn.execute(
            "SELECT * FROM job_parts WHERE job_id=? ORDER BY id",
            (job_id,)).fetchall()
        parts = conn.execute(
            "SELECT * FROM parts WHERE active=1 ORDER BY name").fetchall()
        # All thread emails — inbound and outbound — chronological
        thread_emails = conn.execute("""
            SELECT 'inbound' as direction,
                   id, imported_at as sent_at, sender as from_addr,
                   subject, body, status, message_id
            FROM email_imports WHERE job_id=?
            UNION ALL
            SELECT 'outbound' as direction,
                   id, sent_at, to_address as from_addr,
                   subject, body, 'sent' as status, message_id
            FROM email_replies WHERE job_id=?
            ORDER BY sent_at ASC
        """, (job_id, job_id)).fetchall()
    total = sum(jp['quantity'] * jp['unit_cost'] for jp in job_parts)
    return render_template('jobs/detail.html', job=job, job_parts=job_parts,
                           parts=parts, total=total, TIME_LABELS=TIME_LABELS,
                           JOB_TYPES=JOB_TYPES, thread_emails=thread_emails)


@jobs_bp.route('/jobs/<int:job_id>/edit', methods=['GET', 'POST'])
def edit_job(job_id):
    with get_db() as conn:
        job     = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        regions = conn.execute("SELECT * FROM regions ORDER BY name").fetchall()
    if not job:
        return "Job not found", 404
    if request.method == 'POST':
        suburb     = request.form.get('suburb', '').strip()
        address    = request.form.get('address', '').strip() or suburb
        cust_name  = request.form['customer_name']
        cust_email = request.form.get('customer_email', '').strip()
        cust_phone = request.form.get('customer_phone', '').strip()
        job_type   = request.form.get('job_type', job['job_type'])
        sched_time = request.form.get('scheduled_time') or None
        end_time   = request.form.get('end_time') or None
        if job_type == 'workshop':
            sched_time = None
            end_time   = None
        cust_address = request.form.get('customer_address', '').strip()
        with get_db() as conn:
            customer_id, _ = upsert_customer(
                conn, cust_name, cust_email, cust_phone, suburb, cust_address)
            conn.execute("""
                UPDATE jobs SET job_type=?, customer_id=?, customer_name=?,
                    customer_email=?, customer_phone=?, suburb=?, address=?,
                    description=?, bike_description=?, service_types=?, region_id=?,
                    tax_inclusive=?, scheduled_date=?, scheduled_time=?, end_time=?,
                    status=?, notes=?, paid_date=?, amount_paid=?
                WHERE id=?
            """, (job_type, customer_id, cust_name, cust_email, cust_phone,
                  suburb, address, request.form.get('description', ''),
                  request.form.get('bike_description', ''),
                  ', '.join(request.form.getlist('service_types')),
                  int(request.form['region_id']),
                  1 if request.form.get('tax_inclusive', '1') == '1' else 0,
                  request.form.get('scheduled_date') or None,
                  sched_time,
                  request.form.get('end_time') or None,
                  request.form['status'],
                  request.form.get('notes', ''),
                  request.form.get('paid_date') or None,
                  float(request.form['amount_paid']) if request.form.get('amount_paid') else None,
                  job_id))
            conn.commit()
        flash('Job updated.', 'success')
        return redirect(url_for('jobs.job_detail', job_id=job_id))
    with get_db() as conn:
        suburbs_list = conn.execute("""
            SELECT s.name, s.region_id, r.name as region_name
            FROM suburbs s JOIN regions r ON s.region_id=r.id
            ORDER BY s.name
        """).fetchall()
    return render_template('jobs/edit.html', job=job, regions=regions,
                           TIME_SLOTS=TIME_SLOTS, TIME_LABELS=TIME_LABELS,
                           JOB_TYPES=JOB_TYPES, SERVICE_TYPES=SERVICE_TYPES,
                           suburbs_list=suburbs_list)


@jobs_bp.route('/jobs/<int:job_id>/part/<int:jp_id>/update', methods=['POST'])
def update_part(job_id, jp_id):
    """Inline update of quantity or unit_cost on a job_part row."""
    from flask import jsonify
    data  = request.get_json()
    field = data.get('field')
    value = data.get('value')

    if field not in ('quantity', 'unit_cost') or value is None:
        return jsonify({'error': 'invalid field'}), 400
    try:
        value = float(value)
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid value'}), 400

    with get_db() as conn:
        conn.execute(
            f"UPDATE job_parts SET {field}=? WHERE id=? AND job_id=?",
            (value, jp_id, job_id))
        conn.commit()
        jp = conn.execute(
            "SELECT quantity, unit_cost FROM job_parts WHERE id=?",
            (jp_id,)).fetchone()
        grand = conn.execute(
            "SELECT COALESCE(SUM(quantity*unit_cost),0) as t FROM job_parts WHERE job_id=?",
            (job_id,)).fetchone()['t']

    return jsonify({
        'ok':          True,
        'total':       round(jp['quantity'] * jp['unit_cost'], 2),
        'grand_total': round(grand, 2),
    })


@jobs_bp.route('/jobs/<int:job_id>/add-part', methods=['POST'])
def add_part(job_id):
    part_id = request.form.get('part_id')
    if part_id and part_id.strip():
        with get_db() as conn:
            part = conn.execute(
                "SELECT * FROM parts WHERE id=?", (int(part_id),)).fetchone()
            conn.execute("""
                INSERT INTO job_parts (job_id, part_id, description, part_number, quantity, unit_cost)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (job_id, part['id'], part['name'], part['part_number'],
                  float(request.form.get('quantity', 1)), part['unit_cost']))
            status = conn.execute(
                "SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()['status']
            if status in ('pending', 'scheduled'):
                conn.execute(
                    "UPDATE jobs SET status='in_progress' WHERE id=?", (job_id,))
            conn.commit()
    else:
        description = request.form.get('description', '').strip()
        part_number = request.form.get('part_number', '').strip()
        quantity    = float(request.form.get('quantity', 1))
        unit_cost   = float(request.form.get('unit_cost', 0))

        with get_db() as conn:
            # Upsert into master parts table when a part number is given
            master_part_id = None
            if part_number:
                conn.execute("""
                    INSERT INTO parts (name, part_number, unit_cost, unit, active)
                    VALUES (?, ?, ?, 'each', 1)
                    ON CONFLICT(part_number) DO UPDATE SET
                        name=excluded.name,
                        unit_cost=excluded.unit_cost,
                        active=1
                """, (description, part_number, unit_cost))
                # Ensure active=1 regardless of pre-existing state
                conn.execute(
                    "UPDATE parts SET active=1 WHERE part_number=?",
                    (part_number,))
                master_part_id = conn.execute(
                    "SELECT id FROM parts WHERE part_number=?",
                    (part_number,)).fetchone()['id']

            conn.execute("""
                INSERT INTO job_parts (job_id, part_id, description, part_number, quantity, unit_cost)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (job_id, master_part_id, description, part_number, quantity, unit_cost))

            status = conn.execute(
                "SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()['status']
            if status in ('pending', 'scheduled'):
                conn.execute(
                    "UPDATE jobs SET status='in_progress' WHERE id=?", (job_id,))
            conn.commit()
    flash('Part added.', 'success')
    return redirect(url_for('jobs.job_detail', job_id=job_id))


@jobs_bp.route('/jobs/<int:job_id>/remove-part/<int:jp_id>', methods=['POST'])
def remove_part(job_id, jp_id):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM job_parts WHERE id=? AND job_id=?", (jp_id, job_id))
        conn.commit()
    flash('Part removed.', 'success')
    return redirect(url_for('jobs.job_detail', job_id=job_id))


@jobs_bp.route('/jobs/<int:job_id>/delete', methods=['POST'])
def delete_job(job_id):
    with get_db() as conn:
        job = conn.execute(
            "SELECT reference FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return "Job not found", 404
        conn.execute("DELETE FROM email_imports WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM job_parts WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()
    flash(f'Job {job["reference"]} deleted.', 'success')
    return redirect(url_for('jobs.index'))


@jobs_bp.route('/jobs/<int:job_id>/status', methods=['POST'])
def update_status(job_id):
    from datetime import date as _date
    payment_type = request.form.get('payment_type', '').strip()
    new_status   = request.form['status']
    paid_date    = request.form.get('paid_date') or None
    amount_paid  = request.form.get('amount_paid', '').strip()
    amount_paid  = float(amount_paid) if amount_paid else None

    # Payment type shortcut — set status=paid, today's date, full total
    if payment_type:
        new_status = 'paid'
        paid_date  = _date.today().isoformat()
        with get_db() as conn:
            total = conn.execute(
                "SELECT COALESCE(SUM(quantity*unit_cost),0) FROM job_parts WHERE job_id=?",
                (job_id,)).fetchone()[0]
        amount_paid = round(float(total), 2)

    with get_db() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, paid_date=?, amount_paid=?, "
            "payment_type=? WHERE id=?",
            (new_status, paid_date, amount_paid, payment_type or None, job_id))
        conn.commit()
    msg = f'Paid via {payment_type}.' if payment_type else f'Status updated to {new_status}.'
    flash(msg, 'success')
    return redirect(url_for('jobs.job_detail', job_id=job_id))


@jobs_bp.route('/jobs/email-imports/message/<int:import_id>')
def email_message(import_id):
    with get_db() as conn:
        imp = conn.execute("""
            SELECT ei.*, j.reference
            FROM email_imports ei
            LEFT JOIN jobs j ON j.id = ei.job_id
            WHERE ei.id = ?
        """, (import_id,)).fetchone()
    if not imp:
        return "Message not found", 404
    return render_template('jobs/email_message.html', imp=imp)


@jobs_bp.route('/jobs/email-imports')
def email_imports():
    with get_db() as conn:
        imports = conn.execute("""
            SELECT ei.*, j.reference
            FROM email_imports ei
            LEFT JOIN jobs j ON j.id = ei.job_id
            ORDER BY ei.imported_at DESC
            LIMIT 200
        """).fetchall()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='email_polling'"
        ).fetchone()
        polling_on = (row['value'] == 'on') if row else True
    import os
    poll_minutes = int(os.environ.get('GMAIL_POLL_MINUTES', '5'))
    return render_template('jobs/email_imports.html',
                           imports=imports, polling_on=polling_on,
                           poll_minutes=poll_minutes)


@jobs_bp.route('/jobs/email-polling-toggle', methods=['POST'])
def toggle_polling():
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='email_polling'"
        ).fetchone()
        new_val = 'off' if (row and row['value'] == 'on') else 'on'
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('email_polling', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (new_val,))
        conn.commit()
    flash(f"Email polling turned {'on' if new_val == 'on' else 'off'}.", 'success')
    return redirect(url_for('jobs.email_imports'))


@jobs_bp.route('/jobs/poll-email', methods=['POST'])
def poll_now():
    """Manually trigger an email poll (admin only)."""
    from flask import current_app
    try:
        from email_poller import poll_once
        n = poll_once(current_app._get_current_object(), force=True)
        flash(f'Email poll complete: {n} new job(s) imported.', 'success')
    except Exception as e:
        flash(f'Poll error: {e}', 'danger')
    return redirect(url_for('jobs.email_imports'))


@jobs_bp.route('/settings/status-colors', methods=['GET', 'POST'])
def status_colors():
    """Admin page to configure per-status badge colours."""
    statuses = ['pending', 'scheduled', 'in_progress', 'complete',
                'invoiced', 'paid', 'void']
    defaults = {
        'pending':     '#f59e0b',
        'scheduled':   '#3b82f6',
        'in_progress': '#8b5cf6',
        'complete':    '#10b981',
        'invoiced':    '#6b7280',
        'paid':        '#10b981',
        'void':        '#ef4444',
    }
    if request.method == 'POST':
        with get_db() as conn:
            for s in statuses:
                color = request.form.get(f'color_{s}', defaults[s]).strip()
                conn.execute(
                    "INSERT INTO settings (key,value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (f'status_color_{s}', color))
            conn.commit()
        flash('Status colours saved.', 'success')
        return redirect(url_for('jobs.status_colors'))

    with get_db() as conn:
        colors_map = {}
        for s in statuses:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (f'status_color_{s}',)).fetchone()
            colors_map[s] = row['value'] if row else defaults[s]
    return render_template('jobs/status_colors.html',
                           statuses=statuses, colors_map=colors_map,
                           defaults=defaults)
