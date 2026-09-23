"""
Database layer using Python's built-in sqlite3 module.
No external dependencies required beyond Flask.
"""
import sqlite3
import os

# In Docker the /data volume is mounted for persistence.
# Locally it falls back to the project directory.
_data_dir = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH   = os.path.join(_data_dir, 'field_service.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """
    Create all tables if they don't already exist.
    Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS throughout.
    The DB file itself is created by sqlite3.connect() only when first accessed.
    """
    # Ensure the data directory exists (important when DATA_DIR=/data in Docker)
    os.makedirs(_data_dir, exist_ok=True)

    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                phone         TEXT,
                role          TEXT NOT NULL DEFAULT 'mechanic',
                totp_secret   TEXT,
                totp_enabled  INTEGER NOT NULL DEFAULT 0,
                require_2fa         INTEGER NOT NULL DEFAULT 0,
                show_cash_payments  INTEGER NOT NULL DEFAULT 0,
                must_change_pw      INTEGER NOT NULL DEFAULT 1,
                active        INTEGER NOT NULL DEFAULT 1,
                theme         TEXT NOT NULL DEFAULT 'dark',
                created_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS regions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                name      TEXT NOT NULL UNIQUE,
                visit_day TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS suburbs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
                name      TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS region_dates (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
                date      TEXT NOT NULL,
                status    TEXT NOT NULL DEFAULT 'open',
                gcal_event_id TEXT,
                UNIQUE(region_id, date)
            );

            CREATE TABLE IF NOT EXISTS parts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                part_number       TEXT UNIQUE,
                unit_cost         REAL NOT NULL DEFAULT 0.0,
                unit              TEXT DEFAULT 'each',
                active            INTEGER DEFAULT 1,
                part_type         TEXT DEFAULT 'stock',
                avg_cost_inc_gst  REAL DEFAULT 0,
                reorder_point     REAL DEFAULT 0,
                reorder_qty       REAL DEFAULT 0,
                supplier_id       INTEGER
            );

            CREATE TABLE IF NOT EXISTS service_types (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT NOT NULL UNIQUE,
                label       TEXT NOT NULL,
                description TEXT,
                keywords    TEXT,
                part_id     INTEGER REFERENCES parts(id) ON DELETE SET NULL,
                active      INTEGER DEFAULT 1,
                sort_order  INTEGER DEFAULT 0,
                job_group   TEXT DEFAULT 'booking'
            );

            CREATE TABLE IF NOT EXISTS locations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                job_type    TEXT,
                active      INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS suppliers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                email       TEXT,
                phone       TEXT,
                website     TEXT,
                notes       TEXT,
                active      INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS supplier_parts (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier_id          INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
                part_id              INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
                supplier_sku         TEXT,
                supplier_description TEXT,
                last_price_inc_gst   REAL,
                last_ordered_at      TEXT,
                UNIQUE(supplier_id, supplier_sku)
            );

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
            );

            CREATE TABLE IF NOT EXISTS purchase_order_lines (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id        INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
                part_id         INTEGER REFERENCES parts(id),
                supplier_sku    TEXT,
                supplier_desc   TEXT NOT NULL,
                qty_ordered     REAL NOT NULL,
                qty_received    REAL DEFAULT 0,
                price_inc_gst   REAL NOT NULL,
                location_id     INTEGER REFERENCES locations(id)
            );

            CREATE TABLE IF NOT EXISTS part_location_stock (
                part_id     INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
                location_id INTEGER NOT NULL REFERENCES locations(id),
                qty_on_hand REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (part_id, location_id)
            );

            CREATE TABLE IF NOT EXISTS inventory_counts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                count_date  TEXT NOT NULL,
                location_id INTEGER REFERENCES locations(id),
                status      TEXT DEFAULT 'open',
                notes       TEXT,
                created_by  INTEGER REFERENCES users(id),
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS inventory_count_lines (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                count_id    INTEGER NOT NULL REFERENCES inventory_counts(id) ON DELETE CASCADE,
                part_id     INTEGER NOT NULL REFERENCES parts(id),
                location_id INTEGER REFERENCES locations(id),
                qty_system  REAL NOT NULL,
                qty_counted REAL,
                adjustment  REAL,
                notes       TEXT
            );

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
            );

            CREATE TABLE IF NOT EXISTS customers (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT NOT NULL UNIQUE,
                name       TEXT NOT NULL,
                phone      TEXT,
                suburb     TEXT,
                address    TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS job_status_triggers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type    TEXT NOT NULL,
                trigger_status TEXT NOT NULL,
                template_id INTEGER REFERENCES email_templates(id) ON DELETE SET NULL,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(job_type, trigger_status)
            );

            CREATE TABLE IF NOT EXISTS customer_contacts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                phone       TEXT,
                email       TEXT,
                notes       TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                reference      TEXT NOT NULL UNIQUE,
                customer_id    INTEGER REFERENCES customers(id),
                customer_name  TEXT NOT NULL,
                customer_email TEXT,
                customer_phone TEXT,
                address        TEXT,
                description    TEXT,
                region_id      INTEGER NOT NULL REFERENCES regions(id),
                suburb         TEXT,
                job_type       TEXT NOT NULL DEFAULT 'booking',
                tax_inclusive  INTEGER NOT NULL DEFAULT 1,
                scheduled_date TEXT,
                scheduled_time TEXT,
                end_time       TEXT,
                end_date       TEXT,
                invoice_number TEXT,
                status         TEXT DEFAULT 'pending',
                created_at     TEXT DEFAULT (datetime('now')),
                notes          TEXT,
                paid_date      TEXT,
                amount_paid    REAL,
                service_types  TEXT,
                payment_type   TEXT,
                bike_description TEXT,
                reconciled_eftpos TEXT,
                gcal_event_id     TEXT,
                add_to_calendar   INTEGER NOT NULL DEFAULT 0,
                referral_source   TEXT,
                subtotal          REAL DEFAULT 0,
                gst               REAL DEFAULT 0,
                total             REAL DEFAULT 0,
                portal_token      TEXT
            );

            CREATE TABLE IF NOT EXISTS eftpos_transactions (
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
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO settings (key, value) VALUES ('email_polling', 'on');

            CREATE TABLE IF NOT EXISTS column_visibility_sets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                page        TEXT NOT NULL,
                desktop     TEXT,
                landscape   TEXT,
                portrait    TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS job_queries (
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
                sort1_field   TEXT,
                sort1_dir     TEXT,
                sort2_field   TEXT,
                sort2_dir     TEXT,
                sort3_field   TEXT,
                sort3_dir     TEXT,
                date_field    TEXT NOT NULL DEFAULT 'scheduled',
                column_visibility_id INTEGER REFERENCES column_visibility_sets(id),
                created_at    TEXT DEFAULT (datetime('now')),
                updated_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS email_imports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id  TEXT NOT NULL UNIQUE,
                thread_id   TEXT,
                in_reply_to TEXT,
                subject     TEXT,
                sender      TEXT,
                body        TEXT,
                imported_at TEXT DEFAULT (datetime('now')),
                received_at TEXT,
                job_id      INTEGER REFERENCES jobs(id),
                status      TEXT DEFAULT 'ok',
                read        INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS email_import_attachments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                email_import_id INTEGER NOT NULL REFERENCES email_imports(id) ON DELETE CASCADE,
                filename        TEXT NOT NULL,
                filepath        TEXT NOT NULL,
                mime_type       TEXT,
                size_bytes      INTEGER,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS calendar_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                start_time  TEXT,
                end_time    TEXT,
                title       TEXT NOT NULL,
                description TEXT,
                address     TEXT,
                color       TEXT DEFAULT '#6366f1',
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS email_templates (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                subject    TEXT NOT NULL,
                body       TEXT NOT NULL,
                grp        TEXT NOT NULL DEFAULT 'misc',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

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
            );

            CREATE TABLE IF NOT EXISTS bike_images (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                bike_id    INTEGER NOT NULL REFERENCES bikes_for_sale(id) ON DELETE CASCADE,
                filename   TEXT NOT NULL,
                filepath   TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS email_replies (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER NOT NULL REFERENCES jobs(id),
                message_id  TEXT UNIQUE,
                in_reply_to TEXT,
                subject     TEXT,
                to_address  TEXT,
                body        TEXT,
                sent_at     TEXT DEFAULT (datetime('now')),
                sent_by     INTEGER REFERENCES users(id),
                template_id INTEGER REFERENCES email_templates(id),
                contact_id  INTEGER REFERENCES customer_contacts(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS job_parts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                part_id     INTEGER REFERENCES parts(id),
                description TEXT NOT NULL,
                part_number TEXT,
                quantity    REAL NOT NULL DEFAULT 1,
                unit_cost   REAL NOT NULL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS workshop_day_config (
                day_of_week   INTEGER PRIMARY KEY,
                is_open       INTEGER NOT NULL DEFAULT 0,
                max_bookings  INTEGER NOT NULL DEFAULT 5
            );

            CREATE TABLE IF NOT EXISTS workshop_day_override (
                date          TEXT PRIMARY KEY,
                is_open       INTEGER,
                max_bookings  INTEGER,
                note          TEXT
            );

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
            );

            CREATE TABLE IF NOT EXISTS job_type_config (
                key                   TEXT PRIMARY KEY,
                label                 TEXT NOT NULL,
                prefix                TEXT NOT NULL,
                hide_customer         INTEGER NOT NULL DEFAULT 0,
                hide_address          INTEGER NOT NULL DEFAULT 0,
                hide_phone            INTEGER NOT NULL DEFAULT 0,
                hide_portal           INTEGER NOT NULL DEFAULT 0,
                has_service_types     INTEGER NOT NULL DEFAULT 0,
                has_bike_description  INTEGER NOT NULL DEFAULT 1,
                has_bike_listing      INTEGER NOT NULL DEFAULT 0,
                has_end_date          INTEGER NOT NULL DEFAULT 0,
                use_calendar          INTEGER NOT NULL DEFAULT 0,
                use_region            INTEGER NOT NULL DEFAULT 0,
                tax_inclusive_default INTEGER NOT NULL DEFAULT 0,
                internal_customer     TEXT,
                sort_order            INTEGER NOT NULL DEFAULT 0,
                active                INTEGER NOT NULL DEFAULT 1,
                show_in_new_job       INTEGER NOT NULL DEFAULT 1
            );
        """)

        # Migration: populate customers from existing jobs if customers table is empty
        # but jobs table already has data (upgrading from previous schema version)
        cust_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        job_count  = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        if cust_count == 0 and job_count > 0:
            conn.execute("""
                INSERT OR IGNORE INTO customers (email, name, phone, suburb)
                SELECT DISTINCT
                    COALESCE(NULLIF(customer_email,''), 'unknown_' || id || '@migrated.local'),
                    customer_name,
                    customer_phone,
                    suburb
                FROM jobs
                WHERE customer_name IS NOT NULL
            """)
            conn.execute("""
                UPDATE jobs SET customer_id = (
                    SELECT c.id FROM customers c
                    WHERE c.email = jobs.customer_email
                       OR c.name  = jobs.customer_name
                    LIMIT 1
                )
                WHERE customer_id IS NULL
            """)
            conn.commit()


def get_settings(conn=None):
    """Return all settings as a dict. Provides defaults for all business identity keys."""
    _defaults = {
        'business_name':         'ServiceDesk',
        'business_tagline':      '',
        'business_abn':          '',
        'business_phone':        '',
        'business_email':        '',
        'business_address':      '',
        'business_suburb':       '',
        'business_state':        '',
        'business_postcode':     '',
        'business_website':      '',
        'business_instagram':    '',
        'business_bank_name':    '',
        'app_url':               'http://localhost:5000',
        'booking_secret':        'change-me',
        'booking_cors_origins':  '',
        'internal_email_domain': 'app.internal',
        'setup_complete':        '0',
        'gcal_enabled':          '0',
        'bikes_sold_days':       '30',
        'email_polling':         'on',
    }

    def _fetch(c):
        rows = c.execute("SELECT key, value FROM settings").fetchall()
        result = dict(_defaults)
        result.update({r['key']: r['value'] for r in rows})
        return result

    if conn is not None:
        return _fetch(conn)
    with get_db() as c:
        return _fetch(c)


# ── Job type config ───────────────────────────────────────────────────────────
_JOB_TYPE_DEFAULTS = [
    # key, label, prefix, hide_cust, hide_addr, hide_phone, hide_portal,
    # has_svc, has_bike_desc, has_bike_listing, has_end_date,
    # use_cal, use_region, tax_incl, internal_customer, sort, show_in_new_job
    ('booking',   'Booking',       'FB', 0, 0, 0, 0, 0, 1, 0, 0, 1, 1, 0, None,                          0, 1),
    ('workshop',  'Workshop',      'PB', 0, 1, 0, 0, 1, 1, 0, 0, 0, 0, 0, None,                          1, 1),
    ('rental',    'Rental',        'RB', 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, None,                          2, 1),
    ('sale',      'Sale',          'CS', 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 'counter.sales@app.internal',  3, 0),
    ('sale_bike', 'Bike for Sale', 'BK', 1, 1, 1, 1, 0, 1, 1, 0, 0, 0, 1, 'bikes.for.sale@app.internal', 4, 1),
]


def get_job_types(conn=None):
    """Return job type config as dict keyed by job type key.
    Reads from job_type_config table; falls back to defaults if table empty.
    Each value is a dict with all config fields plus convenience booleans.
    """
    def _load(c):
        rows = c.execute(
            "SELECT * FROM job_type_config WHERE active=1 ORDER BY sort_order"
        ).fetchall()
        if not rows:
            return None
        result = {}
        for r in rows:
            result[r['key']] = dict(r)
        return result

    def _defaults():
        result = {}
        cols = ['key','label','prefix','hide_customer','hide_address','hide_phone',
                'hide_portal','has_service_types','has_bike_description',
                'has_bike_listing','has_end_date','use_calendar','use_region',
                'tax_inclusive_default','internal_customer','sort_order','show_in_new_job']
        for row in _JOB_TYPE_DEFAULTS:
            d = dict(zip(cols, row))
            d['active'] = 1
            result[d['key']] = d
        return result

    try:
        if conn is not None:
            return _load(conn) or _defaults()
        with get_db() as c:
            return _load(c) or _defaults()
    except Exception:
        return _defaults()


def get_workshop_capacity(date_str, conn=None):
    """
    Return dict {is_open, max_bookings, booked, remaining} for a given ISO date.
    Checks override first, falls back to day-of-week config.
    booked = count of non-lost workshop/workshop_booking jobs on that date.
    """
    from datetime import date as _date
    import datetime as _dt

    def _fetch(c):
        # Override takes priority
        ov = c.execute(
            "SELECT is_open, max_bookings FROM workshop_day_override WHERE date=?",
            (date_str,)).fetchone()

        if ov is not None:
            is_open      = ov['is_open'] if ov['is_open'] is not None else _dow_default(c, date_str)[0]
            max_bookings = ov['max_bookings'] if ov['max_bookings'] is not None else _dow_default(c, date_str)[1]
        else:
            is_open, max_bookings = _dow_default(c, date_str)

        booked = c.execute("""
            SELECT COUNT(*) FROM jobs
            WHERE job_type IN ('workshop', 'workshop_booking')
            AND scheduled_date = ?
            AND status != 'lost'
        """, (date_str,)).fetchone()[0]

        return {
            'date':         date_str,
            'is_open':      bool(is_open),
            'max_bookings': max_bookings,
            'booked':       booked,
            'remaining':    max(0, max_bookings - booked),
        }

    def _dow_default(c, ds):
        try:
            d   = _date.fromisoformat(ds)
            dow = d.weekday()  # 0=Mon
        except Exception:
            return (0, 5)
        row = c.execute(
            "SELECT is_open, max_bookings FROM workshop_day_config WHERE day_of_week=?",
            (dow,)).fetchone()
        if row:
            return (row['is_open'], row['max_bookings'])
        return (0, 5)

    if conn is not None:
        return _fetch(conn)
    with get_db() as c:
        return _fetch(c)


def get_workshop_available_dates(from_date_str=None, weeks=8, conn=None):
    """
    Return list of capacity dicts for the next `weeks` weeks starting from from_date.
    Only returns open days with remaining capacity > 0.
    """
    from datetime import date as _date, timedelta as _td
    start = _date.today() if not from_date_str else _date.fromisoformat(from_date_str)
    # Start from tomorrow at minimum
    if start <= _date.today():
        start = _date.today() + _td(days=1)
    end   = start + _td(weeks=weeks)

    def _fetch(c):
        results = []
        cur = start
        while cur <= end:
            cap = get_workshop_capacity(cur.isoformat(), conn=c)
            results.append(cap)
            cur += _td(days=1)
        return results

    if conn is not None:
        return _fetch(conn)
    with get_db() as c:
        return _fetch(c)
