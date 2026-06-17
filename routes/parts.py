from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from models import get_db

parts_bp = Blueprint('parts', __name__)


@parts_bp.route('/parts')
def index():
    from flask import session as _sess, request as _req
    user_id = _sess.get('user_id')
    q             = _req.args.get('q', '').strip()
    show_inactive = _req.args.get('inactive', '')  # 'on' | 'off' | '' (not set)

    with get_db() as conn:
        # ── Persist / restore user prefs ──────────────────────────────────────
        if 'q' in _req.args or 'inactive' in _req.args:
            # User just submitted — save current values
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f'parts_search_{user_id}', q))
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f'parts_inactive_{user_id}', show_inactive or 'off'))
            conn.commit()
        else:
            # First load — restore saved prefs
            r_q = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (f'parts_search_{user_id}',)).fetchone()
            r_i = conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (f'parts_inactive_{user_id}',)).fetchone()
            saved_q        = r_q['value'] if r_q else ''
            saved_inactive = r_i['value'] if r_i else 'off'
            if saved_q or saved_inactive == 'on':
                from flask import redirect as _red, url_for as _url
                return _red(_url('parts.index',
                                 q=saved_q,
                                 inactive='on' if saved_inactive == 'on' else None))

        # ── Build query ───────────────────────────────────────────────────────
        conditions, params = [], []
        if not (show_inactive == 'on'):
            conditions.append("active = 1")
        if q:
            conditions.append("(LOWER(name) LIKE LOWER(?) OR LOWER(COALESCE(part_number,'')) LIKE LOWER(?))")
            params += [f'%{q}%', f'%{q}%']
        where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
        parts = conn.execute(
            f"SELECT * FROM parts {where} ORDER BY name",
            params).fetchall()

    return render_template('parts/index.html',
                           parts=parts, q=q,
                           show_inactive=(show_inactive == 'on'))


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
        active = 1 if 'active' in request.form else 0
        with get_db() as conn:
            conn.execute(
                "UPDATE parts SET name=?, part_number=?, unit_cost=?, unit=?, active=? WHERE id=?",
                (request.form['name'], request.form.get('part_number', ''),
                 float(request.form['unit_cost']), request.form.get('unit', 'each'),
                 active, part_id))
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


@parts_bp.route('/parts/<int:part_id>/reactivate', methods=['POST'])
def reactivate_part(part_id):
    with get_db() as conn:
        name = conn.execute("SELECT name FROM parts WHERE id=?", (part_id,)).fetchone()
        conn.execute("UPDATE parts SET active=1 WHERE id=?", (part_id,))
        conn.commit()
    if name:
        flash(f'"{name["name"]}" reactivated.', 'success')
    return redirect(url_for('parts.index'))

@parts_bp.route('/parts/clear-search', methods=['POST'])
def clear_search():
    from flask import session as _sess
    user_id = _sess.get('user_id')
    if user_id:
        from models import get_db
        with get_db() as conn:
            conn.execute("DELETE FROM settings WHERE key IN (?,?)",
                         (f'parts_search_{user_id}', f'parts_inactive_{user_id}'))
            conn.commit()
    from flask import redirect, url_for
    return redirect(url_for('parts.index'))
