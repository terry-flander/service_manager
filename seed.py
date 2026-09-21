"""
seed.py — initial data loader for ServiceDesk.

Behaviour:
  - Parts:   always upserted from parts.csv (safe to re-run)
  - Regions/Suburbs: loaded from regions_suburbs.csv on first run only
                     (skipped if any regions already exist)
  - No region_dates are created — add these through the UI
  - No default customers or jobs are created
  - DB path is externalised via DATA_DIR env var (see models.py)
"""
import csv
import os
from models import get_db

BASE_DIR          = os.path.dirname(__file__)
PARTS_CSV         = os.path.join(BASE_DIR, 'parts.csv')
REGIONS_CSV       = os.path.join(BASE_DIR, 'regions_suburbs.csv')


# ── Parts ─────────────────────────────────────────────────────────────────────

def _internal_domain(conn):
    row = conn.execute("SELECT value FROM settings WHERE key='internal_email_domain'").fetchone()
    return row['value'] if row else 'app.internal'


def _load_parts_from_csv():
    parts = []
    with open(PARTS_CSV, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            name      = row['part_name'].strip()
            part_num  = row['part_number'].strip()
            unit_cost = float(
                row['unit_cost'].replace('$', '').replace(',', '').strip())
            parts.append((name, part_num, unit_cost, 'each'))
    return parts


def _seed_parts(conn):
    parts    = _load_parts_from_csv()
    csv_nums = [p[1] for p in parts]

    for name, part_number, unit_cost, unit in parts:
        conn.execute("""
            INSERT INTO parts (name, part_number, unit_cost, unit, active)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(part_number) DO UPDATE SET
                name      = excluded.name,
                unit_cost = excluded.unit_cost,
                unit      = excluded.unit,
                active    = 1
        """, (name, part_number, unit_cost, unit))

    print(f'✓ {len(parts)} parts upserted from parts.csv')


# ── Regions & suburbs ─────────────────────────────────────────────────────────

def _load_regions_suburbs_from_csv():
    """
    Returns a dict: { region_name: [suburb, ...] }
    Reads regions_suburbs.csv with columns: Region, Suburb
    """
    mapping = {}
    with open(REGIONS_CSV, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            region = row['Region'].strip()
            suburb = row['Suburb'].strip()
            if region and suburb:
                mapping.setdefault(region, []).append(suburb)
    return mapping


def _seed_regions(conn):
    """Load regions and suburbs from CSV — skipped if regions already exist."""
    existing = conn.execute(
        "SELECT COUNT(*) FROM regions").fetchone()[0]

    if existing > 0:
        print(f'  Regions already present ({existing}) — skipping CSV load')
        return

    mapping = _load_regions_suburbs_from_csv()

    for region_name, suburbs in sorted(mapping.items()):
        conn.execute(
            "INSERT OR IGNORE INTO regions (name, visit_day) VALUES (?, 'Monday')",
            (region_name,))
        region_id = conn.execute(
            "SELECT id FROM regions WHERE name=?",
            (region_name,)).fetchone()['id']

        for suburb in suburbs:
            conn.execute(
                "INSERT OR IGNORE INTO suburbs (region_id, name) VALUES (?, ?)",
                (region_id, suburb))

    region_count = len(mapping)
    suburb_count = sum(len(v) for v in mapping.values())
    print(f'✓ {region_count} regions and {suburb_count} suburbs loaded'
          f' from regions_suburbs.csv')


# ── Entry point ───────────────────────────────────────────────────────────────

def _seed_cash_sales_customer(conn):
    """Ensure the locked Counter Sales customer record exists (migrates legacy email)."""
    dom = _internal_domain(conn)
    cs_email   = f'counter.sales@{dom}'
    cash_email = f'cash.sales@{dom}'
    existing = conn.execute(
        "SELECT id FROM customers WHERE email=?", (cs_email,)
    ).fetchone()
    if existing:
        return
    legacy = conn.execute(
        "SELECT id FROM customers WHERE email=?", (cash_email,)
    ).fetchone()
    if legacy:
        conn.execute("UPDATE customers SET email=? WHERE id=?", (cs_email, legacy['id']))
        conn.commit()
        print("  Counter Sales customer migrated from legacy email")
        return
    conn.execute(
        "INSERT INTO customers (name, email, phone, suburb, address) VALUES (?,?,?,?,?)",
        ('Counter Sales', cs_email, '', '', ''))
    conn.commit()
    print("  Counter Sales customer created")


def _seed_bikes_for_sale_customer(conn):
    """Ensure the locked Bikes for Sale internal customer exists."""
    dom = _internal_domain(conn)
    bfs_email = f'bikes.for.sale@{dom}'
    existing = conn.execute(
        "SELECT id FROM customers WHERE email=?", (bfs_email,)
    ).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT INTO customers (name, email, phone, suburb, address) VALUES (?,?,?,?,?)",
        ('Bikes for Sale', bfs_email, '', '', ''))
    conn.commit()
    print("  Bikes for Sale customer created")


BIKE_SPEC_TEMPLATE = """FRAMESET
FRAME

HEADSET
Integrated (IS) Sealed Bearing, IS 52/28.6 | IS 52/40

SUSPENSION
FORK

DRIVETRAIN
REAR DERAILLEUR

FRONT DERAILLEUR

SHIFTER

CRANK
Crank Arm Length:

CHAINRING

CASSETTE

CHAIN

BRAKES
BRAKE LEVER

BRAKE CALIPER

WHEELS
RIM

TYRE

COCKPIT
HANDLEBAR

STEM

SEAT POST

SADDLE

COMPONENTS
PEDALS

"""

def _seed_bike_spec_template(conn):
    """Ensure the default bike spec template exists."""
    existing = conn.execute(
        "SELECT id FROM email_templates WHERE name='Bike Specification Template'"
    ).fetchone()
    if existing:
        return
    conn.execute("""
        INSERT INTO email_templates (name, subject, body, grp)
        VALUES ('Bike Specification Template', 'Bike Specification', ?, 'bike')
    """, (BIKE_SPEC_TEMPLATE,))
    conn.commit()
    print("  Bike Specification Template created")


def seed_data():
    with get_db() as conn:
        parts_count = conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
        if parts_count == 0:
            _seed_parts(conn)
        else:
            print(f'  Parts already present ({parts_count}) — skipping CSV load')
        _seed_regions(conn)
        _seed_cash_sales_customer(conn)
        _seed_bikes_for_sale_customer(conn)
        _seed_bike_spec_template(conn)
        conn.commit()