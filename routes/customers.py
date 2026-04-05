
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

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        name  = request.form.get('name', '').strip()
        if not email or not name:
            flash('Name and email are required.', 'danger')
            return render_template('customers/form.html',
                                   customer=customer, action='edit', jobs=jobs)
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
                           suburbs=suburbs)
