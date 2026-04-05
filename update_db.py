import sqlite3
conn = sqlite3.connect('field_service.db')
conn.execute("ALTER TABLE jobs ADD COLUMN end_time TEXT")
conn.commit()