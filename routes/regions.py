from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from models import get_db
import csv, io

regions_bp = Blueprint('regions', __name__)

DATE_STATUSES = ['open', 'pending', 'closed']


# ── Region CRUD ───────────────────────────────────────────────────────────────

@regions_bp.route('/regions')
def index():
    with get_db() as conn:
        regions = conn.execute("""
            SELECT r.*,
                   COUNT(DISTINCT s.id) as suburb_count,
                   COUNT(DISTINCT d.id) as date_count
            FROM regions r
            LEFT JOIN suburbs s ON s.region_id = r.id
            LEFT JOIN region_dates d ON d.region_id = r.id
            GROUP BY r.id
            ORDER BY r.name
        """).fetchall()
    return render_template('regions/index.html', regions=regions)


@regions_bp.route('/regions/new', methods=['GET', 'POST'])
def new_region():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Region name is required.', 'danger')
            return render_template('regions/form.html', region=None)
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM regions WHERE name=?", (name,)).fetchone()
            if existing:
                flash(f'Region "{name}" already exists.', 'danger')
                return render_template('regions/form.html', region=request.form)
            conn.execute(
                "INSERT INTO regions (name, visit_day) VALUES (?, ?)",
                (name, request.form.get('visit_day', 'Monday')))
            conn.commit()
        flash(f'Region "{name}" created.', 'success')
        return redirect(url_for('regions.index'))
    return render_template('regions/form.html', region=None)


@regions_bp.route('/regions/<int:region_id>')
def detail(region_id):
    with get_db() as conn:
        region = conn.execute(
            "SELECT * FROM regions WHERE id=?", (region_id,)).fetchone()
        if not region:
            return "Region not found", 404
        suburbs = conn.execute(
            "SELECT * FROM suburbs WHERE region_id=? ORDER BY name",
            (region_id,)).fetchall()
        dates = conn.execute(
            "SELECT * FROM region_dates WHERE region_id=? ORDER BY date",
            (region_id,)).fetchall()
    return render_template('regions/detail.html', region=region,
                           suburbs=suburbs, dates=dates,
                           DATE_STATUSES=DATE_STATUSES)


@regions_bp.route('/regions/<int:region_id>/edit', methods=['GET', 'POST'])
def edit_region(region_id):
    with get_db() as conn:
        region = conn.execute(
            "SELECT * FROM regions WHERE id=?", (region_id,)).fetchone()
    if not region:
        return "Region not found", 404
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Region name is required.', 'danger')
            return render_template('regions/form.html', region=region)
        with get_db() as conn:
            clash = conn.execute(
                "SELECT id FROM regions WHERE name=? AND id!=?",
                (name, region_id)).fetchone()
            if clash:
                flash(f'Region "{name}" already exists.', 'danger')
                return render_template('regions/form.html', region=region)
            conn.execute(
                "UPDATE regions SET name=?, visit_day=? WHERE id=?",
                (name, request.form.get('visit_day', 'Monday'), region_id))
            conn.commit()
        flash('Region updated.', 'success')
        return redirect(url_for('regions.detail', region_id=region_id))
    return render_template('regions/form.html', region=region)


@regions_bp.route('/regions/<int:region_id>/delete', methods=['POST'])
def delete_region(region_id):
    with get_db() as conn:
        region = conn.execute(
            "SELECT name FROM regions WHERE id=?", (region_id,)).fetchone()
        jobs = conn.execute(
            "SELECT COUNT(*) as n FROM jobs WHERE region_id=?",
            (region_id,)).fetchone()['n']
        if jobs:
            flash(f'Cannot delete — {jobs} job(s) are linked to this region.', 'danger')
            return redirect(url_for('regions.detail', region_id=region_id))
        conn.execute("DELETE FROM regions WHERE id=?", (region_id,))
        conn.commit()
    flash(f'Region "{region["name"]}" deleted.', 'success')
    return redirect(url_for('regions.index'))


# ── Suburb CRUD ───────────────────────────────────────────────────────────────

@regions_bp.route('/regions/<int:region_id>/suburbs/add', methods=['POST'])
def add_suburb(region_id):
    name = request.form.get('suburb_name', '').strip().title()
    if not name:
        flash('Suburb name is required.', 'danger')
        return redirect(url_for('regions.detail', region_id=region_id))
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM suburbs WHERE name=?", (name,)).fetchone()
        if existing:
            flash(f'"{name}" is already assigned to a region.', 'danger')
        else:
            conn.execute(
                "INSERT INTO suburbs (region_id, name) VALUES (?, ?)",
                (region_id, name))
            conn.commit()
            flash(f'Suburb "{name}" added.', 'success')
    return redirect(url_for('regions.detail', region_id=region_id))


@regions_bp.route('/regions/suburbs/<int:suburb_id>/delete', methods=['POST'])
def delete_suburb(suburb_id):
    with get_db() as conn:
        s = conn.execute(
            "SELECT name, region_id FROM suburbs WHERE id=?",
            (suburb_id,)).fetchone()
        if s:
            conn.execute("DELETE FROM suburbs WHERE id=?", (suburb_id,))
            conn.commit()
            flash(f'Suburb "{s["name"]}" removed.', 'success')
    return redirect(url_for('regions.suburbs_index'))


# ── Region date CRUD ──────────────────────────────────────────────────────────

@regions_bp.route('/regions/<int:region_id>/dates/add', methods=['POST'])
def add_date(region_id):
    d      = request.form.get('date', '').strip()
    status = request.form.get('status', 'open')
    if not d:
        flash('Date is required.', 'danger')
        return redirect(url_for('regions.detail', region_id=region_id))
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM region_dates WHERE region_id=? AND date=?",
            (region_id, d)).fetchone()
        if existing:
            flash(f'Date {d} is already scheduled for this region.', 'danger')
        else:
            conn.execute(
                "INSERT INTO region_dates (region_id, date, status) VALUES (?, ?, ?)",
                (region_id, d, status))
            conn.commit()
            flash(f'Date {d} added as {status}.', 'success')
    return redirect(url_for('regions.detail', region_id=region_id))


@regions_bp.route('/regions/dates/<int:date_id>/status', methods=['POST'])
def update_date_status(date_id):
    status = request.form.get('status', 'open')
    with get_db() as conn:
        row = conn.execute(
            "SELECT region_id FROM region_dates WHERE id=?", (date_id,)).fetchone()
        conn.execute(
            "UPDATE region_dates SET status=? WHERE id=?", (status, date_id))
        conn.commit()
    flash(f'Status updated to {status}.', 'success')
    return redirect(url_for('regions.detail', region_id=row['region_id']))


@regions_bp.route('/regions/dates/<int:date_id>/delete', methods=['POST'])
def delete_date(date_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT region_id FROM region_dates WHERE id=?", (date_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM region_dates WHERE id=?", (date_id,))
            conn.commit()
            flash('Date removed.', 'success')
            return redirect(url_for('regions.detail', region_id=row['region_id']))
    return redirect(url_for('regions.index'))



# ── Suburbs management ────────────────────────────────────────────────────────

@regions_bp.route('/suburbs')
def suburbs_index():
    with get_db() as conn:
        suburbs = conn.execute("""
            SELECT s.id, s.name, s.region_id,
                   r.name as region_name,
                   COUNT(j.id) as job_count
            FROM suburbs s
            JOIN regions r ON r.id = s.region_id
            LEFT JOIN jobs j ON LOWER(j.suburb) = LOWER(s.name)
            GROUP BY s.id
            ORDER BY s.name
        """).fetchall()
        regions = conn.execute(
            "SELECT id, name FROM regions ORDER BY name").fetchall()
    return render_template('regions/suburbs.html',
                           suburbs=suburbs, regions=regions)


@regions_bp.route('/suburbs/<int:suburb_id>/edit', methods=['GET', 'POST'])
def edit_suburb(suburb_id):
    with get_db() as conn:
        suburb  = conn.execute("""
            SELECT s.*, r.name as region_name,
                   COUNT(j.id) as job_count
            FROM suburbs s
            JOIN regions r ON r.id = s.region_id
            LEFT JOIN jobs j ON LOWER(j.suburb) = LOWER(s.name)
            WHERE s.id=?
            GROUP BY s.id
        """, (suburb_id,)).fetchone()
        regions = conn.execute(
            "SELECT id, name FROM regions ORDER BY name").fetchall()

    if not suburb:
        return "Suburb not found", 404

    if request.method == 'POST':
        new_name      = request.form.get('name', '').strip().title()
        new_region_id = int(request.form.get('region_id', suburb['region_id']))

        if not new_name:
            flash('Suburb name is required.', 'danger')
            return render_template('regions/suburb_edit.html',
                                   suburb=suburb, regions=regions)

        with get_db() as conn:
            # Check name clash (another suburb with same name)
            clash = conn.execute(
                "SELECT id FROM suburbs WHERE LOWER(name)=LOWER(?) AND id!=?",
                (new_name, suburb_id)).fetchone()
            if clash:
                flash(f'Suburb "{new_name}" already exists.', 'danger')
                return render_template('regions/suburb_edit.html',
                                       suburb=suburb, regions=regions)

            old_name       = suburb['name']
            old_region_id  = suburb['region_id']
            region_changed = new_region_id != old_region_id
            name_changed   = new_name.lower() != old_name.lower()

            conn.execute(
                "UPDATE suburbs SET name=?, region_id=? WHERE id=?",
                (new_name, new_region_id, suburb_id))

            # If region changed, update all jobs that reference this suburb
            if region_changed:
                conn.execute("""
                    UPDATE jobs SET region_id=?
                    WHERE LOWER(suburb) = LOWER(?)
                """, (new_region_id, old_name))
                updated_jobs = conn.execute(
                    "SELECT changes()").fetchone()[0]
            else:
                updated_jobs = 0

            # If name changed, update suburb column on jobs too
            if name_changed:
                conn.execute("""
                    UPDATE jobs SET suburb=?
                    WHERE LOWER(suburb) = LOWER(?)
                """, (new_name, old_name))

            conn.commit()

        msg = f'Suburb "{new_name}" updated.'
        if region_changed and updated_jobs:
            new_region_name = next(
                (r['name'] for r in regions if r['id'] == new_region_id), '')
            msg += f' {updated_jobs} job(s) moved to {new_region_name}.'
        flash(msg, 'success')
        return redirect(url_for('regions.suburbs_index'))

    return render_template('regions/suburb_edit.html',
                           suburb=suburb, regions=regions)

# ── CSV Import ───────────────────────────────────────────────────────────────

@regions_bp.route('/regions/import', methods=['GET', 'POST'])
def import_csv():
    if request.method == 'GET':
        return render_template('regions/import.html')

    # ── Confirm step: rows arrive as hidden fields, no file ───────────────────
    if 'confirm' in request.form:
        action   = request.form.get('action', 'skip')
        raw_rows = request.form.getlist('region_row')
        rows = []
        for item in raw_rows:
            if '|' in item:
                region, suburb = item.split('|', 1)
                rows.append({'region': region.strip(), 'suburb': suburb.strip()})

        if not rows:
            flash('No rows to import.', 'danger')
            return redirect(url_for('regions.import_csv'))

        created_regions = 0
        created_suburbs = 0
        skipped_suburbs = 0

        with get_db() as conn:
            for row in rows:
                rname  = row['region']
                suburb = row['suburb']

                existing = conn.execute(
                    "SELECT id FROM regions WHERE name=?", (rname,)).fetchone()
                if existing:
                    region_id = existing['id']
                else:
                    conn.execute(
                        "INSERT INTO regions (name, visit_day) VALUES (?, 'Monday')",
                        (rname,))
                    region_id = conn.execute(
                        "SELECT id FROM regions WHERE name=?",
                        (rname,)).fetchone()['id']
                    created_regions += 1

                existing_sub = conn.execute(
                    "SELECT id, region_id FROM suburbs WHERE name=?",
                    (suburb,)).fetchone()
                if existing_sub:
                    if action == 'replace' and existing_sub['region_id'] != region_id:
                        conn.execute(
                            "UPDATE suburbs SET region_id=? WHERE id=?",
                            (region_id, existing_sub['id']))
                        created_suburbs += 1
                    else:
                        skipped_suburbs += 1
                else:
                    conn.execute(
                        "INSERT INTO suburbs (region_id, name) VALUES (?, ?)",
                        (region_id, suburb))
                    created_suburbs += 1

            conn.commit()

        flash(
            f'Import complete: {created_regions} region(s) created, '
            f'{created_suburbs} suburb(s) added/updated, '
            f'{skipped_suburbs} skipped.',
            'success')
        return redirect(url_for('regions.index'))

    # ── Upload step: parse CSV and show preview ───────────────────────────────
    f = request.files.get('csvfile')
    if not f or not f.filename.endswith('.csv'):
        flash('Please upload a CSV file.', 'danger')
        return render_template('regions/import.html')

    try:
        raw = f.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(raw))
        if not {'Region', 'Suburb'}.issubset(set(reader.fieldnames or [])):
            flash('CSV must have "Region" and "Suburb" columns.', 'danger')
            return render_template('regions/import.html')
        rows = [{'region': r['Region'].strip(), 'suburb': r['Suburb'].strip()}
                for r in reader if r['Region'].strip() and r['Suburb'].strip()]
    except Exception as e:
        flash(f'Could not read CSV: {e}', 'danger')
        return render_template('regions/import.html')

    if not rows:
        flash('No valid rows found in CSV.', 'danger')
        return render_template('regions/import.html')

    with get_db() as conn:
        existing_regions = {r['name'] for r in
                            conn.execute("SELECT name FROM regions").fetchall()}
        existing_suburbs = {s['name'] for s in
                            conn.execute("SELECT name FROM suburbs").fetchall()}

    from collections import defaultdict
    preview_map = defaultdict(list)
    for row in rows:
        preview_map[row['region']].append({
            'suburb': row['suburb'],
            'suburb_exists': row['suburb'] in existing_suburbs,
        })

    preview = [
        {
            'name':     rname,
            'is_new':   rname not in existing_regions,
            'suburbs':  sorted(subs, key=lambda x: x['suburb']),
        }
        for rname, subs in sorted(preview_map.items())
    ]

    return render_template('regions/import.html', preview=preview, rows=rows)

@regions_bp.route('/regions/<int:region_id>/open-dates')
def open_dates(region_id):
    with get_db() as conn:
        dates = conn.execute("""
            SELECT date FROM region_dates
            WHERE region_id=? AND status='open'
            ORDER BY date
        """, (region_id,)).fetchall()
    return jsonify([row['date'] for row in dates])


@regions_bp.route('/suburbs/all')
def all_suburbs():
    with get_db() as conn:
        suburbs = conn.execute("""
            SELECT s.name, s.region_id, r.name as region_name
            FROM suburbs s JOIN regions r ON s.region_id=r.id
            ORDER BY s.name
        """).fetchall()
    return jsonify([dict(s) for s in suburbs])
