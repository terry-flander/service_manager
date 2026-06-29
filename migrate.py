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
    try: conn.execute('ALTER TABLE jobs ADD COLUMN subtotal REAL DEFAULT 0')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN gst REAL DEFAULT 0')
    except: pass
    try: conn.execute('ALTER TABLE jobs ADD COLUMN total REAL DEFAULT 0')
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
