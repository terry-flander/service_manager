"""
Customer import — CSV → preview → confirm.

Expected CSV columns (case-insensitive, order flexible):
  Name, Email, Phone, Suburb, Address

Matching: UPSERT on email (same logic as upsert_customer in jobs.py).
Rows with no email AND no name are skipped.
"""
import csv, io
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models import get_db
from routes.jobs import upsert_customer

import_customers_bp = Blueprint('import_customers', __name__)

EXPECTED_COLS = {'name', 'email', 'phone', 'suburb', 'address'}


def _normalise_header(h):
    return h.strip().lower().replace(' ', '_')


def _parse_csv(file_bytes):
    """
    Parse CSV bytes. Returns (rows, errors) where rows is a list of dicts
    with keys: name, email, phone, suburb, address.
    """
    text = file_bytes.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], ['CSV file has no header row.']

    # Normalise headers
    headers = {_normalise_header(f): f for f in reader.fieldnames}
    missing = EXPECTED_COLS - set(headers.keys()) - {'phone', 'suburb', 'address'}
    if missing:
        return [], [f'Missing required column(s): {", ".join(sorted(missing))}']

    rows, errors = [], []
    for i, row in enumerate(reader, start=2):
        normalised = {_normalise_header(k): (v or '').strip() for k, v in row.items()}
        name  = normalised.get('name',    '')
        email = normalised.get('email',   '').lower()
        phone = normalised.get('phone',   '')
        suburb = normalised.get('suburb', '')
        address = normalised.get('address', '')

        if not name and not email:
            errors.append(f'Row {i}: skipped (no name or email)')
            continue

        rows.append({
            'name':    name,
            'email':   email,
            'phone':   phone,
            'suburb':  suburb,
            'address': address,
        })

    return rows, errors


@import_customers_bp.route('/admin/import-customers', methods=['GET', 'POST'])
def import_customers():
    if session.get('user_role') != 'admin':
        flash('Admin access required.', 'danger')
        return redirect(url_for('jobs.index'))

    if request.method == 'GET':
        return render_template('customers/import.html',
                               preview=None, errors=[], filename=None)

    # ── POST: file upload or confirm ──────────────────────────────────────────
    if 'confirm' in request.form:
        import json
        rows_json = request.form.get('rows_json', '[]')
        try:
            rows = json.loads(rows_json)
        except Exception:
            flash('Import data lost — please re-upload the file.', 'danger')
            return redirect(url_for('import_customers.import_customers'))

        created = updated = 0
        name_mismatches = []   # rows where import name differs from existing name

        with get_db() as conn:
            for row in rows:
                email = (row.get('email') or '').strip().lower()
                if email:
                    existing = conn.execute(
                        "SELECT id, name FROM customers WHERE LOWER(email)=LOWER(?)",
                        (email,)).fetchone()
                else:
                    existing = None

                if existing:
                    # Update everything EXCEPT name — preserve existing name
                    conn.execute("""
                        UPDATE customers
                        SET phone=?, suburb=?, address=?
                        WHERE id=?
                    """, (row.get('phone',''), row.get('suburb',''),
                          row.get('address',''), existing['id']))
                    updated += 1
                    # Record name mismatch for reconciliation report
                    import_name   = row.get('name', '').strip()
                    existing_name = existing['name'].strip()
                    if import_name and import_name.lower() != existing_name.lower():
                        name_mismatches.append({
                            'email':         email,
                            'existing_name': existing_name,
                            'import_name':   import_name,
                        })
                else:
                    # New customer — use upsert_customer for full insert
                    upsert_customer(conn, row['name'], email,
                                    row.get('phone',''), row.get('suburb',''),
                                    row.get('address',''))
                    created += 1
            conn.commit()

        # Show reconciliation report if there are name mismatches
        if name_mismatches:
            return render_template('customers/import.html',
                                   preview=None, errors=[],
                                   filename=None, created=created,
                                   updated=updated,
                                   name_mismatches=name_mismatches)

        flash(f'Import complete: {created} created, {updated} updated.', 'success')
        return redirect(url_for('customers.index'))

    # ── POST: file upload — parse and preview ──────────────────────────────────
    file = request.files.get('csv_file')
    if not file or not file.filename:
        flash('Please select a CSV file.', 'danger')
        return render_template('customers/import.html',
                               preview=None, errors=[], filename=None)

    rows, errors = _parse_csv(file.read())

    if not rows and errors:
        flash('Could not parse CSV file.', 'danger')
        return render_template('customers/import.html',
                               preview=None, errors=errors, filename=file.filename)

    # Annotate with would-create vs would-update
    with get_db() as conn:
        for row in rows:
            if row['email']:
                exists = conn.execute(
                    "SELECT id, name FROM customers WHERE LOWER(email)=LOWER(?)",
                    (row['email'],)).fetchone()
                row['action'] = 'update' if exists else 'create'
                row['existing_name'] = exists['name'] if exists else None
            else:
                row['action'] = 'create'
                row['existing_name'] = None

    import json
    return render_template('customers/import.html',
                           preview=rows, errors=errors,
                           filename=file.filename,
                           rows_json=json.dumps(rows))
