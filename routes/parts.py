from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import get_db

parts_bp = Blueprint('parts', __name__)


@parts_bp.route('/parts')
def index():
    with get_db() as conn:
        parts = conn.execute("SELECT * FROM parts ORDER BY name").fetchall()
    return render_template('parts/index.html', parts=parts)


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

