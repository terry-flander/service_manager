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

print(f"Migration complete. {cleaned} workshop job(s) had service_types cleaned.")
