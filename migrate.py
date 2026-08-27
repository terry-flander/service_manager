from models import get_db

with get_db() as conn:
    # ── Schema additions ─────────────────────────────────────────────────────
    try: conn.execute('ALTER TABLE jobs ADD COLUMN bike_description TEXT')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN end_date TEXT')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN invoice_number TEXT')
    except: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN require_2fa INTEGER DEFAULT 0')
    except: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN show_cash_payments INTEGER DEFAULT 0')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN reconciled_eftpos TEXT')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN gcal_event_id TEXT')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN add_to_calendar INTEGER NOT NULL DEFAULT 0')
    except: pass
    try: conn.execute('ALTER TABLE region_dates ADD COLUMN gcal_event_id TEXT')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN referral_source TEXT')
    except: pass
    try: conn.execute('ALTER TABLE job_queries ADD COLUMN sort1_field TEXT')
    except: pass
    try: conn.execute('ALTER TABLE job_queries ADD COLUMN sort1_dir TEXT')
    except: pass
    try: conn.execute('ALTER TABLE job_queries ADD COLUMN sort2_field TEXT')
    except: pass
    try: conn.execute('ALTER TABLE job_queries ADD COLUMN sort2_dir TEXT')
    except: pass
    try: conn.execute('ALTER TABLE job_queries ADD COLUMN sort3_field TEXT')
    except: pass
    try: conn.execute('ALTER TABLE job_queries ADD COLUMN sort3_dir TEXT')
    except: pass
    try: conn.execute("ALTER TABLE job_queries ADD COLUMN date_field TEXT NOT NULL DEFAULT 'scheduled'")
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN subtotal REAL DEFAULT 0')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN gst REAL DEFAULT 0')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN total REAL DEFAULT 0')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN portal_token TEXT')
    except: pass
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS job_status_triggers (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type       TEXT NOT NULL,
            trigger_status TEXT NOT NULL,
            template_id    INTEGER REFERENCES email_templates(id) ON DELETE SET NULL,
            active         INTEGER NOT NULL DEFAULT 1,
            created_at     TEXT DEFAULT (datetime('now')),
            UNIQUE(job_type, trigger_status)
        )''')
    except: pass
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS customer_contacts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            phone       TEXT,
            email       TEXT,
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )''')
    except: pass
    try: conn.execute('ALTER TABLE email_replies ADD COLUMN contact_id INTEGER REFERENCES customer_contacts(id) ON DELETE SET NULL')
    except: pass
    try: conn.execute('ALTER TABLE job_queries ADD COLUMN column_visibility_id INTEGER REFERENCES column_visibility_sets(id)')
    except: pass

    # ── Column visibility sets (Jobs List / Sales Report column picker) ─────
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS column_visibility_sets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            page        TEXT NOT NULL,
            desktop     TEXT,
            landscape   TEXT,
            portrait    TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        )''')
    except Exception:
        pass

    # ── Saved job queries (Jobs List / Sales Report query builder) ──────────
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS job_queries (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL UNIQUE,
            job_types     TEXT,
            statuses      TEXT,
            payment_types TEXT,
            search        TEXT,
            gross_min     REAL,
            gross_max     REAL,
            date_mode     TEXT NOT NULL DEFAULT 'preset',
            date_preset   TEXT,
            date_from     TEXT,
            date_to       TEXT,
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now'))
        )''')
    except Exception:
        pass

    # ── EFTPOS transactions table ────────────────────────────────────────────
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS eftpos_transactions (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            reference_number     TEXT UNIQUE NOT NULL,
            rrn                  TEXT,
            transaction_datetime TEXT,
            transaction_date     TEXT,
            method               TEXT,
            amount               REAL,
            total_amount         REAL,
            surcharge            REAL DEFAULT 0,
            terminal_id          TEXT,
            card_number          TEXT,
            transaction_status   TEXT,
            pay_status           TEXT,
            settlement_date      TEXT,
            settlement_amount    REAL,
            job_id               INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
            reconciled_at        TEXT,
            reconciled_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
            imported_at          TEXT DEFAULT (datetime('now'))
        )''')
    except: pass

    # ── void → lost ──────────────────────────────────────────────────────────
    conn.execute("UPDATE jobs SET status='lost' WHERE status='void'")

    # ── Clear booking service_types from workshop jobs ────────────────────────
    # Workshop jobs only accept SR- part names in service_types.
    # Any pre-existing value that doesn't match an active SR- part name is cleared.
    sr_names = {r[0] for r in conn.execute(
        "SELECT name FROM parts WHERE active=1 AND part_number LIKE 'SR-%'"
    ).fetchall()}

    workshop_jobs = conn.execute(
        "SELECT id, service_types FROM jobs "
        "WHERE job_type='workshop' AND service_types IS NOT NULL AND service_types != ''"
    ).fetchall()

    cleaned = 0
    for job in workshop_jobs:
        kept = [n.strip() for n in job[1].split(',')
                if n.strip() and n.strip() in sr_names]
        new_val = ', '.join(kept) if kept else None
        if new_val != job[1]:
            conn.execute(
                "UPDATE jobs SET service_types=? WHERE id=?",
                (new_val, job[0]))
            cleaned += 1

    conn.commit()

    # ── Backfill subtotal/gst/total for every existing job ────────────────────
    # These columns are new (denormalized totals, previously calculated on
    # the fly everywhere they were needed). Recalculate once for every job
    # so historical data isn't left at the column default of 0.
    from routes.jobs import recalc_job_totals
    all_job_ids = [r[0] for r in conn.execute("SELECT id FROM jobs").fetchall()]
    for _jid in all_job_ids:
        recalc_job_totals(conn, _jid)
    print(f"Backfilled totals for {len(all_job_ids)} job(s).")

print(f"Migration complete. {cleaned} workshop job(s) had service_types cleaned.")

# ── Parts inventory schema ────────────────────────────────────────────────────

# parts table new columns
for col, defn in [
    ('part_type',        "TEXT DEFAULT 'stock'"),
    ('avg_cost_inc_gst', 'REAL DEFAULT 0'),
    ('reorder_point',    'REAL DEFAULT 0'),
    ('reorder_qty',      'REAL DEFAULT 0'),
    ('supplier_id',      'INTEGER'),
]:
    try:
        conn.execute(f'ALTER TABLE parts ADD COLUMN {col} {defn}')
    except Exception:
        pass

# Auto-classify existing parts by name keywords
conn.execute("""
    UPDATE parts SET part_type='service'
    WHERE part_type='stock'
      AND (LOWER(name) LIKE '%service%'
        OR LOWER(name) LIKE '%fitting%'
        OR LOWER(name) LIKE '%adjust%')
""")

# New tables
conn.execute("""
    CREATE TABLE IF NOT EXISTS service_types (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        code        TEXT NOT NULL UNIQUE,
        label       TEXT NOT NULL,
        description TEXT,
        keywords    TEXT,
        part_id     INTEGER REFERENCES parts(id) ON DELETE SET NULL,
        active      INTEGER DEFAULT 1,
        sort_order  INTEGER DEFAULT 0
    )
""")

conn.execute("""
    CREATE TABLE IF NOT EXISTS locations (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT NOT NULL UNIQUE,
        job_type TEXT,
        active   INTEGER DEFAULT 1
    )
""")

conn.execute("""
    CREATE TABLE IF NOT EXISTS suppliers (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        email      TEXT,
        phone      TEXT,
        website    TEXT,
        notes      TEXT,
        active     INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now'))
    )
""")

conn.execute("""
    CREATE TABLE IF NOT EXISTS supplier_parts (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_id          INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
        part_id              INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
        supplier_sku         TEXT,
        supplier_description TEXT,
        last_price_inc_gst   REAL,
        last_ordered_at      TEXT,
        UNIQUE(supplier_id, supplier_sku)
    )
""")

conn.execute("""
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_id     INTEGER NOT NULL REFERENCES suppliers(id),
        order_date      TEXT NOT NULL,
        reference       TEXT,
        status          TEXT DEFAULT 'open',
        freight_inc_gst REAL DEFAULT 0,
        notes           TEXT,
        created_by      INTEGER REFERENCES users(id),
        created_at      TEXT DEFAULT (datetime('now'))
    )
""")

conn.execute("""
    CREATE TABLE IF NOT EXISTS purchase_order_lines (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id      INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
        part_id       INTEGER REFERENCES parts(id),
        supplier_sku  TEXT,
        supplier_desc TEXT NOT NULL,
        qty_ordered   REAL NOT NULL,
        qty_received  REAL DEFAULT 0,
        price_inc_gst REAL NOT NULL,
        location_id   INTEGER REFERENCES locations(id)
    )
""")

conn.execute("""
    CREATE TABLE IF NOT EXISTS part_location_stock (
        part_id     INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
        location_id INTEGER NOT NULL REFERENCES locations(id),
        qty_on_hand REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (part_id, location_id)
    )
""")

conn.execute("""
    CREATE TABLE IF NOT EXISTS inventory_counts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        count_date  TEXT NOT NULL,
        location_id INTEGER REFERENCES locations(id),
        status      TEXT DEFAULT 'open',
        notes       TEXT,
        created_by  INTEGER REFERENCES users(id),
        created_at  TEXT DEFAULT (datetime('now'))
    )
""")

conn.execute("""
    CREATE TABLE IF NOT EXISTS inventory_count_lines (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        count_id    INTEGER NOT NULL REFERENCES inventory_counts(id) ON DELETE CASCADE,
        part_id     INTEGER NOT NULL REFERENCES parts(id),
        location_id INTEGER NOT NULL REFERENCES locations(id),
        qty_system  REAL NOT NULL,
        qty_counted REAL,
        adjustment  REAL,
        notes       TEXT
    )
""")

# Seed locations
for loc_name, loc_job_type in [('Workshop', 'workshop'), ('Truck', 'booking')]:
    try:
        conn.execute(
            "INSERT INTO locations (name, job_type) VALUES (?, ?)",
            (loc_name, loc_job_type))
    except Exception:
        pass

# Seed service_types from hardcoded list
# keywords stored as comma-separated string
_SERVICE_SEED = [
    ('general_service',      'General Service',        'Standard bicycle service and repair',
     'general service,service,tune,repair,overhaul,brake,gear,tyre,tube,chain,derailleur,assemble,setup,check', 0),
    ('ebike_service',        'eBike Service',          'Electric bicycle service',
     'ebike,e-bike,electric bike,e-cargo,e bike,ecargo,electric', 1),
    ('tribe_cargo_service',  'Tribe/Cargo Bike Service', 'Cargo and longtail bicycle service',
     'tribe,longtail,long tail,cargo bike,bakfiets', 2),
    ('three_or_more_bikes',  '3 or More Bikes',        'Fleet or multi-bike service',
     '3 or more,3+ bikes,three or more,4 bikes,5 bikes,fleet,3 bikes,three bikes,four bikes,36 bikes', 3),
    ('other',                'Other',                  '',
     '', 4),
]

for code, label, desc, keywords, sort in _SERVICE_SEED:
    try:
        conn.execute("""
            INSERT INTO service_types (code, label, description, keywords, sort_order)
            VALUES (?, ?, ?, ?, ?)
        """, (code, label, desc, keywords, sort))
    except Exception:
        pass

# Link service types to parts by name match
for code, label, *_ in _SERVICE_SEED:
    part = conn.execute(
        "SELECT id FROM parts WHERE LOWER(name)=LOWER(?) AND active=1 LIMIT 1",
        (label,)).fetchone()
    if part:
        try:
            conn.execute(
                "UPDATE service_types SET part_id=? WHERE code=?",
                (part['id'], code))
        except Exception:
            pass

conn.commit()
print("Inventory schema migration complete.")

# ── service_types job_group column ───────────────────────────────────────────
try:
    conn.execute("ALTER TABLE service_types ADD COLUMN job_group TEXT DEFAULT 'booking'")
    print("Added service_types.job_group")
except Exception:
    pass

# Set existing booking types
conn.execute("""
    UPDATE service_types SET job_group='booking'
    WHERE job_group IS NULL OR job_group=''
""")

# Migrate SR- parts to workshop service types
sr_parts = conn.execute("""
    SELECT id, name, part_number, unit_cost FROM parts
    WHERE part_number LIKE 'SR-%' AND active=1
    ORDER BY part_number
""").fetchall()

for i, p in enumerate(sr_parts):
    existing = conn.execute(
        "SELECT id FROM service_types WHERE part_id=?", (p['id'],)).fetchone()
    if not existing:
        code = 'sr_' + p['part_number'].lower().replace('-','_').replace(' ','_')
        try:
            conn.execute("""
                INSERT INTO service_types
                    (code, label, description, keywords, part_id, active, sort_order, job_group)
                VALUES (?, ?, '', '', ?, 1, ?, 'workshop')
            """, (code, p['name'], p['id'], i))
            print(f"  Migrated SR part '{p['name']}' to workshop service type")
        except Exception as e:
            print(f"  Skip {p['name']}: {e}")
    else:
        conn.execute(
            "UPDATE service_types SET job_group='workshop' WHERE part_id=?",
            (p['id'],))

conn.commit()
print("service_types job_group migration complete.")

# ── inventory_transactions table ─────────────────────────────────────────────
conn.execute("""
    CREATE TABLE IF NOT EXISTS inventory_transactions (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        part_id             INTEGER NOT NULL REFERENCES parts(id),
        transaction_date    TEXT NOT NULL,
        type                TEXT NOT NULL,
        quantity            REAL NOT NULL,
        unit_price_inc_gst  REAL,
        location_id         INTEGER REFERENCES locations(id),
        source_type         TEXT,
        source_id           INTEGER,
        notes               TEXT,
        created_by          INTEGER REFERENCES users(id),
        created_at          TEXT DEFAULT (datetime('now'))
    )
""")
conn.commit()
print("inventory_transactions table ready.")

# ── Fix inventory_count_lines.location_id NOT NULL ────────────────────────────
# Drop and recreate to remove the NOT NULL constraint (table should be empty)
try:
    count = conn.execute("SELECT COUNT(*) FROM inventory_count_lines").fetchone()[0]
    if count == 0:
        conn.execute("DROP TABLE IF EXISTS inventory_count_lines")
        conn.execute("""
            CREATE TABLE inventory_count_lines (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                count_id    INTEGER NOT NULL REFERENCES inventory_counts(id) ON DELETE CASCADE,
                part_id     INTEGER NOT NULL REFERENCES parts(id),
                location_id INTEGER REFERENCES locations(id),
                qty_system  REAL NOT NULL,
                qty_counted REAL,
                adjustment  REAL,
                notes       TEXT
            )
        """)
        conn.commit()
        print("Recreated inventory_count_lines without NOT NULL on location_id.")
    else:
        print(f"inventory_count_lines has {count} rows — skipping recreate.")
except Exception as e:
    print(f"inventory_count_lines fix skipped: {e}")

# ── email_import_attachments table ────────────────────────────────────────────
try:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_import_attachments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email_import_id INTEGER NOT NULL REFERENCES email_imports(id) ON DELETE CASCADE,
            filename        TEXT NOT NULL,
            filepath        TEXT NOT NULL,
            mime_type       TEXT,
            size_bytes      INTEGER,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    print("email_import_attachments table ready.")
except Exception as e:
    print(f"email_import_attachments migration skipped: {e}")
