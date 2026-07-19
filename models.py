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
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                part_number TEXT UNIQUE,
                unit_cost   REAL NOT NULL DEFAULT 0.0,
                unit        TEXT DEFAULT 'each',
                active      INTEGER DEFAULT 1
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
                total             REAL DEFAULT 0
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
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
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
