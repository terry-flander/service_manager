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

# ── Bikes for Sale tables ──────────────────────────────────────────────────────
try:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bikes_for_sale (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id       INTEGER UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
            short_desc   TEXT NOT NULL DEFAULT '',
            specs        TEXT NOT NULL DEFAULT '',
            year_est     INTEGER,
            asking_price REAL,
            min_price    REAL,
            status       TEXT NOT NULL DEFAULT 'preparing',
            created_at   TEXT DEFAULT (datetime('now')),
            updated_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bike_images (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            bike_id    INTEGER NOT NULL REFERENCES bikes_for_sale(id) ON DELETE CASCADE,
            filename   TEXT NOT NULL,
            filepath   TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    print("bikes_for_sale and bike_images tables ready.")
except Exception as e:
    print(f"bikes_for_sale migration skipped: {e}")

# ── email_templates.grp column ────────────────────────────────────────────────
try:
    conn.execute("ALTER TABLE email_templates ADD COLUMN grp TEXT NOT NULL DEFAULT 'misc'")
    conn.commit()
    print("email_templates.grp column added.")
except Exception as e:
    print(f"email_templates.grp skipped: {e}")

# ── bikes_sold_days setting ───────────────────────────────────────────────────
try:
    conn.execute("""
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('bikes_sold_days', '30')
    """)
    conn.commit()
    print("bikes_sold_days setting ready.")
except Exception as e:
    print(f"bikes_sold_days setting skipped: {e}")

# ── bikes_for_sale additional columns ────────────────────────────────────────
for col, defn in [
    ('description_html', 'TEXT DEFAULT ""'),
    ('condition_grade',  'TEXT DEFAULT ""'),
    ('frame_size',       'TEXT DEFAULT ""'),
    ('colour',           'TEXT DEFAULT ""'),
]:
    try:
        conn.execute(f"ALTER TABLE bikes_for_sale ADD COLUMN {col} {defn}")
        conn.commit()
        print(f"bikes_for_sale.{col} added.")
    except Exception as e:
        print(f"bikes_for_sale.{col} skipped: {e}")

# ── Business identity settings ────────────────────────────────────────────────
_biz_settings = [
    ('business_name',         'My Business'),
    ('business_tagline',      'Field Service Management'),
    ('business_abn',          ''),
    ('business_phone',        ''),
    ('business_email',        ''),
    ('business_address',      ''),
    ('business_suburb',       ''),
    ('business_state',        ''),
    ('business_postcode',     ''),
    ('business_website',      ''),
    ('business_instagram',    ''),
    ('business_bank_name',    ''),
    ('app_url',               'http://localhost:5000'),
    ('booking_secret',        'change-me'),
    ('booking_cors_origins',  ''),
    ('business_bsb',           ''),
    ('business_account',       ''),
    ('internal_email_domain', 'app.internal'),
    ('setup_complete',        '0'),
]
for key, default in _biz_settings:
    try:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, default))
    except Exception:
        pass
conn.commit()
print("Business identity settings seeded.")

# ── job_type_config table seed ────────────────────────────────────────────────
# Only inserts rows that don't already exist (INSERT OR IGNORE on PK)
# Uses internal_email_domain from settings for internal customer emails
_idom_row = conn.execute(
    "SELECT value FROM settings WHERE key='internal_email_domain'").fetchone()
_idom = _idom_row['value'] if _idom_row else 'app.internal'

_jtc_rows = [
    ('booking',   'Booking',       'FB', 0, 0, 0, 0, 0, 1, 0, 0, 1, 1, 0, None,                                  0),
    ('workshop',  'Workshop',      'PB', 0, 1, 0, 0, 1, 1, 0, 0, 0, 0, 0, None,                                  1),
    ('rental',    'Rental',        'RB', 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, None,                                  2),
    ('sale',      'Sale',          'CS', 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, f'counter.sales@{_idom}',             3),
    ('sale_bike', 'Bike for Sale', 'BK', 1, 1, 1, 1, 0, 1, 1, 0, 0, 0, 1, f'bikes.for.sale@{_idom}',           4),
]
for row in _jtc_rows:
    try:
        conn.execute("""
            INSERT OR IGNORE INTO job_type_config
            (key, label, prefix, hide_customer, hide_address, hide_phone, hide_portal,
             has_service_types, has_bike_description, has_bike_listing, has_end_date,
             use_calendar, use_region, tax_inclusive_default, internal_customer, sort_order)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, row)
    except Exception:
        pass
conn.commit()
print("job_type_config seeded.")

# ── Add show_in_new_job column to job_type_config ────────────────────────────
try:
    conn.execute("ALTER TABLE job_type_config ADD COLUMN show_in_new_job INTEGER NOT NULL DEFAULT 1")
    conn.commit()
    print("Added show_in_new_job to job_type_config.")
except Exception:
    pass  # already exists

# ── sms_log table ─────────────────────────────────────────────────────────────
try:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sms_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      INTEGER REFERENCES jobs(id),
            to_number   TEXT NOT NULL,
            body        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'sent',
            twilio_sid  TEXT,
            error_msg   TEXT,
            sent_at     TEXT DEFAULT (datetime('now')),
            sent_by     INTEGER REFERENCES users(id)
        )
    """)
    conn.commit()
    print("sms_log table ready.")
except Exception as e:
    print(f"sms_log: {e}")

# ── SMS settings ──────────────────────────────────────────────────────────────
for key, default in [('sms_enabled', '0'), ('sms_sender', '')]:
    try:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (key, default))
    except Exception:
        pass
conn.commit()

# ── SMS email_templates (grp='sms') ──────────────────────────────────────────
_sms_seeds = [
    ('SMS: Booking Reminder',
     'Hi {{name}}, reminder of your {{business}} visit on {{date}}. '
     'Call {{phone}} to reschedule. Ref {{ref}}.'),
    ('SMS: On My Way',
     'Hi {{name}}, your mechanic is on the way — expected arrival {{time}}. Ref {{ref}}.'),
    ('SMS: Job Complete',
     'Hi {{name}}, your bike service is complete. Total: ${{total}}. '
     'Thanks for choosing {{business}}!'),
    ('SMS: Workshop Ready',
     'Hi {{name}}, your bike at {{business}} is ready for collection. '
     'Ref {{ref}}. Call {{phone}} for pickup times.'),
    ('SMS: Booking Confirmed',
     'Hi {{name}}, your booking with {{business}} is confirmed for {{date}} {{time}}. '
     'Ref {{ref}}.'),
]
for name, body in _sms_seeds:
    try:
        conn.execute(
            "INSERT OR IGNORE INTO email_templates (name, subject, body, grp) VALUES (?,?,?,?)",
            (name, '', body, 'sms'))
    except Exception:
        pass
conn.commit()
print("SMS settings and templates seeded.")

# ── Workshop booking tables ───────────────────────────────────────────────────
try:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workshop_day_config (
            day_of_week   INTEGER PRIMARY KEY,  -- 0=Mon, 1=Tue ... 6=Sun
            is_open       INTEGER NOT NULL DEFAULT 0,
            max_bookings  INTEGER NOT NULL DEFAULT 5
        )
    """)
    conn.commit()
    print("workshop_day_config table ready.")
except Exception as e:
    print(f"workshop_day_config: {e}")

try:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workshop_day_override (
            date          TEXT PRIMARY KEY,  -- ISO date YYYY-MM-DD
            is_open       INTEGER,           -- NULL = use default
            max_bookings  INTEGER,           -- NULL = use default
            note          TEXT
        )
    """)
    conn.commit()
    print("workshop_day_override table ready.")
except Exception as e:
    print(f"workshop_day_override: {e}")

# ── Seed workshop_day_config (Mon=0 closed, Tue-Fri open, Sat closed, Sun open)
_day_defaults = [
    (0, 0, 5),  # Mon — closed by default (open in busy season via override)
    (1, 1, 5),  # Tue
    (2, 1, 5),  # Wed
    (3, 1, 5),  # Thu
    (4, 1, 5),  # Fri
    (5, 0, 5),  # Sat — closed
    (6, 1, 5),  # Sun
]
for row in _day_defaults:
    try:
        conn.execute(
            "INSERT OR IGNORE INTO workshop_day_config (day_of_week, is_open, max_bookings) VALUES (?,?,?)",
            row)
    except Exception:
        pass
conn.commit()
print("workshop_day_config seeded.")

# ── Add source columns to jobs ────────────────────────────────────────────────
for col, defn in [
    ('web_source',  "TEXT"),        # 'pista_booking', 'in_service', etc.
    ('web_ref',     "TEXT"),        # external reference / event token
]:
    try:
        conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")
        conn.commit()
        print(f"jobs.{col} added.")
    except Exception:
        pass  # already exists

# ── Extend calendar_events for future In Service days ───────────────────────
for col, defn in [
    ('booking_token',   "TEXT UNIQUE"),
    ('max_bookings',    "INTEGER DEFAULT 0"),
    ('booking_secret',  "TEXT"),
    ('location_name',   "TEXT"),
]:
    try:
        conn.execute(f"ALTER TABLE calendar_events ADD COLUMN {col} {defn}")
        conn.commit()
        print(f"calendar_events.{col} added.")
    except Exception:
        pass  # already exists

# ── workshop_booking job type ─────────────────────────────────────────────────
_idom_row = conn.execute(
    "SELECT value FROM settings WHERE key='internal_email_domain'").fetchone()
_idom = _idom_row['value'] if _idom_row else 'app.internal'
try:
    max_sort = conn.execute(
        "SELECT MAX(sort_order) FROM job_type_config").fetchone()[0] or 0
    conn.execute("""
        INSERT OR IGNORE INTO job_type_config
        (key, label, prefix, hide_customer, hide_address, hide_phone, hide_portal,
         has_service_types, has_bike_description, has_bike_listing, has_end_date,
         use_calendar, use_region, tax_inclusive_default, internal_customer,
         sort_order, active, show_in_new_job)
        VALUES ('workshop_booking','Workshop Booking','WB',0,1,0,0,1,1,0,0,0,0,0,NULL,?,1,0)
    """, (max_sort + 1,))
    conn.commit()
    print("workshop_booking job type seeded.")
except Exception as e:
    print(f"workshop_booking job type: {e}")

# ── Booking secret for Pista Bikes ────────────────────────────────────────────
try:
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('pista_booking_secret','change-me')")
    conn.commit()
    print("pista_booking_secret setting seeded.")
except Exception:
    pass

# ── Email template for workshop booking ack ───────────────────────────────────
try:
    conn.execute("""
        INSERT OR IGNORE INTO email_templates (name, subject, body, grp) VALUES (
            'Workshop Booking Acknowledgement',
            'Your Workshop Booking Request — {{ref}}',
            'Hi {{name}},\n\nThanks for your workshop booking request at Pista Bikes!\n\n'
            'We have received your request for {{date}} and will confirm your appointment shortly.\n\n'
            'Booking reference: {{ref}}\nBike: {{bike}}\n\n'
            'If you need to get in touch:\nPhone: {{phone}}\nEmail: {{email}}\n\n'
            'Pista Bikes\n255 Hawthorn Road, Caulfield',
            'workshop'
        )
    """)
    conn.commit()
    print("Workshop booking ack template seeded.")
except Exception:
    pass
