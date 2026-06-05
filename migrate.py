from models import get_db
with get_db() as conn:
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
conn.commit() 
print('Done')
    
