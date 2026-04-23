
def get_suburbs(conn):
    return conn.execute("""
        SELECT s.name, s.region_id, r.name as region_name
        FROM suburbs s JOIN regions r ON s.region_id=r.id
        ORDER BY s.name
    """).fetchall()

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from models import get_db

customers_bp = Blueprint('customers', __name__)


@customers_bp.route('/customers')
def index():
    q = request.args.get('q', '').strip()
    with get_db() as conn:
        if q:
            like = f'%{q}%'
            customers = conn.execute("""
                SELECT c.*,
                       COUNT(j.id) as job_count,
                       MAX(j.scheduled_date) as last_job
                FROM customers c
                LEFT JOIN jobs j ON j.customer_id = c.id
                WHERE c.name LIKE ?
                   OR c.phone LIKE ?
                GROUP BY c.id
                ORDER BY c.name
            """, (like, like)).fetchall()
        else:
            customers = conn.execute("""
                SELECT c.*,
                       COUNT(j.id) as job_count,
                       MAX(j.scheduled_date) as last_job
                FROM customers c
                LEFT JOIN jobs j ON j.customer_id = c.id
                GROUP BY c.id
                ORDER BY c.name
            """).fetchall()
    return render_template('customers/index.html', customers=customers, q=q)


@customers_bp.route('/customers/search')
def search():
    """JSON endpoint for live name search used by job forms."""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, name, email, phone, suburb, address
            FROM customers
            WHERE name LIKE ?
            ORDER BY name
            LIMIT 10
        """, (f'%{q}%',)).fetchall()
    return jsonify([dict(r) for r in rows])


@customers_bp.route('/customers/new', methods=['GET', 'POST'])
def new_customer():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        name  = request.form.get('name', '').strip()
        if not email or not name:
            flash('Name and email are required.', 'danger')
            with get_db() as conn:
                suburbs = get_suburbs(conn)
            return render_template('customers/form.html', customer=None, action='new',
                                   suburbs=suburbs)
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM customers WHERE email=?", (email,)).fetchone()
            if existing:
                flash(f'A customer with email {email} already exists.', 'danger')
                return render_template('customers/form.html',
                                       customer=request.form, action='new')
            conn.execute("""
                INSERT INTO customers (email, name, phone, suburb, address)
                VALUES (?, ?, ?, ?, ?)
            """, (email, name,
                  request.form.get('phone', '').strip(),
                  request.form.get('suburb', '').strip(),
                  request.form.get('address', '').strip()))
            conn.commit()
        flash(f'Customer {name} created.', 'success')
        return redirect(url_for('customers.index'))
    with get_db() as conn:
        suburbs = get_suburbs(conn)
    return render_template('customers/form.html', customer=None, action='new',
                           suburbs=suburbs)


@customers_bp.route('/customers/<int:customer_id>/merge', methods=['GET', 'POST'])
def merge_customer(customer_id):
    with get_db() as conn:
        source = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not source:
            return "Customer not found", 404

    if request.method == 'POST':
        target_id  = request.form.get('target_id', type=int)
        keep_email   = request.form.get('keep_email',   'target')
        keep_phone   = request.form.get('keep_phone',   'target')
        keep_address = request.form.get('keep_address', 'target')

        if not target_id or target_id == customer_id:
            flash('Please select a valid target customer.', 'danger')
            return redirect(url_for('customers.merge_customer', customer_id=customer_id))

        with get_db() as conn:
            target = conn.execute("SELECT * FROM customers WHERE id=?", (target_id,)).fetchone()
            if not target:
                flash('Target customer not found.', 'danger')
                return redirect(url_for('customers.merge_customer', customer_id=customer_id))

            src = dict(source)
            tgt = dict(target)

            final_email   = src['email']   if keep_email   == 'source' else tgt['email']
            final_phone   = src['phone']   if keep_phone   == 'source' else tgt['phone']
            final_address = src['address'] if keep_address == 'source' else tgt['address']
            final_suburb  = src['suburb']  if keep_address == 'source' else tgt['suburb']

            # Update target with the chosen contact details
            conn.execute("""
                UPDATE customers SET email=?, phone=?, address=?, suburb=?
                WHERE id=?
            """, (final_email, final_phone, final_address, final_suburb, target_id))

            # Move all source jobs to target and sync denormalised fields
            conn.execute("""
                UPDATE jobs
                SET customer_id=?, customer_name=?, customer_email=?,
                    customer_phone=?, suburb=?
                WHERE customer_id=?
            """, (target_id, tgt['name'], final_email, final_phone, final_suburb, customer_id))

            conn.execute("DELETE FROM customers WHERE id=?", (customer_id,))
            conn.commit()

        flash(f'{src["name"]} merged into {tgt["name"]}.', 'success')
        return redirect(url_for('customers.edit_customer', customer_id=target_id))

    return render_template('customers/merge.html', source=source)


@customers_bp.route('/customers/<int:customer_id>/edit', methods=['GET', 'POST'])
def edit_customer(customer_id):
    with get_db() as conn:
        customer = conn.execute(
            "SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not customer:
            return "Customer not found", 404
        jobs = conn.execute("""
            SELECT j.*, r.name as region_name,
                   COALESCE((SELECT SUM(quantity*unit_cost)
                             FROM job_parts WHERE job_id=j.id), 0) as total
            FROM jobs j JOIN regions r ON j.region_id=r.id
            WHERE j.customer_id=?
            ORDER BY j.scheduled_date DESC, j.id DESC
        """, (customer_id,)).fetchall()
        # All email thread messages across all jobs for this customer
        thread_emails = conn.execute("""
            SELECT 'inbound'  as direction,
                   ei.id, ei.imported_at as sent_at,
                   ei.sender  as from_addr,
                   ei.subject, ei.body, ei.status,
                   ei.message_id, j.reference, j.id as job_id
            FROM email_imports ei
            JOIN jobs j ON j.id = ei.job_id
            WHERE j.customer_id = ?
            UNION ALL
            SELECT 'outbound' as direction,
                   er.id, er.sent_at,
                   er.to_address as from_addr,
                   er.subject, er.body, 'sent' as status,
                   er.message_id, j.reference, j.id as job_id
            FROM email_replies er
            JOIN jobs j ON j.id = er.job_id
            WHERE j.customer_id = ?
            ORDER BY sent_at ASC
        """, (customer_id, customer_id)).fetchall()

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        name  = request.form.get('name', '').strip()
        if not email or not name:
            flash('Name and email are required.', 'danger')
            return render_template('customers/form.html',
                                   customer=customer, action='edit', jobs=jobs,
                                   thread_emails=thread_emails)
        with get_db() as conn:
            clash = conn.execute(
                "SELECT id FROM customers WHERE email=? AND id!=?",
                (email, customer_id)).fetchone()
            if clash:
                flash(f'Email {email} is already used by another customer.', 'danger')
                with get_db() as conn:
                    suburbs = get_suburbs(conn)
                return render_template('customers/form.html',
                                       customer=customer, action='edit', jobs=jobs,
                                       suburbs=suburbs)
            conn.execute("""
                UPDATE customers SET name=?, email=?, phone=?, suburb=?, address=?
                WHERE id=?
            """, (name, email,
                  request.form.get('phone', '').strip(),
                  request.form.get('suburb', '').strip(),
                  request.form.get('address', '').strip(),
                  customer_id))
            # Keep denormalised job fields in sync (address intentionally excluded —
            # job address is set at booking time and not overwritten by later customer edits)
            conn.execute("""
                UPDATE jobs SET customer_name=?, customer_email=?,
                    customer_phone=?, suburb=?
                WHERE customer_id=?
            """, (name, email,
                  request.form.get('phone', '').strip(),
                  request.form.get('suburb', '').strip(),
                  customer_id))
            conn.commit()
        flash('Customer updated.', 'success')
        return redirect(url_for('customers.edit_customer', customer_id=customer_id))

    with get_db() as conn:
        suburbs = get_suburbs(conn)
    return render_template('customers/form.html',
                           customer=customer, action='edit', jobs=jobs,
                           thread_emails=thread_emails, suburbs=suburbs)
