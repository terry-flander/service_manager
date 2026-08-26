from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from models import get_db

parts_bp = Blueprint('parts', __name__)


@parts_bp.route('/parts')
def index():
    from flask import session as _sess, request as _req
    user_id     = _sess.get('user_id')
    q           = _req.args.get('q', '').strip()
    type_filter = _req.args.get('type', '').strip()
    if type_filter == 'all':
        type_filter = ''  # 'all' sentinel means no filter

    with get_db() as conn:
        if 'q' in _req.args or 'type' in _req.args:
            # Explicit filter — save and use
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f'parts_search_{user_id}', q))
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f'parts_type_{user_id}', type_filter))
            conn.commit()
        elif not _req.args:  # plain /parts with no params → restore saved
            r_q = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (f'parts_search_{user_id}',)).fetchone()
            r_t = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (f'parts_type_{user_id}',)).fetchone()
            saved_q = r_q['value'] if r_q else ''
            saved_t = r_t['value'] if r_t else ''
            if saved_q or saved_t:
                from flask import redirect as _red, url_for as _url
                return _red(_url('parts.index', q=saved_q or None,
                                 type=saved_t or None))

        conditions, params = [], []
        if q:
            conditions.append(
                "(LOWER(p.name) LIKE LOWER(?) OR "
                "LOWER(COALESCE(p.part_number,'')) LIKE LOWER(?))")
            params += [f'%{q}%', f'%{q}%']
        if type_filter:
            conditions.append("COALESCE(p.part_type,'stock') = ?")
            params.append(type_filter)
        where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
        sql = (
            "SELECT p.*, s.name as supplier_name "
            "FROM parts p "
            "LEFT JOIN suppliers s ON s.id = p.supplier_id "
            + where +
            " ORDER BY p.name")
        rows = conn.execute(sql, params).fetchall()
        # Calculate qty_on_hand from inventory_transactions if table exists
        try:
            qty_map = {r[0]: r[1] for r in conn.execute(
                "SELECT part_id, COALESCE(SUM(quantity),0) "
                "FROM inventory_transactions GROUP BY part_id").fetchall()}
        except Exception:
            qty_map = {}
        parts = []
        for r in rows:
            d = dict(r)
            d['qty_on_hand'] = qty_map.get(d['id'], 0)
            parts.append(d)
        suppliers = conn.execute(
            "SELECT id, name FROM suppliers WHERE active=1 ORDER BY name"
        ).fetchall()

    return render_template('parts/index.html',
                           parts=parts, q=q,
                           type_filter=type_filter,
                           suppliers=suppliers)

@parts_bp.route('/parts/new', methods=['GET', 'POST'])
def new_part():
    if request.method == 'POST':
        with get_db() as conn:
            conn.execute(
                "INSERT INTO parts (name, part_number, unit_cost, unit, active) VALUES (?, ?, ?, ?, 1)",
                (request.form['name'], request.form.get('part_number', ''),
                 float(request.form['unit_cost']), request.form.get('unit', 'each')))
            conn.commit()
        flash(f'Part "{request.form["name"]}" added to master list.', 'success')
        return redirect(url_for('parts.index'))
    return render_template('parts/new.html')


@parts_bp.route('/parts/<int:part_id>/edit', methods=['GET', 'POST'])
def edit_part(part_id):
    with get_db() as conn:
        part = conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()
    if not part:
        return "Part not found", 404
    if request.method == 'POST':
        active    = 1 if 'active' in request.form else 0
        part_type = request.form.get('part_type', 'stock')
        with get_db() as conn:
            conn.execute(
                "UPDATE parts SET name=?, part_number=?, unit_cost=?, unit=?, active=?, part_type=? WHERE id=?",
                (request.form['name'], request.form.get('part_number', ''),
                 float(request.form['unit_cost']), request.form.get('unit', 'each'),
                 active, part_type, part_id))
            conn.commit()
        flash('Part updated.', 'success')
        return redirect(url_for('parts.index'))
    return render_template('parts/edit.html', part=part)


@parts_bp.route('/parts/<int:part_id>/delete', methods=['POST'])
def delete_part(part_id):
    with get_db() as conn:
        name = conn.execute("SELECT name FROM parts WHERE id=?", (part_id,)).fetchone()['name']
        conn.execute("UPDATE parts SET active=0 WHERE id=?", (part_id,))
        conn.commit()
    flash(f'Part "{name}" deactivated.', 'success')
    return redirect(url_for('parts.index'))


@parts_bp.route('/parts/<int:part_id>/destroy', methods=['POST'])
def destroy_part(part_id):
    """Permanently delete a part. Nullifies job_parts.part_id first to preserve history."""
    with get_db() as conn:
        part = conn.execute("SELECT name FROM parts WHERE id=?", (part_id,)).fetchone()
        if not part:
            flash('Part not found.', 'danger')
            return redirect(url_for('parts.index'))
        conn.execute("UPDATE job_parts SET part_id=NULL WHERE part_id=?", (part_id,))
        conn.execute("DELETE FROM parts WHERE id=?", (part_id,))
        conn.commit()
    flash(f'Part "{part["name"]}" permanently deleted. Job history preserved.', 'success')
    return redirect(url_for('parts.index'))


@parts_bp.route('/parts/search')
def search():
    """
    Live search endpoint for the Add Part selector on job detail.
    ?q=<term>  — matches name or part_number (case-insensitive, partial).
    Returns JSON list of matching active parts.
    """
    q = request.args.get('q', '').strip()
    if len(q) < 1:
        return jsonify([])
    like = f'%{q}%'
    with get_db() as conn:
        parts = conn.execute("""
            SELECT id, name, part_number, unit_cost
            FROM parts
            WHERE active = 1
              AND (name LIKE ? OR part_number LIKE ?)
              AND part_number NOT LIKE 'SR-%'
              AND COALESCE(part_type, 'stock') != 'ad_hoc'
            ORDER BY
              CASE WHEN LOWER(name) LIKE LOWER(?) THEN 0 ELSE 1 END,
              name
            LIMIT 20
        """, (like, like, f'{q}%')).fetchall()
    return jsonify([{
        'id':          p['id'],
        'name':        p['name'],
        'part_number': p['part_number'] or '',
        'unit_cost':   p['unit_cost'],
        'label':       f"{p['name']}{' (' + p['part_number'] + ')' if p['part_number'] else ''} — ${p['unit_cost']:.2f}",
    } for p in parts])


@parts_bp.route('/parts/quick-add', methods=['POST'])
def quick_add():
    """Create a part quickly from the PO import popup. Returns JSON."""
    from flask import jsonify
    name      = request.form.get('name', '').strip()
    number    = request.form.get('part_number', '').strip() or None
    cost      = float(request.form.get('unit_cost', 0) or 0)
    part_type = request.form.get('part_type', 'stock')
    if not name:
        return jsonify({'ok': False, 'error': 'Name is required'}), 400
    with get_db() as conn:
        try:
            conn.execute("""
                INSERT INTO parts (name, part_number, unit_cost, unit, active, part_type)
                VALUES (?, ?, ?, 'each', 1, ?)
            """, (name, number, cost, part_type))
            conn.commit()
            new_id = conn.execute(
                "SELECT id FROM parts WHERE name=? ORDER BY id DESC LIMIT 1",
                (name,)).fetchone()['id']
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'id': new_id, 'name': name,
                    'unit_cost': cost, 'part_number': number or ''})


@parts_bp.route('/parts/<int:part_id>/reactivate', methods=['POST'])
def reactivate_part(part_id):
    with get_db() as conn:
        name = conn.execute("SELECT name FROM parts WHERE id=?", (part_id,)).fetchone()
        conn.execute("UPDATE parts SET active=1 WHERE id=?", (part_id,))
        conn.commit()
    if name:
        flash(f'"{name["name"]}" reactivated.', 'success')
    return redirect(url_for('parts.index'))

@parts_bp.route('/parts/clear-search', methods=['GET','POST'])
def clear_search():
    from flask import session as _sess
    user_id = _sess.get('user_id')
    if user_id:
        from models import get_db
        with get_db() as conn:
            conn.execute("DELETE FROM settings WHERE key IN (?,?,?)",
                         (f'parts_search_{user_id}', f'parts_inactive_{user_id}',
                          f'parts_type_{user_id}'))
            conn.commit()
    from flask import redirect, url_for
    return redirect(url_for('parts.index'))


@parts_bp.route('/parts/<int:part_id>/save-field', methods=['POST'])
def save_field(part_id):
    """Inline field save from parts list."""
    from flask import jsonify
    data  = request.get_json() or {}
    field = data.get('field')
    value = data.get('value')
    allowed = {'name', 'part_number', 'unit_cost', 'part_type',
               'avg_cost_inc_gst', 'reorder_point', 'reorder_qty', 'supplier_id'}
    if field not in allowed:
        return jsonify({'ok': False, 'error': 'Invalid field'}), 400
    numeric = {'unit_cost', 'avg_cost_inc_gst', 'reorder_point', 'reorder_qty'}
    with get_db() as conn:
        if field in numeric:
            v = float(value) if value not in (None, '') else 0
        elif field == 'supplier_id':
            v = int(value) if value not in (None, '', '0') else None
        else:
            v = str(value).strip() if value else None
        conn.execute(f"UPDATE parts SET {field}=? WHERE id=?", (v, part_id))
        conn.commit()
    return jsonify({'ok': True})


@parts_bp.route('/parts/<int:part_id>/merge', methods=['GET', 'POST'])
def merge_part(part_id):
    """Merge this part into another. Source is deactivated, all job_parts
    references updated to target. Weighted average cost recalculated."""
    with get_db() as conn:
        source = conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()
        if not source:
            return "Part not found", 404

        if request.method == 'POST':
            target_id = request.form.get('target_id', type=int)
            keep_name   = request.form.get('keep_name',   'target')
            keep_number = request.form.get('keep_number', 'target')
            keep_cost   = request.form.get('keep_cost',   'target')

            if not target_id or target_id == part_id:
                flash('Please select a valid target part.', 'danger')
                return redirect(url_for('parts.merge_part', part_id=part_id))

            target = conn.execute("SELECT * FROM parts WHERE id=?", (target_id,)).fetchone()
            if not target:
                flash('Target part not found.', 'danger')
                return redirect(url_for('parts.merge_part', part_id=part_id))

            src = dict(source)
            tgt = dict(target)

            final_name   = src['name']        if keep_name   == 'source' else tgt['name']
            final_number = src['part_number'] if keep_number == 'source' else tgt['part_number']
            final_cost   = src['unit_cost']   if keep_cost   == 'source' else tgt['unit_cost']

            # Recalculate weighted avg cost from both parts
            tgt_qty  = conn.execute(
                "SELECT COUNT(*) FROM job_parts WHERE part_id=?",
                (target_id,)).fetchone()[0]
            src_qty  = conn.execute(
                "SELECT COUNT(*) FROM job_parts WHERE part_id=?",
                (part_id,)).fetchone()[0]
            total_qty = tgt_qty + src_qty
            if total_qty > 0:
                src_avg = src['avg_cost_inc_gst'] or src['unit_cost'] or 0
                tgt_avg = tgt['avg_cost_inc_gst'] or tgt['unit_cost'] or 0
                new_avg = ((tgt_avg * tgt_qty) + (src_avg * src_qty)) / total_qty
            else:
                new_avg = tgt['avg_cost_inc_gst'] or tgt['unit_cost'] or 0

            # Update target
            conn.execute("""
                UPDATE parts SET name=?, part_number=?, unit_cost=?,
                                 avg_cost_inc_gst=?
                WHERE id=?
            """, (final_name, final_number, final_cost, new_avg, target_id))

            # Reroute all job_parts references
            conn.execute(
                "UPDATE job_parts SET part_id=? WHERE part_id=?",
                (target_id, part_id))

            # Deactivate source
            conn.execute(
                "UPDATE parts SET active=0 WHERE id=?", (part_id,))

            conn.commit()

            flash(f'"{src["name"]}" merged into "{tgt["name"]}".', 'success')
            return redirect(url_for('parts.edit_part', part_id=target_id))

        # GET — show merge form with part search
        candidates = conn.execute("""
            SELECT id, name, part_number, unit_cost, part_type
            FROM parts
            WHERE id != ? AND active = 1
            ORDER BY name
        """, (part_id,)).fetchall()

    return render_template('parts/merge.html',
                           source=dict(source),
                           candidates=[dict(c) for c in candidates])
