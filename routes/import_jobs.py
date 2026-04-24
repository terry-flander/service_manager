"""
Job import — CSV → preview → confirm.
CSV columns: Name, Email, Phone, Suburb, Message, Date, Time

Per row:
  - UPSERT customer (email key; phone fallback if no email)
  - Lookup region from suburb (suburbs table)
  - Create booking job: status=paid, paid_date=Date,
    scheduled_date=Date, scheduled_time=Time, end_time=Time+1hr,
    description=Message
"""
import csv, io, re
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import get_db
from routes.jobs import upsert_customer, generate_reference, TIME_SLOTS

import_jobs_bp = Blueprint('import_jobs', __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(raw):
    """Accept d/m/yyyy, dd/mm/yyyy, yyyy-mm-dd. Returns ISO string or None."""
    raw = (raw or '').strip()
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(raw, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return None


def _parse_time(raw):
    """Accept H:MM, HH:MM, H:MM AM/PM. Returns HH:MM string or None."""
    raw = (raw or '').strip()
    for fmt in ('%H:%M', '%I:%M %p', '%I:%M%p', '%H:%M:%S'):
        try:
            t = datetime.strptime(raw, fmt)
            # Snap to nearest 30-min slot
            mins = t.hour * 60 + t.minute
            snapped = round(mins / 30) * 30
            snapped = max(0, min(snapped, 23 * 60 + 30))
            hh, mm = divmod(snapped, 60)
            return f"{hh:02d}:{mm:02d}"
        except ValueError:
            pass
    return None


def _end_time(start):
    """Return start + 1 hour, snapped to the nearest TIME_SLOT."""
    if not start or not TIME_SLOTS:
        return None
    h, m = int(start[:2]), int(start[3:])
    mins = h * 60 + m + 60
    return min(TIME_SLOTS, key=lambda s: abs(int(s[:2]) * 60 + int(s[3:]) - mins))


def _suburb_to_region(conn, suburb):
    """Return region_id for suburb name (case-insensitive), or first region."""
    if suburb:
        row = conn.execute(
            "SELECT region_id FROM suburbs WHERE LOWER(name)=LOWER(?)",
            (suburb.strip(),)).fetchone()
        if row:
            return row['region_id']
    # Fall back to first region
    row = conn.execute("SELECT id FROM regions ORDER BY id LIMIT 1").fetchone()
    return row['id'] if row else 1


def _temp_email(phone):
    """Generate a deterministic placeholder email from phone number."""
    digits = re.sub(r'\D', '', phone or '')
    return f"noemail_{digits}@import.local" if digits else None


def _parse_csv(stream):
    """Parse CSV, return list of row dicts and any field errors."""
    reader = csv.DictReader(stream)
    fields = [f.strip() for f in (reader.fieldnames or [])]
    required = {'Name', 'Date'}
    missing  = required - set(fields)
    if missing:
        return None, f"CSV missing required columns: {', '.join(missing)}"
    rows = []
    for i, row in enumerate(reader, 2):
        name    = (row.get('Name')    or '').strip()
        email   = (row.get('Email')   or '').strip().lower()
        phone   = (row.get('Phone')   or '').strip()
        suburb  = (row.get('Suburb')  or '').strip()
        message = (row.get('Message') or '').strip()
        raw_date = row.get('Date') or ''
        raw_time = row.get('Time') or ''
        if not name:
            continue
        parsed_date = _parse_date(raw_date)
        parsed_time = _parse_time(raw_time)
        etime       = _end_time(parsed_time)
        # Use phone-based temp email if email blank
        if not email:
            email = _temp_email(phone) or ''
        address = (row.get('Address') or '').strip()
        rows.append({
            'row':       i,
            'name':      name,
            'email':     email,
            'phone':     phone,
            'suburb':    suburb,
            'address':   address,
            'message':   message,
            'date':      parsed_date,
            'time':      parsed_time,
            'end_time':  etime,
            'raw_date':  raw_date,
            'address':   (row.get('Address') or '').strip(),
            'raw_time':  raw_time,
            'error':     None if parsed_date else f"Row {i}: unrecognised date '{raw_date}'",
        })
    return rows, None


# ── Routes ────────────────────────────────────────────────────────────────────

@import_jobs_bp.route('/jobs/import', methods=['GET', 'POST'])
def import_jobs():
    if request.method == 'GET':
        return render_template('jobs/import.html')

    # ── Confirm step ─────────────────────────────────────────────────────────
    if 'confirm' in request.form:
        import json, sqlite3 as _sqlite3
        raw_rows = request.form.get('rows_json', '[]')
        try:
            rows = json.loads(raw_rows)
        except Exception:
            flash('Session expired — please re-upload the CSV.', 'danger')
            return redirect(url_for('import_jobs.import_jobs'))

        created_customers = 0
        created_jobs      = 0
        skipped           = 0

        with get_db() as conn:
            for row in rows:
                if row.get('error'):
                    skipped += 1
                    continue
                try:
                    # Upsert customer
                    customer_id, stored_addr = upsert_customer(
                        conn,
                        row['name'], row['email'], row['phone'],
                        row['suburb'], row.get('address') or '')

                    # Was this customer new?  (check before commit)
                    existing = conn.execute(
                        "SELECT created_at FROM customers WHERE id=?",
                        (customer_id,)).fetchone()
                    # Count as new if email is a temp placeholder
                    if 'import.local' in (row['email'] or ''):
                        created_customers += 1

                    region_id = _suburb_to_region(conn, row['suburb'])

                    # Generate reference inside the transaction (retry loop)
                    for _attempt in range(5):
                        ref = generate_reference('booking', conn)
                        try:
                            conn.execute("""
                                INSERT INTO jobs (
                                    reference, job_type, customer_id,
                                    customer_name, customer_email, customer_phone,
                                    suburb, address, description,
                                    region_id, tax_inclusive,
                                    scheduled_date, scheduled_time, end_time,
                                    status, paid_date, notes)
                                VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,'paid',?,?)
                            """, (
                                ref, 'booking', customer_id,
                                row['name'], row['email'], row['phone'],
                        row['suburb'], (row.get('address') or row['suburb']), row['message'],
                                region_id,
                                row['date'], row['time'], row['end_time'],
                                row['date'], 'Imported from CSV'
                            ))
                            created_jobs += 1
                            break
                        except _sqlite3.IntegrityError as e:
                            if 'reference' in str(e) and _attempt < 4:
                                conn.rollback()
                                continue
                            raise

                except Exception as e:
                    skipped += 1
                    continue

            conn.commit()

        flash(
            f'Import complete: {created_jobs} job(s) created, '
            f'{skipped} row(s) skipped.',
            'success')
        return redirect(url_for('jobs.index'))

    # ── Upload + preview step ─────────────────────────────────────────────────
    f = request.files.get('csvfile')
    if not f or not f.filename.endswith('.csv'):
        flash('Please upload a .csv file.', 'danger')
        return render_template('jobs/import.html')

    try:
        raw   = f.read().decode('utf-8-sig')
        rows, err = _parse_csv(io.StringIO(raw))
    except Exception as e:
        flash(f'Could not read CSV: {e}', 'danger')
        return render_template('jobs/import.html')

    if err:
        flash(err, 'danger')
        return render_template('jobs/import.html')

    if not rows:
        flash('No valid rows found in CSV.', 'danger')
        return render_template('jobs/import.html')

    # Enrich preview with existing-customer info
    with get_db() as conn:
        for row in rows:
            if row['email']:
                existing = conn.execute(
                    "SELECT id, name FROM customers WHERE email=?",
                    (row['email'],)).fetchone()
                row['customer_exists'] = existing is not None
                row['customer_name_stored'] = existing['name'] if existing else None
            else:
                row['customer_exists'] = False
                row['customer_name_stored'] = None

    import json
    return render_template('jobs/import.html',
                           preview=rows,
                           rows_json=json.dumps(rows))
