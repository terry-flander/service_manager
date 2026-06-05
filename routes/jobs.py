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
    'rental':   {'label': 'Rental',   'prefix': 'RB'},
    'sale':     {'label': 'Sale',     'prefix': 'CS'},
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
        # Try to match on name, phone, or email (any non-empty field)
        existing = None
        if name:
            existing = conn.execute(
                "SELECT id, address FROM customers WHERE LOWER(name)=LOWER(?)",
                (name,)).fetchone()
        if not existing and phone:
            existing = conn.execute(
                "SELECT id, address FROM customers WHERE phone=?",
                (phone,)).fetchone()
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


@jobs_bp.route('/jobs/new-sale', methods=['GET', 'POST'])
def new_sale():
    """Create a new cash sale (CS- prefix) — minimal form, lands on detail."""
    from datetime import date as _date
    if request.method == 'POST':
        sale_date    = request.form.get('sale_date') or _date.today().isoformat()
        payment_type = request.form.get('payment_type', '').strip()
        notes        = request.form.get('notes', '').strip()
        if not payment_type:
            flash('Payment type is required.', 'danger')
            return render_template('jobs/new_sale.html',
                                   today=_date.today().isoformat())

        with get_db() as conn:
            # Get or create Cash Sales customer
            cust = conn.execute(
                "SELECT id FROM customers WHERE email='cash.sales@flyingbike.internal'"
            ).fetchone()
            if not cust:
                conn.execute("""
                    INSERT INTO customers (name, email, phone, suburb, address)
                    VALUES ('Cash Sales','cash.sales@flyingbike.internal','','','')
                """)
                conn.commit()
                cust = conn.execute(
                    "SELECT id FROM customers WHERE email='cash.sales@flyingbike.internal'"
                ).fetchone()
            cust_id = cust['id']

            ref = generate_reference('sale', conn)
            conn.execute("""
                INSERT INTO jobs (reference, job_type, customer_id, customer_name,
                    customer_email, customer_phone, suburb, address, description,
                    region_id, tax_inclusive, scheduled_date, status,
                    payment_type, paid_date, notes)
                VALUES (?, 'sale', ?, 'Cash Sales',
                    'cash.sales@flyingbike.internal', '', '', '', '',
                    1, 1, ?, 'paid', ?, ?, ?)
            """, (ref, cust_id, sale_date, payment_type, sale_date, notes))
            conn.commit()
            job_id = conn.execute(
                "SELECT id FROM jobs WHERE reference=?", (ref,)).fetchone()['id']

        flash(f'Sale {ref} created. Add parts below.', 'success')
        return redirect(url_for('jobs.job_detail', job_id=job_id))

    from datetime import date as _date
    return render_template('jobs/new_sale.html',
                           today=_date.today().isoformat())


def _recalc_sale_total(conn, job_id):
    """After adding/removing parts on a sale job, update amount_paid to parts total."""
    job = conn.execute(
        "SELECT job_type, payment_type, scheduled_date FROM jobs WHERE id=?",
        (job_id,)).fetchone()
    if not job or job['job_type'] != 'sale':
        return
    parts = conn.execute(
        "SELECT quantity, unit_cost FROM job_parts WHERE job_id=?",
        (job_id,)).fetchall()
    total = round(sum(p['quantity'] * p['unit_cost'] for p in parts), 2)
    conn.execute(
        "UPDATE jobs SET amount_paid=?, paid_date=coalesce(paid_date, scheduled_date) "
        "WHERE id=?",
        (total, job_id))
    conn.commit()


@jobs_bp.route('/')
def index():
    import json as _json
    from flask import session as _sess
    user_id   = _sess.get('user_id')
    PREFS_KEY = f'job_filter_{user_id}'

    status    = request.args.get('status', '')
    region_id = request.args.get('region_id', '')
    job_type  = request.args.get('job_type', '')
    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to', '')
    sort      = request.args.get('sort', 'date')

    SORT_MAP = {
        'scheduled': 'j.scheduled_date ASC, j.scheduled_time',
        'paid':      'j.paid_date',
        'ref':       'j.reference',
        'type':      'j.job_type',
        'customer':  'j.customer_name',
        'gross':     'total_sort',   # post-query
        'payment':   'j.payment_type',
        'amount':    'j.amount_paid',
        'status':    'j.status',
        'region':    'r.name',
        # legacy compat
        'date':      'j.scheduled_date ASC, j.scheduled_time',
        'total':     'total_sort',
    }
    if sort not in SORT_MAP:
        sort = 'paid'

    has_params = bool(request.args)

    with get_db() as conn:
        if has_params:
            # Save current filter selection to settings
            prefs = {'status': status, 'region_id': region_id,
                     'job_type': job_type, 'date_from': date_from,
                     'date_to': date_to, 'sort': sort}
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (PREFS_KEY, _json.dumps(prefs)))
            conn.commit()
        else:
            # No params — restore saved filters and redirect immediately
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (PREFS_KEY,)).fetchone()
            if row:
                try:
                    prefs = _json.loads(row['value'])
                    params = {k: v for k, v in prefs.items() if v}
                    if params:
                        return redirect(url_for('jobs.index', **params))
                except Exception:
                    pass

    with get_db() as conn:
        query = """
            SELECT j.*, r.name as region_name,
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
        if date_from:
            query += " AND j.scheduled_date >= ?"
            params.append(date_from)
        if date_to:
            query += " AND j.scheduled_date <= ?"
            params.append(date_to)
        # ORDER BY
        if sort in ('gross', 'total'):
            query += " ORDER BY coalesce(j.paid_date, j.scheduled_date) ASC, j.id DESC"
        elif sort == 'paid':
            query += " ORDER BY j.paid_date ASC, j.scheduled_date ASC, j.id DESC"
        else:
            order_col = SORT_MAP[sort]
            query += f" ORDER BY {order_col} ASC, j.id DESC"
        jobs_raw = conn.execute(query, params).fetchall()

        # Fetch all parts for these jobs in one query, compute GST-correct totals
        if jobs_raw:
            job_ids   = [j['id'] for j in jobs_raw]
            ph        = ','.join('?' * len(job_ids))
            parts_map = {}
            for p in conn.execute(
                f"SELECT * FROM job_parts WHERE job_id IN ({ph})", job_ids
            ).fetchall():
                parts_map.setdefault(p['job_id'], []).append(p)

        from routes.invoice import calc_totals
        jobs = []
        for j in jobs_raw:
            j_parts = parts_map.get(j['id'], []) if jobs_raw else []
            if (j['payment_type'] or '').lower() == 'cash':
                total = round(sum(p['quantity'] * p['unit_cost'] for p in j_parts), 2)
            else:
                _, _, total = calc_totals(j_parts, bool(j['tax_inclusive']))
            jobs.append((j, total))

        # Post-query sort for computed gross column
        if sort in ('gross', 'total'):
            jobs.sort(key=lambda x: x[1] or 0, reverse=True)
        elif sort == 'amount':
            jobs.sort(key=lambda x: x[0]['amount_paid'] or 0, reverse=True)

        regions = conn.execute("SELECT * FROM regions ORDER BY name").fetchall()
    return render_template('jobs/index.html', jobs=jobs, regions=regions,
                           status=status, region_id=region_id, job_type=job_type,
                           date_from=date_from, date_to=date_to, sort=sort,
                           TIME_LABELS=TIME_LABELS, JOB_TYPES=JOB_TYPES)


@jobs_bp.route('/jobs/new', methods=['GET', 'POST'])
def new_job():
    with get_db() as conn:
        regions = conn.execute("SELECT * FROM regions ORDER BY name").fetchall()
    if request.method == 'POST':
        region_id  = int(request.form.get('region_id') or 1)
        suburb     = request.form.get('suburb', '').strip()
        job_type   = request.form.get('job_type', 'booking')
        sched_date = request.form.get('scheduled_date') or None
        sched_time = request.form.get('scheduled_time') or None
        end_time   = request.form.get('end_time') or None
        end_date   = request.form.get('end_date') or None
        # Workshop and rental jobs have no time slots
        if job_type in ('workshop', 'rental'):
            sched_time = None
            end_time   = None
        # Non-rental jobs have no end_date
        if job_type != 'rental':
            end_date = None
        cust_name  = request.form['customer_name']
        cust_email = request.form.get('customer_email', '').strip()
        cust_phone = request.form.get('customer_phone', '').strip()

        cust_address = request.form.get('customer_address', '').strip()
        # If customer_id was passed from the customer page, use it directly
        supplied_cust_id = request.form.get('customer_id_prefill', '').strip()
        import sqlite3 as _sqlite3
        for _attempt in range(5):
            with get_db() as conn:
                ref = generate_reference(job_type, conn)
                if supplied_cust_id and supplied_cust_id.isdigit():
                    customer_id   = int(supplied_cust_id)
                    stored_address = cust_address
                else:
                    customer_id, stored_address = upsert_customer(
                        conn, cust_name, cust_email, cust_phone, suburb, cust_address)
                explicit_address = request.form.get('address', '').strip()
                job_address = explicit_address or stored_address or suburb
                try:
                    # Rental: no region/suburb/bike_desc/service_types
                    _suburb       = '' if job_type == 'rental' else suburb
                    _region_id    = region_id
                    _bike_desc    = '' if job_type == 'rental' else request.form.get('bike_description', '')
                    _svc_types    = '' if job_type == 'rental' else ', '.join(request.form.getlist('service_types'))
                    conn.execute("""
                        INSERT INTO jobs (reference, job_type, customer_id, customer_name,
                            customer_email, customer_phone, suburb, address, description,
                            bike_description, service_types, region_id, tax_inclusive,
                            scheduled_date, scheduled_time, end_time, end_date, status, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """, (ref, job_type, customer_id, cust_name, cust_email, cust_phone,
                          _suburb, job_address, request.form.get('description', ''),
                          _bike_desc, _svc_types, _region_id,
                          1 if request.form.get('tax_inclusive', '1') == '1' else 0,
                          sched_date, sched_time, end_time, end_date,
                          request.form.get('notes', '')))
                    job_id = conn.execute(
                        "SELECT id FROM jobs WHERE reference=?", (ref,)).fetchone()['id']

                    # Auto-add a job_part for each selected service type (booking only)
                    selected_types = request.form.getlist('service_types') if job_type == 'booking' else []
                    for stype in selected_types:
                        part = conn.execute(
                            """SELECT id, name, part_number, unit_cost FROM parts
                                WHERE LOWER(name) = LOWER(?) AND active = 1
                                LIMIT 1""",
                            (stype,)).fetchone()
                        if part:
                            conn.execute(
                                """INSERT INTO job_parts
                                    (job_id, part_id, description, part_number,
                                     quantity, unit_cost)
                                   VALUES (?, ?, ?, ?, 1, ?)""",
                                (job_id, part['id'], part['name'],
                                 part['part_number'] or '', part['unit_cost']))

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

    # ── GET — optionally pre-fill from a customer record ─────────────────────
    prefill_customer = None
    customer_id_param = request.args.get('customer_id', '').strip()
    if customer_id_param and customer_id_param.isdigit():
        with get_db() as conn:
            prefill_customer = conn.execute(
                "SELECT * FROM customers WHERE id=?",
                (int(customer_id_param),)).fetchone()

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
                           today=date.today().isoformat(),
                           prefill_customer=prefill_customer)


@jobs_bp.route('/jobs/<int:job_id>', methods=['GET', 'POST'])
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
        regions = conn.execute("SELECT * FROM regions ORDER BY name").fetchall()

    if request.method == 'POST':
        jt          = job['job_type']
        description = request.form.get('description', '').strip()
        address     = request.form.get('address', '').strip()
        bike_desc   = request.form.get('bike_description', '').strip() if jt == 'workshop' else (job['bike_description'] or '')
        notes       = request.form.get('notes', '').strip()
        tax_incl    = int(request.form.get('tax_inclusive', '1') or 1)
        # Status & Payment (merged from separate update_status form)
        new_status     = request.form.get('status', job['status'])
        invoice_number = request.form.get('invoice_number', '').strip() or None
        paid_date      = request.form.get('paid_date', '').strip() or None
        amount_paid_s  = request.form.get('amount_paid', '').strip()
        amount_paid    = float(amount_paid_s) if amount_paid_s else None
        # payment_type: prefer submitted value, fall back to _payment_display, then keep existing
        payment_type   = (request.form.get('payment_type', '').strip()
                          or request.form.get('_payment_display', '').strip()
                          or job['payment_type'] or None)

        if jt == 'booking':
            sched_date = request.form.get('scheduled_date') or None
            sched_time = request.form.get('scheduled_time') or None
            end_time   = request.form.get('end_time') or None
            end_date   = None
            region_id  = int(request.form.get('region_id') or job['region_id'])
        elif jt == 'workshop':
            sched_date = request.form.get('scheduled_date') or None
            sched_time = None
            end_time   = None
            end_date   = None
            region_id  = job['region_id']
        else:  # rental
            sched_date = request.form.get('scheduled_date') or None
            end_date   = request.form.get('end_date') or None
            sched_time = None
            end_time   = None
            region_id  = job['region_id']

        with get_db() as wconn:
            wconn.execute("""
                UPDATE jobs
                SET description=?, bike_description=?, address=?,
                    scheduled_date=?, scheduled_time=?, end_time=?, end_date=?,
                    region_id=?, tax_inclusive=?, notes=?,
                    status=?, invoice_number=?,
                    paid_date=?, amount_paid=?, payment_type=?
                WHERE id=?
            """, (description, bike_desc, address,
                  sched_date, sched_time, end_time, end_date,
                  region_id, tax_incl, notes,
                  new_status, invoice_number,
                  paid_date, amount_paid, payment_type,
                  job_id))
            if jt == 'sale':
                wconn.execute(
                    "UPDATE jobs SET paid_date=coalesce(paid_date, scheduled_date) WHERE id=?",
                    (job_id,))
            wconn.commit()
            if jt == 'sale':
                _recalc_sale_total(wconn, job_id)

        flash('Job updated.', 'success')
        return_to = request.form.get('return_to', '').strip()
        if return_to in ('calendar', 'email', 'jobs'):
            return redirect(url_for({
                'calendar': 'calendar.index',
                'email':    'jobs.email_imports',
                'jobs':     'jobs.index',
            }[return_to]))
        return redirect(url_for('jobs.job_detail', job_id=job_id))

    with get_db() as conn:
        job_parts = conn.execute(
            "SELECT * FROM job_parts WHERE job_id=? ORDER BY id",
            (job_id,)).fetchall()
        parts = conn.execute(
            "SELECT * FROM parts WHERE active=1 ORDER BY name").fetchall()
        from routes.invoice import calc_totals as _calc
        _, _, total = _calc(job_parts, bool(job['tax_inclusive']))
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

    return render_template('jobs/detail.html', job=job, job_parts=job_parts,
                           parts=parts, total=total, regions=regions,
                           thread_emails=thread_emails,
                           TIME_SLOTS=TIME_SLOTS, TIME_LABELS=TIME_LABELS,
                           JOB_TYPES=JOB_TYPES)


@jobs_bp.route('/jobs/<int:job_id>/edit', methods=['GET', 'POST'])
def edit_job(job_id):
    """Kept for backwards-compat; redirects to merged detail page."""
    return redirect(url_for('jobs.job_detail', job_id=job_id))


@jobs_bp.route('/jobs/<int:job_id>/edit_legacy', methods=['GET', 'POST'])
def edit_job_legacy(job_id):
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
        end_date   = request.form.get('end_date') or None
        if job_type in ('workshop', 'rental'):
            sched_time = None
            end_time   = None
        if job_type != 'rental':
            end_date = None
        # Rental: clear inapplicable fields
        _suburb     = '' if job_type == 'rental' else suburb
        _bike_desc  = '' if job_type == 'rental' else request.form.get('bike_description', '')
        _svc_types  = '' if job_type == 'rental' else ', '.join(request.form.getlist('service_types'))
        _region_id  = int(request.form.get('region_id') or 1)
        cust_address = request.form.get('customer_address', '').strip()
        import sqlite3 as _sqlite3
        for _attempt in range(5):
            with get_db() as conn:
                customer_id, _ = upsert_customer(
                    conn, cust_name, cust_email, cust_phone, suburb, cust_address)

                # Re-number if job_type changed
                new_ref = job['reference']
                if job_type != job['job_type']:
                    new_ref = generate_reference(job_type, conn)

                try:
                    conn.execute("""
                        UPDATE jobs SET job_type=?, reference=?, customer_id=?,
                            customer_name=?, customer_email=?, customer_phone=?,
                            suburb=?, address=?, description=?, bike_description=?,
                            service_types=?, region_id=?, tax_inclusive=?,
                            scheduled_date=?, scheduled_time=?, end_time=?, end_date=?,
                            status=?, notes=?, paid_date=?, amount_paid=?
                        WHERE id=?
                    """, (job_type, new_ref, customer_id,
                          cust_name, cust_email, cust_phone,
                          _suburb, address, request.form.get('description', ''),
                          _bike_desc, _svc_types, _region_id,
                          1 if request.form.get('tax_inclusive', '1') == '1' else 0,
                          request.form.get('scheduled_date') or None,
                          sched_time, end_time, end_date,
                          request.form['status'],
                          request.form.get('notes', ''),
                          request.form.get('paid_date') or None,
                          float(request.form['amount_paid']) if request.form.get('amount_paid') else None,
                          job_id))
                    conn.commit()
                    break
                except _sqlite3.IntegrityError as e:
                    if 'reference' in str(e) and _attempt < 4:
                        conn.rollback()
                        continue
                    raise

        if job_type != job['job_type']:
            flash(f'Job type changed — re-numbered to {new_ref}.', 'success')
        else:
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
        job_row = conn.execute(
            "SELECT tax_inclusive FROM jobs WHERE id=?", (job_id,)).fetchone()
        all_parts = conn.execute(
            "SELECT * FROM job_parts WHERE job_id=?", (job_id,)).fetchall()
        from routes.invoice import calc_totals as _calc
        _, _, grand = _calc(all_parts, bool(job_row['tax_inclusive']))

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
                  float(request.form.get('quantity', 1)),
                  float(request.form.get('unit_cost') or part['unit_cost'])))
            conn.commit()
            _recalc_sale_total(conn, job_id)
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

            conn.commit()
            _recalc_sale_total(conn, job_id)
    flash('Part added.', 'success')
    # Preserve the ?from= param so return_to still works after adding a part
    from_param = request.args.get('from', '')
    suffix = ('?from=' + from_param if from_param else '') + '#add-part'
    return redirect(url_for('jobs.job_detail', job_id=job_id) + suffix)


@jobs_bp.route('/jobs/<int:job_id>/remove-part/<int:jp_id>', methods=['POST'])
def remove_part(job_id, jp_id):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM job_parts WHERE id=? AND job_id=?", (jp_id, job_id))
        conn.commit()
        _recalc_sale_total(conn, job_id)
    flash('Part removed.', 'success')
    from_param = request.args.get('from', '')
    suffix = ('?from=' + from_param) if from_param else ''
    return redirect(url_for('jobs.job_detail', job_id=job_id) + suffix)


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
    amount_paid    = request.form.get('amount_paid', '').strip()
    amount_paid    = float(amount_paid) if amount_paid else None
    invoice_number = request.form.get('invoice_number', '').strip() or None

    # Payment type set — server only forces status=paid
    if payment_type:
        new_status = 'paid'

    with get_db() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, paid_date=?, amount_paid=?, "
            "payment_type=?, invoice_number=? WHERE id=?",
            (new_status, paid_date, amount_paid, payment_type or None,
             invoice_number, job_id))
        conn.commit()
    msg = f'Paid via {payment_type}.' if payment_type else f'Status updated to {new_status}.'
    flash(msg, 'success')
    return_to = request.form.get('return_to', '').strip()
    if return_to in ('calendar', 'email', 'jobs'):
        return redirect(url_for({
            'calendar': 'calendar.index',
            'email':    'jobs.email_imports',
            'jobs':     'jobs.index',
        }[return_to]))
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


@jobs_bp.route('/jobs/email-replies/message/<int:reply_id>')
def email_reply_message(reply_id):
    with get_db() as conn:
        reply = conn.execute("""
            SELECT er.*,
                   j.reference,
                   u.name as sent_by_name
            FROM email_replies er
            LEFT JOIN jobs j ON j.id = er.job_id
            LEFT JOIN users u ON u.id = er.sent_by
            WHERE er.id = ?
        """, (reply_id,)).fetchone()
    if not reply:
        return "Message not found", 404
    return render_template('jobs/email_reply_message.html', reply=reply)


@jobs_bp.route('/jobs/email-imports')
def email_imports():
    import json as _json
    from flask import session as _sess
    user_id   = _sess.get('user_id')
    PREFS_KEY = f'email_filter_{user_id}'

    q          = request.args.get('q',          '').strip()
    filter_    = request.args.get('filter',     'all')
    date_from  = request.args.get('date_from',  '')
    date_to    = request.args.get('date_to',    '')

    if filter_ not in ('all', 'unread', 'no_reply'):
        filter_ = 'all'

    has_params = bool(request.args)

    with get_db() as conn:
        if has_params:
            prefs = {'q': q, 'filter': filter_,
                     'date_from': date_from, 'date_to': date_to}
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (PREFS_KEY, _json.dumps(prefs)))
            conn.commit()
        else:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (PREFS_KEY,)).fetchone()
            if row:
                try:
                    prefs     = _json.loads(row['value'])
                    q         = prefs.get('q',         '')
                    filter_   = prefs.get('filter',    'all')
                    date_from = prefs.get('date_from', '')
                    date_to   = prefs.get('date_to',   '')
                    params = {'filter': filter_}
                    if q:         params['q']         = q
                    if date_from: params['date_from'] = date_from
                    if date_to:   params['date_to']   = date_to
                    return redirect(url_for('jobs.email_imports', **params))
                except Exception:
                    pass

        # Build query
        wheres, params = [], []

        if q:
            wheres.append("(LOWER(ei.subject) LIKE LOWER(?) "
                          "OR LOWER(ei.sender) LIKE LOWER(?))")
            params += [f'%{q}%', f'%{q}%']

        if filter_ == 'unread':
            wheres.append("(ei.read = 1 OR ei.read IS NULL)")
        elif filter_ == 'no_reply':
            wheres.append("ei.job_id IS NOT NULL")
            wheres.append("""NOT EXISTS (
                SELECT 1 FROM email_replies er WHERE er.job_id = ei.job_id
            )""")
            wheres.append("ei.status = 'ok'")

        if date_from:
            wheres.append("coalesce(ei.received_at, ei.imported_at) >= ?")
            params.append(date_from)
        if date_to:
            wheres.append("coalesce(ei.received_at, ei.imported_at) < ?")
            params.append(date_to + 'T23:59:59')

        where_sql = ('WHERE ' + ' AND '.join(wheres)) if wheres else ''

        imports = conn.execute(f"""
            SELECT ei.*, j.reference
            FROM email_imports ei
            LEFT JOIN jobs j ON j.id = ei.job_id
            {where_sql}
            ORDER BY coalesce(ei.received_at, ei.imported_at) DESC
            LIMIT 500
        """, params).fetchall()

        row = conn.execute(
            "SELECT value FROM settings WHERE key='email_polling'"
        ).fetchone()
        polling_on = (row['value'] == 'on') if row else True

    import os
    poll_minutes = int(os.environ.get('GMAIL_POLL_MINUTES', '5'))
    return render_template('jobs/email_imports.html',
                           imports=imports, polling_on=polling_on,
                           poll_minutes=poll_minutes,
                           q=q, filter=filter_,
                           date_from=date_from, date_to=date_to)


@jobs_bp.route('/jobs/email-imports/clear-filters', methods=['POST'])
def clear_email_filters():
    from flask import session as _sess
    user_id = _sess.get('user_id')
    if user_id:
        with get_db() as conn:
            conn.execute("DELETE FROM settings WHERE key=?",
                         (f'email_filter_{user_id}',))
            conn.commit()
    return redirect(url_for('jobs.email_imports'))


@jobs_bp.route('/jobs/email-imports/<int:import_id>/mark-read', methods=['POST'])
def mark_email_read(import_id):
    """Mark an email import as read. Returns JSON for AJAX calls, redirect otherwise."""
    from flask import jsonify
    with get_db() as conn:
        conn.execute("UPDATE email_imports SET read=0 WHERE id=?", (import_id,))
        imp = conn.execute(
            "SELECT job_id FROM email_imports WHERE id=?", (import_id,)).fetchone()
        conn.commit()
    # AJAX call from inbox JS
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    # Direct form submission fallback
    if imp and imp['job_id']:
        return redirect(url_for('jobs.job_detail', job_id=imp['job_id']))
    return redirect(url_for('jobs.email_imports'))


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


@jobs_bp.route('/admin/poll-log')
def poll_log():
    """Run a poll and return the log output as plain text. Admin only."""
    from flask import session as _sess
    if _sess.get('user_role') != 'admin':
        return 'Admin access required', 403

    import logging, io
    from flask import current_app, Response

    # Capture all log output into a string buffer
    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(name)s %(levelname)s %(message)s'))

    # Attach to root logger and email_poller specifically
    root_logger   = logging.getLogger()
    poller_logger = logging.getLogger('email_poller')

    old_root_level   = root_logger.level
    old_poller_level = poller_logger.level

    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)
    poller_logger.setLevel(logging.DEBUG)

    result_line = ''
    try:
        from email_poller import poll_once
        n = poll_once(current_app._get_current_object(), force=True)
        result_line = f'\n=== Poll complete: {n} new message(s) imported ===\n'
    except Exception as e:
        import traceback
        result_line = f'\n=== Poll error: {e} ===\n{traceback.format_exc()}'
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(old_root_level)
        poller_logger.setLevel(old_poller_level)

    output = log_buf.getvalue() + result_line
    return Response(output, mimetype='text/plain')



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


@jobs_bp.route('/jobs/email-imports/clear-search', methods=['POST'])
def clear_email_search():
    from flask import session as _sess
    user_id = _sess.get('user_id')
    if user_id:
        with get_db() as conn:
            conn.execute("DELETE FROM settings WHERE key=?",
                         (f'email_search_{user_id}',))
            conn.commit()
    return redirect(url_for('jobs.email_imports'))


@jobs_bp.route('/jobs/<int:job_id>/change-type', methods=['POST'])
def change_type(job_id):
    from flask import jsonify
    data     = request.get_json()
    new_type = data.get('job_type', '').strip()
    if new_type not in JOB_TYPES:
        return jsonify({'ok': False, 'error': 'Invalid job type'}), 400

    import sqlite3 as _sqlite3
    for _attempt in range(5):
        with get_db() as conn:
            job = conn.execute(
                "SELECT job_type, reference FROM jobs WHERE id=?",
                (job_id,)).fetchone()
            if not job:
                return jsonify({'ok': False, 'error': 'Job not found'}), 404
            if job['job_type'] == new_type:
                return jsonify({'ok': True, 'job_id': job_id,
                                'reference': job['reference']})
            new_ref = generate_reference(new_type, conn)
            try:
                conn.execute(
                    "UPDATE jobs SET job_type=?, reference=? WHERE id=?",
                    (new_type, new_ref, job_id))
                conn.commit()
                return jsonify({'ok': True, 'job_id': job_id,
                                'reference': new_ref})
            except _sqlite3.IntegrityError as e:
                if 'reference' in str(e) and _attempt < 4:
                    conn.rollback()
                    continue
                return jsonify({'ok': False, 'error': str(e)}), 500
    return jsonify({'ok': False, 'error': 'Could not generate reference'}), 500


@jobs_bp.route('/jobs/clear-filters', methods=['POST'])
def clear_job_filters():
    from flask import session as _sess
    user_id = _sess.get('user_id')
    if user_id:
        with get_db() as conn:
            conn.execute("DELETE FROM settings WHERE key=?",
                         (f'job_filter_{user_id}',))
            conn.commit()
    return redirect(url_for('jobs.index'))


@jobs_bp.route('/admin/backup-db')
def backup_db():
    """Download a live backup of the SQLite database. Admin only."""
    from flask import send_file, session as _session
    import sqlite3 as _sq
    import tempfile, os
    from datetime import datetime

    if _session.get('user_role') != 'admin':
        flash('Admin access required.', 'danger')
        return redirect(url_for('jobs.index'))

    from models import DB_PATH
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname     = f'field_service_{timestamp}.db'

    # Write to a temp file using sqlite3.backup() — safe on a live DB
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    try:
        src_conn = _sq.connect(DB_PATH)
        bak_conn = _sq.connect(tmp.name)
        src_conn.backup(bak_conn)
        bak_conn.close()
        src_conn.close()
        return send_file(
            tmp.name,
            as_attachment=True,
            download_name=fname,
            mimetype='application/x-sqlite3',
        )
    except Exception as e:
        os.unlink(tmp.name)
        flash(f'Backup failed: {e}', 'danger')
        return redirect(url_for('jobs.index'))
