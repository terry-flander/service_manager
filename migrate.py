from models import get_db
with get_db() as conn:
    try: conn.execute('ALTER TABLE jobs ADD COLUMN bike_description TEXT')
    except: pass
conn.commit() 
print('Done')
    