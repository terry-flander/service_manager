"""
Database layer using Python's built-in sqlite3 module.
No external dependencies required beyond Flask.
"""
import sqlite3
import os
import re

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

            -- Configurable job statuses. Starts EMPTY: an empty table means the
            -- app uses the hard-coded defaults in _JOB_STATUS_DEFAULTS and
            -- behaves exactly as before. The Job Statuses settings page seeds
            -- it on first save. See get_job_statuses().
            CREATE TABLE IF NOT EXISTS job_statuses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                code            TEXT NOT NULL UNIQUE,
                label           TEXT NOT NULL,
                job_types       TEXT,              -- NULL/'' = all; else comma-separated keys
                sort_order      INTEGER NOT NULL DEFAULT 0,
                badge_color     TEXT,              -- hex; NULL = default colour
                special_meaning TEXT,              -- NULL or one of STATUS_MEANINGS
                builtin         INTEGER NOT NULL DEFAULT 0,
                active          INTEGER NOT NULL DEFAULT 1
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


# ── Job statuses ──────────────────────────────────────────────────────────────
# Special meanings are a controlled vocabulary: code paths that need to know
# "is this job paid / invoiced / lost / done?" ask status_has_meaning() or
# status_codes_for() rather than testing a literal string. A custom status
# with special_meaning='lost' (e.g. 'no_show') is then hidden from the job
# list and calendar exactly like 'lost'. The literal built-in code always
# carries its own meaning, so these helpers never return LESS than the old
# hard-coded checks did.
STATUS_MEANINGS = ('complete', 'invoiced', 'paid', 'lost')
STATUS_MEANING_LABELS = {
    'complete': 'Complete — work done (enables Send to Xero, locks schedule)',
    'invoiced': 'Invoiced — invoice issued (portal invoice link, sales report)',
    'paid':     'Paid — money received (sales report, EFTPOS reconciliation)',
    'lost':     'Lost — cancelled (hidden from job list, calendar, schedule)',
}
STATUS_CODE_RE = re.compile(r'^[a-z][a-z0-9_]{0,30}$')
STATUS_FALLBACK_COLOR = '#94a3b8'

_JOB_STATUS_DEFAULTS = [
    # code,         label,         default colour, special_meaning, sort
    ('pending',     'Pending',     '#f59e0b', None,       1),
    ('scheduled',   'Scheduled',   '#3b82f6', None,       2),
    ('in_progress', 'In Progress', '#8b5cf6', None,       3),
    ('quote',       'Quote',       '#0ea5e9', None,       4),
    ('complete',    'Complete',    '#10b981', 'complete', 5),
    ('invoiced',    'Invoiced',    '#6b7280', 'invoiced', 6),
    ('paid',        'Paid',        '#10b981', 'paid',     7),
    ('lost',        'Lost',        '#ef4444', 'lost',     8),
]
BUILTIN_STATUS_CODES = tuple(r[0] for r in _JOB_STATUS_DEFAULTS)
_DEFAULT_STATUS_COLORS = {r[0]: r[2] for r in _JOB_STATUS_DEFAULTS}


def _legacy_status_color(c, code):
    """Colour saved by the old Status Colours page (settings table), if any."""
    keys = [f'status_color_{code}']
    if code == 'lost':
        keys.append('status_color_void')  # pre-'lost' rename
    for k in keys:
        row = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        if row and (row['value'] or '').strip():
            return row['value'].strip()
    return None


def default_job_statuses(conn=None):
    """The hard-coded statuses as dicts, with any colours saved in settings
    by the old Status Colours page applied. Used both as the fallback when
    job_statuses is empty and as the seed rows."""
    def _build(c):
        out = []
        for code, label, color, meaning, sort in _JOB_STATUS_DEFAULTS:
            saved = None
            if c is not None:
                try:
                    saved = _legacy_status_color(c, code)
                except Exception:
                    saved = None
            out.append({
                'code': code, 'label': label, 'job_types': [],
                'sort_order': sort, 'badge_color': saved or color,
                'special_meaning': meaning, 'builtin': 1, 'active': 1,
            })
        return out
    try:
        if conn is not None:
            return _build(conn)
        with get_db() as c:
            return _build(c)
    except Exception:
        return _build(None)


def _row_to_status(r):
    d = dict(r)
    d['job_types'] = [j.strip() for j in (d.get('job_types') or '').split(',') if j.strip()]
    d['badge_color'] = d.get('badge_color') or _DEFAULT_STATUS_COLORS.get(d['code'], STATUS_FALLBACK_COLOR)
    d['special_meaning'] = d.get('special_meaning') or None
    return d


def get_job_statuses(conn=None, include_inactive=False):
    """Return the job status list (dicts, sorted).

    If the job_statuses table is missing or empty, returns the hard-coded
    defaults — callers can't tell which path ran. Results are cached for the
    duration of a Flask request.

    Each dict: code, label, job_types (list; empty = all types), sort_order,
    badge_color (resolved hex), special_meaning, builtin, active.
    """
    cache_key = '_job_statuses_all' if include_inactive else '_job_statuses_active'
    try:
        from flask import g, has_app_context
        use_cache = has_app_context()
    except Exception:
        use_cache = False
    if use_cache and cache_key in g:
        return g.get(cache_key)

    def _load(c):
        try:
            rows = c.execute(
                "SELECT * FROM job_statuses ORDER BY sort_order, id").fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            return default_job_statuses(c)
        result = [_row_to_status(r) for r in rows]
        if not include_inactive:
            result = [s for s in result if s['active']]
        return result

    try:
        if conn is not None:
            result = _load(conn)
        else:
            with get_db() as c:
                result = _load(c)
    except Exception:
        result = default_job_statuses(None)

    if use_cache:
        setattr(g, cache_key, result)
    return result


def job_statuses_customised(conn=None):
    """True if the job_statuses table has rows (i.e. dynamic mode)."""
    def _q(c):
        try:
            return c.execute("SELECT COUNT(*) FROM job_statuses").fetchone()[0] > 0
        except sqlite3.OperationalError:
            return False
    if conn is not None:
        return _q(conn)
    with get_db() as c:
        return _q(c)


def status_codes_for(meaning, conn=None):
    """All status codes carrying a special meaning — always includes the
    built-in literal (e.g. 'lost'), plus any custom codes, active or not
    (an inactive custom status still describes historical jobs)."""
    codes = [meaning] if meaning in STATUS_MEANINGS else []
    for s in get_job_statuses(conn, include_inactive=True):
        if s['special_meaning'] == meaning and s['code'] not in codes:
            codes.append(s['code'])
    return codes


def status_has_meaning(code, meaning, conn=None):
    """True if `code` is the built-in literal for `meaning` or a custom
    status mapped to it."""
    if not code:
        return False
    if code == meaning:
        return True
    return code in status_codes_for(meaning, conn)


def status_sql_list(*meanings, conn=None):
    """SQL literal list for use in `status IN (...)` / `NOT IN (...)`,
    e.g. "'lost','no_show'". Codes are validated against STATUS_CODE_RE
    on save and re-checked here, so inlining them is safe."""
    codes = []
    for m in meanings:
        for c in status_codes_for(m, conn):
            if STATUS_CODE_RE.match(c) and c not in codes:
                codes.append(c)
    return ','.join(f"'{c}'" for c in codes) or "''"


def status_colors_map(conn=None):
    """{code: hex} for every status (including inactive)."""
    return {s['code']: s['badge_color']
            for s in get_job_statuses(conn, include_inactive=True)}


def status_labels_map(conn=None):
    return {s['code']: s['label']
            for s in get_job_statuses(conn, include_inactive=True)}


def status_label(code, conn=None):
    """Display label for a status code; falls back to 'In Progress' style
    for unknown/historical codes."""
    if not code:
        return ''
    return status_labels_map(conn).get(code) or str(code).replace('_', ' ').title()


def statuses_for_job_type(job_type, current=None, conn=None):
    """Active statuses applicable to a job type, for status pickers.
    The job's current status is always included (even if inactive or
    restricted to another type) so saving the form can't silently change it."""
    result = [s for s in get_job_statuses(conn)
              if not s['job_types'] or (job_type and job_type in s['job_types'])]
    if current and current not in [s['code'] for s in result]:
        known = {s['code']: s for s in get_job_statuses(conn, include_inactive=True)}
        result.append(known.get(current) or {
            'code': current, 'label': status_label(current, conn),
            'badge_color': STATUS_FALLBACK_COLOR, 'special_meaning': None,
            'job_types': [], 'active': 0, 'builtin': 0, 'sort_order': 999})
    return result


def clear_job_status_cache():
    """Drop the per-request cache after editing job_statuses."""
    try:
        from flask import g, has_app_context
        if has_app_context():
            g.pop('_job_statuses_all', None)
            g.pop('_job_statuses_active', None)
    except Exception:
        pass


def seed_job_statuses(conn):
    """Copy the effective defaults into job_statuses if it's empty.
    Returns True if rows were inserted."""
    if job_statuses_customised(conn):
        return False
    for s in default_job_statuses(conn):
        conn.execute("""
            INSERT OR IGNORE INTO job_statuses
                (code, label, job_types, sort_order, badge_color,
                 special_meaning, builtin, active)
            VALUES (?,?,NULL,?,?,?,1,1)
        """, (s['code'], s['label'], s['sort_order'], s['badge_color'],
              s['special_meaning']))
    conn.commit()
    clear_job_status_cache()
    return True


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

        booked = c.execute(f"""
            SELECT COUNT(*) FROM jobs
            WHERE job_type IN ('workshop', 'workshop_booking')
            AND scheduled_date = ?
            AND status NOT IN ({status_sql_list('lost', conn=c)})
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
