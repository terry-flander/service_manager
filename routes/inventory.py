"""
routes/inventory.py — Parts inventory management.

Covers: service_types, locations, suppliers, purchase_orders.
Parts list/edit remain in routes/parts.py.
"""
import logging
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, jsonify, session)
from models import get_db

inventory_bp = Blueprint('inventory', __name__)
log = logging.getLogger('app')


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_service_types(conn):
    return conn.execute(
        "SELECT st.*, p.name as part_name FROM service_types st "
        "LEFT JOIN parts p ON p.id = st.part_id "
        "ORDER BY st.sort_order, st.label").fetchall()


# ── Service Types ─────────────────────────────────────────────────────────────

@inventory_bp.route('/inventory/service-types')
def service_types():
    with get_db() as conn:
        types = [dict(r) for r in _get_service_types(conn)]
        parts = [dict(r) for r in conn.execute(
            "SELECT id, name, part_number FROM parts "
            "WHERE active=1 AND part_type IN ('stock','service') "
            "ORDER BY name").fetchall()]
    return render_template('inventory/service_types.html',
                           types=types, parts=parts)


@inventory_bp.route('/inventory/service-types/new', methods=['POST'])
def service_type_new():
    code      = request.form.get('code', '').strip().lower().replace(' ', '_')
    label     = request.form.get('label', '').strip()
    desc      = request.form.get('description', '').strip()
    keywords  = request.form.get('keywords', '').strip()
    part_id   = request.form.get('part_id') or None
    sort_ord  = int(request.form.get('sort_order', 0) or 0)
    job_group = request.form.get('job_group', 'booking')

    if not code or not label:
        flash('Code and label are required.', 'danger')
        return redirect(url_for('inventory.service_types'))

    with get_db() as conn:
        try:
            conn.execute("""
                INSERT INTO service_types
                    (code, label, description, keywords, part_id, sort_order, job_group)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (code, label, desc, keywords,
                  int(part_id) if part_id else None, sort_ord, job_group))
            conn.commit()
            flash(f'Service type "{label}" added.', 'success')
        except Exception as e:
            flash(f'Error: {e}', 'danger')
    return redirect(url_for('inventory.service_types'))


@inventory_bp.route('/inventory/service-types/<int:st_id>/edit', methods=['POST'])
def service_type_edit(st_id):
    label     = request.form.get('label', '').strip()
    desc      = request.form.get('description', '').strip()
    keywords  = request.form.get('keywords', '').strip()
    part_id   = request.form.get('part_id') or None
    sort_ord  = int(request.form.get('sort_order', 0) or 0)
    active    = 1 if request.form.get('active') else 0
    job_group = request.form.get('job_group', 'booking')

    with get_db() as conn:
        conn.execute("""
            UPDATE service_types
            SET label=?, description=?, keywords=?, part_id=?, sort_order=?, active=?, job_group=?
            WHERE id=?
        """, (label, desc, keywords,
              int(part_id) if part_id else None,
              sort_ord, active, job_group, st_id))
        conn.commit()
    flash('Service type updated.', 'success')
    return redirect(url_for('inventory.service_types'))


@inventory_bp.route('/inventory/service-types/<int:st_id>/delete', methods=['POST'])
def service_type_delete(st_id):
    with get_db() as conn:
        conn.execute("DELETE FROM service_types WHERE id=?", (st_id,))
        conn.commit()
    flash('Service type deleted.', 'success')
    return redirect(url_for('inventory.service_types'))


# ── Locations ─────────────────────────────────────────────────────────────────

@inventory_bp.route('/inventory/locations')
def locations():
    with get_db() as conn:
        locs = [dict(r) for r in conn.execute(
            "SELECT * FROM locations ORDER BY name").fetchall()]
    return render_template('inventory/locations.html', locations=locs)


@inventory_bp.route('/inventory/locations/new', methods=['POST'])
def location_new():
    name     = request.form.get('name', '').strip()
    job_type = request.form.get('job_type', '').strip() or None
    if not name:
        flash('Name is required.', 'danger')
        return redirect(url_for('inventory.locations'))
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO locations (name, job_type) VALUES (?, ?)",
                (name, job_type))
            conn.commit()
            flash(f'Location "{name}" added.', 'success')
        except Exception as e:
            flash(f'Error: {e}', 'danger')
    return redirect(url_for('inventory.locations'))


@inventory_bp.route('/inventory/locations/<int:loc_id>/edit', methods=['POST'])
def location_edit(loc_id):
    name     = request.form.get('name', '').strip()
    job_type = request.form.get('job_type', '').strip() or None
    active   = 1 if request.form.get('active') else 0
    with get_db() as conn:
        conn.execute(
            "UPDATE locations SET name=?, job_type=?, active=? WHERE id=?",
            (name, job_type, active, loc_id))
        conn.commit()
    flash('Location updated.', 'success')
    return redirect(url_for('inventory.locations'))


@inventory_bp.route('/inventory/locations/<int:loc_id>/delete', methods=['POST'])
def location_delete(loc_id):
    with get_db() as conn:
        conn.execute("DELETE FROM locations WHERE id=?", (loc_id,))
        conn.commit()
    flash('Location deleted.', 'success')
    return redirect(url_for('inventory.locations'))


# ── Suppliers ─────────────────────────────────────────────────────────────────

@inventory_bp.route('/inventory/suppliers')
def suppliers():
    with get_db() as conn:
        sups = conn.execute(
            "SELECT * FROM suppliers ORDER BY name").fetchall()
    return render_template('inventory/suppliers.html', suppliers=sups)


@inventory_bp.route('/inventory/suppliers/new', methods=['GET', 'POST'])
def supplier_new():
    if request.method == 'POST':
        with get_db() as conn:
            conn.execute("""
                INSERT INTO suppliers (name, email, phone, website, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (request.form.get('name', '').strip(),
                  request.form.get('email', '').strip().lower() or None,
                  request.form.get('phone', '').strip() or None,
                  request.form.get('website', '').strip() or None,
                  request.form.get('notes', '').strip() or None))
            conn.commit()
        flash('Supplier added.', 'success')
        return redirect(url_for('inventory.suppliers'))
    return render_template('inventory/supplier_form.html',
                           supplier=None, action='new')


@inventory_bp.route('/inventory/suppliers/<int:sup_id>/edit', methods=['GET', 'POST'])
def supplier_edit(sup_id):
    with get_db() as conn:
        supplier = conn.execute(
            "SELECT * FROM suppliers WHERE id=?", (sup_id,)).fetchone()
        if not supplier:
            flash('Supplier not found.', 'danger')
            return redirect(url_for('inventory.suppliers'))
        if request.method == 'POST':
            conn.execute("""
                UPDATE suppliers
                SET name=?, email=?, phone=?, website=?, notes=?, active=?
                WHERE id=?
            """, (request.form.get('name', '').strip(),
                  request.form.get('email', '').strip().lower() or None,
                  request.form.get('phone', '').strip() or None,
                  request.form.get('website', '').strip() or None,
                  request.form.get('notes', '').strip() or None,
                  1 if request.form.get('active') else 0,
                  sup_id))
            conn.commit()
            flash('Supplier updated.', 'success')
            return redirect(url_for('inventory.suppliers'))
        orders = conn.execute("""
            SELECT po.id, po.order_date, po.reference, po.status,
                   po.freight_inc_gst,
                   COUNT(pol.id) as line_count,
                   COALESCE(SUM(pol.price_inc_gst * pol.qty_ordered), 0) as lines_total
            FROM purchase_orders po
            LEFT JOIN purchase_order_lines pol ON pol.order_id = po.id
            WHERE po.supplier_id = ?
            GROUP BY po.id
            ORDER BY po.order_date DESC, po.id DESC
        """, (sup_id,)).fetchall()
    return render_template('inventory/supplier_form.html',
                           supplier=dict(supplier),
                           orders=[dict(o) for o in orders],
                           action='edit')

@inventory_bp.route('/inventory/suppliers/<int:sup_id>/delete', methods=['POST'])
def supplier_delete(sup_id):
    with get_db() as conn:
        conn.execute("DELETE FROM suppliers WHERE id=?", (sup_id,))
        conn.commit()
    flash('Supplier deleted.', 'success')
    return redirect(url_for('inventory.suppliers'))


# ── Purchase Orders ───────────────────────────────────────────────────────────

@inventory_bp.route('/inventory/purchase-orders')
def purchase_orders():
    with get_db() as conn:
        orders = conn.execute("""
            SELECT po.*, s.name as supplier_name,
                   COUNT(pol.id) as line_count,
                   COALESCE(SUM(pol.price_inc_gst * pol.qty_ordered), 0) as lines_total
            FROM purchase_orders po
            JOIN suppliers s ON s.id = po.supplier_id
            LEFT JOIN purchase_order_lines pol ON pol.order_id = po.id
            GROUP BY po.id
            ORDER BY po.order_date DESC, po.id DESC
        """).fetchall()
        suppliers = conn.execute(
            "SELECT id, name FROM suppliers WHERE active=1 ORDER BY name").fetchall()
    return render_template('inventory/purchase_orders.html',
                           orders=orders, suppliers=suppliers)


@inventory_bp.route('/inventory/purchase-orders/new', methods=['GET', 'POST'])
def purchase_order_new():
    with get_db() as conn:
        suppliers = conn.execute(
            "SELECT id, name FROM suppliers WHERE active=1 ORDER BY name").fetchall()
        if request.method == 'POST':
            sup_id   = int(request.form.get('supplier_id'))
            ord_date = request.form.get('order_date', '').strip()
            ref      = request.form.get('reference', '').strip() or None
            freight  = float(request.form.get('freight_inc_gst', 0) or 0)
            notes    = request.form.get('notes', '').strip() or None
            conn.execute("""
                INSERT INTO purchase_orders
                    (supplier_id, order_date, reference, freight_inc_gst, notes, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (sup_id, ord_date, ref, freight, notes, session.get('user_id')))
            conn.commit()
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Add checked supplier parts as order lines
            i = 0
            while True:
                part_id_val = request.form.get(f'sp_id_{i}')
                if part_id_val is None and i > 50:
                    break
                if part_id_val is None:
                    i += 1
                    continue
                qty = float(request.form.get(f'sp_qty_{i}', 0) or 0)
                if qty > 0:
                    sp = conn.execute("""
                        SELECT p.name, p.part_number, sp.supplier_sku,
                               sp.supplier_description, sp.last_price_inc_gst, p.unit_cost
                        FROM supplier_parts sp
                        JOIN parts p ON p.id = sp.part_id
                        WHERE sp.part_id=? AND sp.supplier_id=?
                        LIMIT 1
                    """, (int(part_id_val), sup_id)).fetchone()
                    if sp:
                        price = sp['last_price_inc_gst'] or sp['unit_cost'] or 0
                        conn.execute("""
                            INSERT INTO purchase_order_lines
                                (order_id, part_id, supplier_sku, supplier_desc,
                                 qty_ordered, price_inc_gst)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (new_id, int(part_id_val),
                              sp['supplier_sku'] or '',
                              sp['supplier_description'] or sp['name'],
                              qty, price))
                i += 1
            conn.commit()

            flash('Purchase order created.', 'success')
            return redirect(url_for('inventory.purchase_order_detail', po_id=new_id))
    return render_template('inventory/purchase_order_form.html',
                           suppliers=suppliers, order=None,
                           preselect_supplier=request.args.get('supplier_id', ''),
                           today=__import__('datetime').date.today().isoformat())


@inventory_bp.route('/inventory/purchase-orders/<int:po_id>')
def purchase_order_detail(po_id):
    with get_db() as conn:
        order = conn.execute("""
            SELECT po.*, s.name as supplier_name
            FROM purchase_orders po
            JOIN suppliers s ON s.id = po.supplier_id
            WHERE po.id=?
        """, (po_id,)).fetchone()
        if not order:
            flash('Order not found.', 'danger')
            return redirect(url_for('inventory.purchase_orders'))
        lines = conn.execute("""
            SELECT pol.*, p.name as part_name
            FROM purchase_order_lines pol
            LEFT JOIN parts p ON p.id = pol.part_id
            WHERE pol.order_id=?
            ORDER BY pol.id
        """, (po_id,)).fetchall()
        parts = conn.execute(
            "SELECT id, name, part_number, unit_cost FROM parts "
            "WHERE active=1 ORDER BY name").fetchall()
        locations = conn.execute(
            "SELECT id, name FROM locations WHERE active=1 ORDER BY name").fetchall()
    return render_template('inventory/purchase_order_detail.html',
                           order=dict(order),
                           lines=[dict(l) for l in lines],
                           parts=parts, locations=locations)


@inventory_bp.route('/inventory/purchase-orders/<int:po_id>/edit', methods=['POST'])
def purchase_order_edit(po_id):
    with get_db() as conn:
        conn.execute("""
            UPDATE purchase_orders
            SET reference=?, order_date=?, status=?, freight_inc_gst=?, notes=?
            WHERE id=?
        """, (request.form.get('reference', '').strip() or None,
              request.form.get('order_date', '').strip(),
              request.form.get('status', 'open'),
              float(request.form.get('freight_inc_gst', 0) or 0),
              request.form.get('notes', '').strip() or None,
              po_id))
        conn.commit()
    flash('Order updated.', 'success')
    return redirect(url_for('inventory.purchase_order_detail', po_id=po_id))


@inventory_bp.route('/inventory/purchase-orders/<int:po_id>/lines/add', methods=['POST'])
def purchase_order_line_add(po_id):
    with get_db() as conn:
        part_id  = request.form.get('part_id') or None
        conn.execute("""
            INSERT INTO purchase_order_lines
                (order_id, part_id, supplier_sku, supplier_desc,
                 qty_ordered, price_inc_gst, location_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (po_id,
              int(part_id) if part_id else None,
              request.form.get('supplier_sku', '').strip() or None,
              request.form.get('supplier_desc', '').strip(),
              float(request.form.get('qty_ordered', 1) or 1),
              float(request.form.get('price_inc_gst', 0) or 0),
              int(request.form.get('location_id')) if request.form.get('location_id') else None))
        conn.commit()
    return redirect(url_for('inventory.purchase_order_detail', po_id=po_id))


@inventory_bp.route('/inventory/purchase-orders/<int:po_id>/lines/<int:line_id>/edit', methods=['POST'])
def purchase_order_line_edit(po_id, line_id):
    with get_db() as conn:
        old_line = conn.execute(
            "SELECT part_id FROM purchase_order_lines WHERE id=? AND order_id=?",
            (line_id, po_id)).fetchone()

        part_id = request.form.get('part_id') or None
        conn.execute("""
            UPDATE purchase_order_lines
            SET part_id=?, supplier_sku=?, supplier_desc=?,
                qty_ordered=?, price_inc_gst=?
            WHERE id=? AND order_id=?
        """, (int(part_id) if part_id else None,
              request.form.get('supplier_sku','').strip() or None,
              request.form.get('supplier_desc','').strip(),
              float(request.form.get('qty_ordered',1) or 1),
              float(request.form.get('price_inc_gst',0) or 0),
              line_id, po_id))

        # If part_id changed, update any inventory_transactions that
        # reference this PO line so the parts-by-supplier report stays correct
        if old_line and str(old_line['part_id']) != str(part_id or ''):
            conn.execute("""
                UPDATE inventory_transactions
                SET part_id=?
                WHERE source_type='order' AND source_id=? AND part_id IS ?
            """, (int(part_id) if part_id else None,
                  po_id,
                  old_line['part_id']))

            # Update supplier_parts mapping if we have a SKU to key on
            sku = request.form.get('supplier_sku', '').strip() or None
            if part_id and sku:
                # Get supplier_id from the order
                order = conn.execute(
                    "SELECT supplier_id FROM purchase_orders WHERE id=?",
                    (po_id,)).fetchone()
                if order:
                    conn.execute("""
                        INSERT INTO supplier_parts
                            (supplier_id, part_id, supplier_sku,
                             supplier_description, last_price_inc_gst)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(supplier_id, supplier_sku) DO UPDATE SET
                            part_id              = excluded.part_id,
                            supplier_description = excluded.supplier_description,
                            last_price_inc_gst   = excluded.last_price_inc_gst
                    """, (order['supplier_id'], int(part_id), sku,
                          request.form.get('supplier_desc', '').strip(),
                          float(request.form.get('price_inc_gst', 0) or 0)))

        conn.commit()
    flash('Line updated.', 'success')
    return redirect(url_for('inventory.purchase_order_detail', po_id=po_id))


@inventory_bp.route('/inventory/purchase-orders/<int:po_id>/lines/<int:line_id>/delete', methods=['POST'])
def purchase_order_line_delete(po_id, line_id):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM purchase_order_lines WHERE id=? AND order_id=?",
            (line_id, po_id))
        conn.commit()
    return redirect(url_for('inventory.purchase_order_detail', po_id=po_id))


@inventory_bp.route('/inventory/purchase-orders/<int:po_id>/import', methods=['GET', 'POST'])
def purchase_order_import(po_id):
    """Parse pasted text into PO lines for preview/confirm."""
    with get_db() as conn:
        order = conn.execute("""
            SELECT po.*, s.name as supplier_name, s.id as supplier_id
            FROM purchase_orders po
            JOIN suppliers s ON s.id = po.supplier_id
            WHERE po.id=?
        """, (po_id,)).fetchone()
        if not order:
            flash('Order not found.', 'danger')
            return redirect(url_for('inventory.purchase_orders'))
        parts = conn.execute(
            "SELECT id, name, part_number FROM parts WHERE active=1 ORDER BY name"
        ).fetchall()
        locations = conn.execute(
            "SELECT id, name FROM locations WHERE active=1 ORDER BY name"
        ).fetchall()

    if request.method == 'POST':
        action = request.form.get('action', 'parse')

        if action == 'parse':
            parsed, fmt = [], ''

            # PDF upload takes priority over pasted text
            pdf_file = request.files.get('import_pdf')
            if pdf_file and pdf_file.filename:
                from po_parser import parse_pdf as po_parse_pdf
                parsed, fmt = po_parse_pdf(pdf_file.read())
                text = f'[PDF: {pdf_file.filename}]'
                if not parsed:
                    flash(f'Could not extract lines from PDF (format: {fmt}). '
                          'Try pasting the text instead.', 'warning')
                    return redirect(url_for('inventory.purchase_order_import', po_id=po_id))
            else:
                text = request.form.get('import_text', '').strip()
                if not text:
                    flash('No text or file to parse.', 'danger')
                    return redirect(url_for('inventory.purchase_order_import', po_id=po_id))
                from po_parser import parse as po_parse
                parsed, fmt = po_parse(text)

            with get_db() as conn:
                sku_map = {}
                for r in conn.execute(
                    "SELECT id, part_number FROM parts WHERE part_number IS NOT NULL AND active=1"
                ).fetchall():
                    sku_map[r['part_number']] = r['id']
                name_map = {r['name'].lower(): r['id'] for r in conn.execute(
                    "SELECT id, name FROM parts WHERE active=1").fetchall()}
                sup_sku_map = {r['supplier_sku']: r['part_id'] for r in conn.execute(
                    "SELECT supplier_sku, part_id FROM supplier_parts WHERE supplier_id=?",
                    (order['supplier_id'],)).fetchall()}
                id_name_map = {r['id']: r['name'] for r in conn.execute(
                    "SELECT id, name FROM parts WHERE active=1").fetchall()}

            for line in parsed:
                matched = None
                if line['supplier_sku']:
                    matched = (sup_sku_map.get(line['supplier_sku']) or
                               sku_map.get(line['supplier_sku']))
                if not matched:
                    matched = name_map.get(line['supplier_desc'].lower())
                line['matched_part_id'] = matched or 0
                line['matched_name'] = id_name_map.get(matched, '') if matched else ''
            return render_template('inventory/po_import.html',
                                   order=dict(order),
                                   parsed=parsed,
                                   fmt=fmt,
                                   raw_text=text,
                                   parts=[dict(p) for p in parts],
                                   locations=[dict(l) for l in locations])
        elif action == 'confirm':
            with get_db() as conn:
                idxs = request.form.getlist('line_idx')
                for idx in idxs:
                    part_id = request.form.get(f'part_id_{idx}') or None
                    sku     = request.form.get(f'sku_{idx}', '').strip() or None
                    desc    = request.form.get(f'desc_{idx}', '').strip()
                    qty     = float(request.form.get(f'qty_{idx}', 1) or 1)
                    price   = float(request.form.get(f'price_{idx}', 0) or 0)
                    loc_id  = request.form.get(f'location_{idx}') or None
                    if not desc:
                        continue
                    conn.execute("""
                        INSERT INTO purchase_order_lines
                            (order_id, part_id, supplier_sku, supplier_desc,
                             qty_ordered, price_inc_gst, location_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (po_id, int(part_id) if part_id else None,
                           sku, desc, qty, price,
                           int(loc_id) if loc_id else None))
                    if sku and part_id:
                        try:
                            conn.execute("""
                                INSERT INTO supplier_parts
                                    (supplier_id, part_id, supplier_sku,
                                     supplier_description, last_price_inc_gst,
                                     last_ordered_at)
                                VALUES (?, ?, ?, ?, ?, date('now'))
                                ON CONFLICT(supplier_id, supplier_sku) DO UPDATE SET
                                    part_id=excluded.part_id,
                                    last_price_inc_gst=excluded.last_price_inc_gst,
                                    last_ordered_at=excluded.last_ordered_at
                            """, (order['supplier_id'], int(part_id), sku, desc, price))
                        except Exception:
                            pass
                conn.commit()
            flash(f'{len(idxs)} line(s) imported.', 'success')
            return redirect(url_for('inventory.purchase_order_detail', po_id=po_id))

    return render_template('inventory/po_import.html',
                           order=dict(order), parsed=[], fmt='',
                           raw_text='',
                           parts=[dict(p) for p in parts],
                           locations=[dict(l) for l in locations])



# ── Receive against PO ───────────────────────────────────────────────────────

@inventory_bp.route('/inventory/purchase-orders/<int:po_id>/delete', methods=['POST'])
def purchase_order_delete(po_id):
    with get_db() as conn:
        # Guard: don't delete if any lines have been received
        received = conn.execute("""
            SELECT SUM(qty_received) FROM purchase_order_lines WHERE order_id=?
        """, (po_id,)).fetchone()[0] or 0
        if received > 0:
            flash('Cannot delete — stock has already been received against this order.', 'danger')
            return redirect(url_for('inventory.purchase_order_detail', po_id=po_id))
        conn.execute("DELETE FROM purchase_order_lines WHERE order_id=?", (po_id,))
        conn.execute("DELETE FROM purchase_orders WHERE id=?", (po_id,))
        conn.commit()
    flash('Purchase order deleted.', 'success')
    return redirect(url_for('inventory.purchase_orders'))


@inventory_bp.route('/inventory/purchase-orders/<int:po_id>/receive', methods=['GET', 'POST'])
def purchase_order_receive(po_id):
    """Receive ordered items — update qty_received, create transactions."""
    from datetime import date as _date
    with get_db() as conn:
        order = conn.execute("""
            SELECT po.*, s.name as supplier_name, s.id as supplier_id
            FROM purchase_orders po
            JOIN suppliers s ON s.id = po.supplier_id
            WHERE po.id=?
        """, (po_id,)).fetchone()
        if not order:
            flash('Order not found.', 'danger')
            return redirect(url_for('inventory.purchase_orders'))
        lines = conn.execute("""
            SELECT pol.*, p.name as part_name, p.avg_cost_inc_gst,
                   p.supplier_id as part_supplier_id
            FROM purchase_order_lines pol
            LEFT JOIN parts p ON p.id = pol.part_id
            WHERE pol.order_id=?
            ORDER BY pol.id
        """, (po_id,)).fetchall()
        locations = conn.execute(
            "SELECT id, name FROM locations WHERE active=1 ORDER BY name"
        ).fetchall()
        # Default location — Workshop
        default_loc = conn.execute(
            "SELECT id FROM locations WHERE LOWER(name)='workshop' LIMIT 1"
        ).fetchone()
        default_loc_id = default_loc['id'] if default_loc else (locations[0]['id'] if locations else None)

    if request.method == 'POST':
        today = request.form.get('receive_date') or _date.today().isoformat()
        notes = request.form.get('notes', '').strip()
        user_id = session.get('user_id')

        with get_db() as conn:
            any_received = False
            for line in lines:
                lid = line['id']
                qty_str = request.form.get(f'qty_{lid}', '').strip()
                if not qty_str:
                    continue
                try:
                    qty_recv = float(qty_str)
                except ValueError:
                    continue
                if qty_recv <= 0:
                    continue

                loc_id = request.form.get(f'loc_{lid}') or default_loc_id
                loc_id = int(loc_id) if loc_id else None
                unit_price = line['price_inc_gst']
                part_id = line['part_id']

                # Update line qty_received and location
                conn.execute("""
                    UPDATE purchase_order_lines
                    SET qty_received = COALESCE(qty_received,0) + ?,
                        location_id  = ?
                    WHERE id=?
                """, (qty_recv, loc_id, lid))

                if part_id:
                    # Update avg cost (weighted average)
                    part = conn.execute(
                        "SELECT avg_cost_inc_gst, supplier_id FROM parts WHERE id=?",
                        (part_id,)).fetchone()
                    old_avg = part['avg_cost_inc_gst'] or 0

                    # Current stock from transactions
                    stock_row = conn.execute("""
                        SELECT COALESCE(SUM(quantity),0) as total
                        FROM inventory_transactions WHERE part_id=?
                    """, (part_id,)).fetchone()
                    old_qty = stock_row['total']
                    new_qty = old_qty + qty_recv
                    new_avg = ((old_avg * old_qty) + (unit_price * qty_recv)) / new_qty if new_qty > 0 else unit_price

                    # Create receipt transaction
                    conn.execute("""
                        INSERT INTO inventory_transactions
                            (part_id, transaction_date, type, quantity,
                             unit_price_inc_gst, location_id,
                             source_type, source_id, notes, created_by)
                        VALUES (?, ?, 'receipt', ?, ?, ?, 'order', ?, ?, ?)
                    """, (part_id, today, qty_recv, unit_price,
                          loc_id, po_id, notes or None, user_id))

                    # Update part avg_cost and supplier_id
                    conn.execute("""
                        UPDATE parts
                        SET avg_cost_inc_gst = ?,
                            supplier_id = COALESCE(supplier_id, ?)
                        WHERE id=?
                    """, (round(new_avg, 4), order['supplier_id'], part_id))

                    # Upsert supplier_parts
                    conn.execute("""
                        INSERT INTO supplier_parts
                            (supplier_id, part_id, supplier_sku,
                             supplier_description, last_price_inc_gst, last_ordered_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(supplier_id, supplier_sku) DO UPDATE SET
                            part_id               = excluded.part_id,
                            last_price_inc_gst    = excluded.last_price_inc_gst,
                            last_ordered_at       = excluded.last_ordered_at
                    """, (order['supplier_id'], part_id,
                          line['supplier_sku'] or f'PO{po_id}-L{lid}',
                          line['supplier_desc'], unit_price, today))

                any_received = True

            if any_received:
                # Update order status
                conn.execute("""
                    UPDATE purchase_orders
                    SET status = CASE
                        WHEN (SELECT SUM(qty_received) FROM purchase_order_lines WHERE order_id=?) >=
                             (SELECT SUM(qty_ordered)  FROM purchase_order_lines WHERE order_id=?)
                        THEN 'received' ELSE 'partial' END
                    WHERE id=?
                """, (po_id, po_id, po_id))
                conn.commit()
                flash('Stock received and inventory updated.', 'success')
            else:
                flash('No quantities entered.', 'warning')

        return redirect(url_for('inventory.purchase_order_detail', po_id=po_id))

    return render_template('inventory/po_receive.html',
                           order=dict(order),
                           lines=[dict(l) for l in lines],
                           locations=[dict(l) for l in locations],
                           default_loc_id=default_loc_id,
                           today=_date.today().isoformat())


# ── Supplier parts checklist for new PO ──────────────────────────────────────

@inventory_bp.route('/inventory/suppliers/<int:sup_id>/parts-for-order')
def supplier_parts_for_order(sup_id):
    """Return JSON list of previously ordered / linked parts for supplier."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT
                p.id, p.name, p.part_number, p.unit_cost,
                sp.supplier_sku, sp.supplier_description, sp.last_price_inc_gst
            FROM supplier_parts sp
            JOIN parts p ON p.id = sp.part_id
            WHERE sp.supplier_id = ? AND p.active = 1
            ORDER BY p.name
        """, (sup_id,)).fetchall()
    from flask import jsonify as _json
    return _json([dict(r) for r in rows])


# ── Stock Takes (Inventory Counts) ───────────────────────────────────────────

@inventory_bp.route('/inventory/stock-takes')
def stock_takes():
    with get_db() as conn:
        counts = conn.execute("""
            SELECT ic.*, l.name as location_name,
                   COUNT(icl.id) as line_count,
                   SUM(CASE WHEN icl.qty_counted IS NOT NULL THEN 1 ELSE 0 END) as counted
            FROM inventory_counts ic
            LEFT JOIN locations l ON l.id = ic.location_id
            LEFT JOIN inventory_count_lines icl ON icl.count_id = ic.id
            GROUP BY ic.id
            ORDER BY ic.count_date DESC, ic.id DESC
        """).fetchall()
        locations = conn.execute(
            "SELECT id, name FROM locations WHERE active=1 ORDER BY name").fetchall()
    return render_template('inventory/stock_take_list.html',
                           counts=[dict(c) for c in counts],
                           locations=[dict(l) for l in locations])


@inventory_bp.route('/inventory/stock-takes/new', methods=['POST'])
def stock_take_new():
    from datetime import date as _date
    loc_id = request.form.get('location_id') or None
    notes  = request.form.get('notes', '').strip() or None
    count_date = request.form.get('count_date') or _date.today().isoformat()

    with get_db() as conn:
        conn.execute("""
            INSERT INTO inventory_counts (count_date, location_id, status, notes, created_by)
            VALUES (?, ?, 'open', ?, ?)
        """, (count_date, int(loc_id) if loc_id else None,
              notes, session.get('user_id')))
        conn.commit()
        count_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Build count lines — all stock and ad_hoc parts
        parts = conn.execute("""
            SELECT p.id, p.name, p.part_number,
                   COALESCE((SELECT SUM(it.quantity)
                              FROM inventory_transactions it
                              WHERE it.part_id=p.id
                              AND (? IS NULL OR it.location_id=?)),0) as qty_system
            FROM parts p
            WHERE p.active=1
              AND COALESCE(p.part_type,'stock') IN ('stock','ad_hoc')
            ORDER BY p.name
        """, (int(loc_id) if loc_id else None,
              int(loc_id) if loc_id else None)).fetchall()

        for p in parts:
            conn.execute("""
                INSERT INTO inventory_count_lines
                    (count_id, part_id, location_id, qty_system)
                VALUES (?, ?, ?, ?)
            """, (count_id, p['id'],
                  int(loc_id) if loc_id else None,
                  p['qty_system']))
        conn.commit()

    flash(f'Stock take created with {len(parts)} parts.', 'success')
    return redirect(url_for('inventory.stock_take_detail', count_id=count_id))


@inventory_bp.route('/inventory/stock-takes/<int:count_id>')
def stock_take_detail(count_id):
    with get_db() as conn:
        count = conn.execute("""
            SELECT ic.*, l.name as location_name
            FROM inventory_counts ic
            LEFT JOIN locations l ON l.id = ic.location_id
            WHERE ic.id=?
        """, (count_id,)).fetchone()
        if not count:
            flash('Stock take not found.', 'danger')
            return redirect(url_for('inventory.stock_takes'))
        lines = conn.execute("""
            SELECT icl.*, p.name as part_name, p.part_number, p.part_type
            FROM inventory_count_lines icl
            JOIN parts p ON p.id = icl.part_id
            WHERE icl.count_id=?
            ORDER BY p.name
        """, (count_id,)).fetchall()
    return render_template('inventory/stock_take_detail.html',
                           count=dict(count),
                           lines=[dict(l) for l in lines])


@inventory_bp.route('/inventory/stock-takes/<int:count_id>/save', methods=['POST'])
def stock_take_save(count_id):
    """Save counted quantities without finalising."""
    with get_db() as conn:
        count = conn.execute(
            "SELECT status FROM inventory_counts WHERE id=?", (count_id,)).fetchone()
        if not count or count['status'] == 'finalised':
            flash('Count is already finalised.', 'danger')
            return redirect(url_for('inventory.stock_take_detail', count_id=count_id))

        line_ids = request.form.getlist('line_id')
        for lid in line_ids:
            qty_str = request.form.get(f'qty_{lid}', '').strip()
            if qty_str == '':
                conn.execute(
                    "UPDATE inventory_count_lines SET qty_counted=NULL, adjustment=NULL WHERE id=?",
                    (int(lid),))
            else:
                try:
                    qty_counted = float(qty_str)
                    conn.execute("""
                        UPDATE inventory_count_lines
                        SET qty_counted=?,
                            adjustment=? - qty_system
                        WHERE id=?
                    """, (qty_counted, qty_counted, int(lid)))
                except ValueError:
                    pass
        conn.commit()
    flash('Count saved.', 'success')
    return redirect(url_for('inventory.stock_take_detail', count_id=count_id))


@inventory_bp.route('/inventory/stock-takes/<int:count_id>/finalise', methods=['POST'])
def stock_take_finalise(count_id):
    """Finalise count — create adjustment transactions for checked lines only."""
    from datetime import date as _date
    today = _date.today().isoformat()
    user_id = session.get('user_id')

    with get_db() as conn:
        count = conn.execute(
            "SELECT * FROM inventory_counts WHERE id=?", (count_id,)).fetchone()
        if not count or count['status'] == 'finalised':
            flash('Already finalised.', 'danger')
            return redirect(url_for('inventory.stock_take_detail', count_id=count_id))

        # Only process lines that are checked (included in form)
        included_line_ids = set(int(x) for x in request.form.getlist('include_line'))

        lines = conn.execute(
            "SELECT * FROM inventory_count_lines WHERE count_id=?", (count_id,)).fetchall()

        adj_count = 0
        for line in lines:
            if line['id'] not in included_line_ids:
                continue
            if line['qty_counted'] is None:
                continue
            adjustment = (line['qty_counted'] or 0) - (line['qty_system'] or 0)
            if adjustment == 0:
                continue

            conn.execute("""
                INSERT INTO inventory_transactions
                    (part_id, transaction_date, type, quantity,
                     unit_price_inc_gst, location_id,
                     source_type, source_id, notes, created_by)
                VALUES (?, ?, 'adjustment', ?, NULL, ?, 'count', ?, ?, ?)
            """, (line['part_id'], today, adjustment,
                  line['location_id'], count_id,
                  f'Stock take #{count_id}', user_id))
            adj_count += 1

        conn.execute(
            "UPDATE inventory_counts SET status='finalised' WHERE id=?", (count_id,))
        conn.commit()

    flash(f'Count finalised — {adj_count} adjustment transaction(s) created.', 'success')
    return redirect(url_for('inventory.stock_take_detail', count_id=count_id))


@inventory_bp.route('/inventory/stock-takes/<int:count_id>/delete', methods=['POST'])
def stock_take_delete(count_id):
    with get_db() as conn:
        count = conn.execute(
            "SELECT status FROM inventory_counts WHERE id=?", (count_id,)).fetchone()
        if count and count['status'] == 'finalised':
            flash('Cannot delete a finalised count.', 'danger')
            return redirect(url_for('inventory.stock_take_detail', count_id=count_id))
        conn.execute("DELETE FROM inventory_count_lines WHERE count_id=?", (count_id,))
        conn.execute("DELETE FROM inventory_counts WHERE id=?", (count_id,))
        conn.commit()
    flash('Stock take deleted.', 'success')
    return redirect(url_for('inventory.stock_takes'))


# ── API: service types list (for job forms) ───────────────────────────────────

@inventory_bp.route('/api/service-types')
def api_service_types():
    """Return active service types as JSON for job forms."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT code, label, description, sort_order "
            "FROM service_types WHERE active=1 "
            "ORDER BY sort_order, label").fetchall()
    return jsonify([dict(r) for r in rows])
