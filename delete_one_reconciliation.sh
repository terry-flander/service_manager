docker exec servicedesk-flask-1 python3 -c "
import sqlite3
REF = '150538001'
conn = sqlite3.connect('/data/field_service.db')
conn.row_factory = sqlite3.Row
row = conn.execute('SELECT * FROM eftpos_transactions WHERE reference_number=?', (REF,)).fetchone()
if not row:
    print('Not found')
else:
    print('Found:', dict(row))
    conn.execute('DELETE FROM eftpos_transactions WHERE reference_number=?', (REF,))
    conn.commit()
    print('Deleted.')
conn.close()
"