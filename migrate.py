from models import get_db
with get_db() as conn:
    try: conn.execute('ALTER TABLE email_imports ADD COLUMN read INTEGER DEFAULT 1')
    except: pass
    try: conn.execute('ALTER TABLE email_imports ADD COLUMN received_at TEXT')
    except: pass
    # Mark all existing imports as read so inbox starts clean
    try: conn.execute('UPDATE email_imports SET read=0 WHERE read IS NULL')
    except: pass
    try: conn.commit()
    except: pass
print('Done')
    