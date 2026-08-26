"""
fix_email_imports_sender.py

Updates email_imports rows where sender = GMAIL_USER to use the linked
customer's email instead. Flags any customers whose email is still GMAIL_USER
for manual correction.

Usage:
    docker exec -it servicedesk-flask-1 python3 /app/fix_email_imports_sender.py
"""
import os
import sys
import sqlite3

DB_PATH   = '/data/field_service.db'
GMAIL_USER = os.environ.get('GMAIL_USER', '').strip().lower()

if not GMAIL_USER:
    print("ERROR: GMAIL_USER not set in environment")
    sys.exit(1)

print(f"GMAIL_USER = {GMAIL_USER}")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Find all email_imports where sender = GMAIL_USER
rows = conn.execute("""
    SELECT ei.id, ei.job_id, ei.sender, ei.subject,
           j.customer_id, j.customer_email, j.customer_name,
           c.email as cust_email
    FROM email_imports ei
    LEFT JOIN jobs j ON j.id = ei.job_id
    LEFT JOIN customers c ON c.id = j.customer_id
    WHERE LOWER(ei.sender) = ?
    ORDER BY ei.id
""", (GMAIL_USER,)).fetchall()

print(f"\nFound {len(rows)} email_imports with sender = GMAIL_USER\n")

fixed = 0
flagged = 0

for row in rows:
    ei_id      = row['id']
    job_id     = row['job_id']
    cust_email = (row['cust_email'] or '').strip().lower()
    cust_name  = row['customer_name'] or ''
    cust_id    = row['customer_id']

    # Determine the correct sender email
    new_sender = None

    if cust_email and cust_email != GMAIL_USER:
        new_sender = cust_email
    elif row['customer_email'] and row['customer_email'].lower() != GMAIL_USER:
        new_sender = row['customer_email'].lower()

    if new_sender:
        conn.execute(
            "UPDATE email_imports SET sender=? WHERE id=?",
            (new_sender, ei_id))
        print(f"  FIXED  ei.id={ei_id} job_id={job_id} -> sender={new_sender} ({cust_name})")
        fixed += 1
    else:
        # Customer's email is also GMAIL_USER or missing — flag for manual update
        print(f"  FLAG   ei.id={ei_id} job_id={job_id} cust_id={cust_id} "
              f"cust_name='{cust_name}' — customer email is '{cust_email}' — NEEDS MANUAL UPDATE")
        flagged += 1

conn.commit()

print(f"\nSummary: {fixed} fixed, {flagged} flagged for manual update")

if flagged > 0:
    print("\nFlagged customers (email = GMAIL_USER or missing):")
    flagged_custs = conn.execute("""
        SELECT DISTINCT c.id, c.name, c.email
        FROM customers c
        JOIN jobs j ON j.customer_id = c.id
        JOIN email_imports ei ON ei.job_id = j.id
        WHERE LOWER(ei.sender) = ?
           OR LOWER(c.email) = ?
    """, (GMAIL_USER, GMAIL_USER)).fetchall()
    for c in flagged_custs:
        print(f"  customer_id={c['id']} name='{c['name']}' email='{c['email']}'")

conn.close()
print("\nDone.")
