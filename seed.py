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

    # Deactivate any parts no longer in the CSV
    conn.execute(
        f"UPDATE parts SET active=0 WHERE part_number NOT IN "
        f"({','.join('?' * len(csv_nums))})",
        csv_nums)

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

def seed_data():
    with get_db() as conn:
        _seed_parts(conn)
        _seed_regions(conn)
        conn.commit()
