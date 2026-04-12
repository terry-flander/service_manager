import sqlite3
conn = sqlite3.connect('field_service.db')
conn.execute("ALTER TABLE jobs ADD COLUMN service_types TEXT;")
conn.execute("CREATE TABLE IF NOT EXISTS email_imports (id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE, subject TEXT, sender TEXT, imported_at TEXT DEFAULT (datetime('now')), job_id INTEGER REFERENCES jobs(id), status TEXT DEFAULT 'ok');")
conn.commit()

import sqlite3
conn = sqlite3.connect('field_service.db')
conn.execute("ALTER TABLE jobs ADD COLUMN service_types TEXT;")
conn.execute("CREATE TABLE IF NOT EXISTS email_imports (id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE, subject TEXT, sender TEXT, imported_at TEXT DEFAULT (datetime('now')), job_id INTEGER REFERENCES jobs(id), status TEXT DEFAULT 'ok');")
conn.commit()