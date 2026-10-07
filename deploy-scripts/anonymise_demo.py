#!/usr/bin/env python3
"""
anonymise_demo.py — turn a copy of the production database into demo data.

Run ON THE STAGING / DEMO SERVER, inside the container:

    docker compose exec -T flask python3 /app/scripts/anonymise_demo.py                     # dry run
    docker compose exec -T flask python3 /app/scripts/anonymise_demo.py --apply --keep-user you@keepcroft.com.au

What it changes (everything else — jobs, parts, prices, dates, statuses,
suburbs, regions, bikes for sale, settings you've made — is left alone):

  customers / jobs / contacts   names, emails, phones, street addresses → fake
                                (suburb kept so the map still works)
  job text (description, notes, bike description)
                                emails, phone numbers and the customer's real
                                name scrubbed out of the text
  email threads                 addresses → fake; message text → generic demo
                                messages; attachment files deleted
  SMS log                       numbers → fake; text → generic
  calendar events               text scrubbed, address → fake
  EFTPOS card numbers           masked
  portal links                  new tokens (old customer links stop working)
  staff logins                  every user except --keep-user gets a fake name,
                                an @example.com email, a fake phone, a random
                                password and 2FA cleared
  settings                      bank BSB / account → placeholders,
                                Xero token removed

Safe to run more than once: fake values are derived from each record's id,
so a second run produces exactly the same result. A backup is written to
/data/backups/ before anything changes.

Fake contact details are unusable by design: emails use example.com
(reserved, never delivers) and phones use the 03 5550 xxxx range set
aside by ACMA for fictional use.
"""
import argparse
import hashlib
import os
import re
import secrets
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, '/app')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# No names that are also ordinary words (Bell, Wood, Grace, Max…) so scrubbing
# free text can never mangle a job description.
FIRST = ['Olivia', 'Jack', 'Charlotte', 'Noah', 'Amelia', 'William', 'Isla', 'Oliver',
         'Mia', 'Thomas', 'Ava', 'James', 'Lucas', 'Chloe', 'Henry', 'Zoe', 'Leo',
         'Harrison', 'Sophie', 'Archie', 'Ella', 'Lachlan', 'Matilda', 'Ethan', 'Evie',
         'Samuel', 'Hugo', 'Oscar', 'Finn', 'Hannah', 'Patrick', 'Priya', 'Mei', 'Kenji',
         'Fatima', 'Omar', 'Sienna', 'Marco', 'Elena', 'Nikos', 'Anh', 'Isabelle', 'Riley']
LAST = ['Smith', 'Nguyen', 'Wilson', 'Taylor', 'Johnson', 'Anderson', 'Thompson',
        'Harris', 'Ryan', 'Robinson', 'Kelly', 'Davis', 'Evans', 'Roberts', 'Jackson',
        'Clarke', 'Patel', 'Khan', 'Chen', 'Wang', 'Singh', 'Murphy', "O'Brien", 'Mitchell',
        'Campbell', 'Morris', 'Russo', 'Papadopoulos', 'Tran', 'Hughes', 'Edwards',
        'Collins', 'Stewart', 'Morgan', 'Bailey', 'Kowalski', 'Lombardi', 'Okafor']
STREETS = ['Acacia', 'Banksia', 'Wattle', 'Station', 'Church', 'Park', 'High', 'Railway',
           'Victoria', 'Albert', 'George', 'William', 'King', 'Queen', 'Elm', 'Oak',
           'Grevillea', 'Bay', 'Beach', 'Hill', 'Union', 'Pier', 'Market', 'Garden']
STREET_TYPES = ['Street', 'Road', 'Avenue', 'Parade', 'Grove', 'Court', 'Crescent', 'Lane']

INBOUND = [
    "Hi, just checking in on how the job is going. Thanks!",
    "Hello, could we move the booking to later in the week if that's easier?",
    "Thanks for the quote. Please go ahead with the work.",
    "Hi there, is it ready to pick up yet?",
    "Thanks again, all working perfectly now.",
    "Hi, can you let me know roughly what this will cost?",
]
OUTBOUND = [
    "Hi, thanks for getting in touch. We'll have it ready by Thursday.",
    "Hi, your booking is confirmed. See you then.",
    "Hi, here's the quote for the work we discussed. Let us know if you'd like to go ahead.",
    "Hi, all done and ready to collect. Thanks for your business.",
    "Hi, we've moved your booking as requested.",
]
SMS = ["Your booking is confirmed for tomorrow.", "Your job is ready to collect.",
       "We're on our way, about 15 minutes away."]

EMAIL_RE = re.compile(r'[\w.+\'-]+@[\w-]+(?:\.[\w-]+)+')
PHONE_RE = re.compile(r'(?<!\d)(?:\+?61[ -]?|0)[2-478](?:[ -]?\d){8}(?!\d)')


# ── deterministic fakes ──────────────────────────────────────────────────────
def h(*parts):
    return int(hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest(), 16)

def fake_name(kind, key):
    n = h('name', kind, key)
    return f"{FIRST[n % len(FIRST)]} {LAST[(n // 97) % len(LAST)]}"

def fake_email(kind, key, name=None):
    name = name or fake_name(kind, key)
    local = re.sub(r'[^a-z.]', '', name.lower().replace(' ', '.'))
    return f"{local}.{kind[0]}{key}@example.com"

def fake_phone(kind, key):
    return f"035550{h('phone', kind, key) % 10000:04d}"

def fake_address(kind, key):
    n = h('addr', kind, key)
    return (f"{n % 180 + 1} {STREETS[(n // 7) % len(STREETS)]} "
            f"{STREET_TYPES[(n // 131) % len(STREET_TYPES)]}")

def pick(options, *key):
    return options[h(*key) % len(options)]


def scrub(text, real_names=(), replace_name='the customer', kind='x', key=0):
    """Remove emails, phone numbers and known real names from free text."""
    if not text:
        return text
    text = EMAIL_RE.sub(lambda m: m.group(0) if m.group(0).endswith('@example.com')
                        else fake_email(kind, key), text)
    text = PHONE_RE.sub(fake_phone(kind, key), text)
    for real in real_names:
        for part in {real, *real.split()}:
            part = part.strip()
            if len(part) >= 3 and part.lower() not in ('the', 'and', 'customer'):
                text = re.sub(r'\b' + re.escape(part) + r'\b', replace_name, text,
                              flags=re.I)
    return text


def is_internal(email):
    return bool(email) and email.lower().endswith('.internal')


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--apply', action='store_true', help='make the changes (default: dry run)')
    ap.add_argument('--keep-user', action='append', default=[],
                    help='login email to leave untouched (repeatable). Required with --apply.')
    ap.add_argument('--db', default=None, help='database path (default: the app\'s DB)')
    args = ap.parse_args()

    base_url = os.environ.get('BASE_URL', '')
    if 'theflyingbike' in base_url or os.environ.get('ALLOW_ANONYMISE') == 'no':
        sys.exit(f"Refusing: BASE_URL is {base_url!r} — this looks like production.")

    if args.db:
        db_path = args.db
    else:
        import models
        db_path = models.DB_PATH
    data_dir = os.path.dirname(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    keep = {e.strip().lower() for e in args.keep_user}

    if args.apply:
        if not keep:
            sys.exit("--apply needs --keep-user <your login email>, or you'd be locked out.")
        found = {r['email'].lower() for r in conn.execute("SELECT email FROM users")}
        missing = keep - found
        if missing:
            sys.exit(f"--keep-user not found in users: {', '.join(missing)}")
        os.makedirs(os.path.join(data_dir, 'backups'), exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        bpath = os.path.join(data_dir, 'backups', f'pre_anonymise_{stamp}.db')
        dst = sqlite3.connect(bpath); conn.backup(dst); dst.close()
        print(f"Backup written: {bpath}")

    def cols(table):
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.DatabaseError:
            return set()

    counts = {}
    def upd(table, sql, params):
        counts[table] = counts.get(table, 0) + 1
        if args.apply:
            conn.execute(sql, params)

    # ── customers ─────────────────────────────────────────────────────────
    real_names_by_customer = {}
    for c in conn.execute("SELECT * FROM customers").fetchall():
        if is_internal(c['email']):
            continue
        # Already anonymised on an earlier run → no real name left to scrub
        done = (c['email'] or '').endswith('@example.com')
        real_names_by_customer[c['id']] = '' if done else (c['name'] or '')
        name = fake_name('customer', c['id'])
        upd('customers', "UPDATE customers SET name=?, email=?, phone=?, address=? WHERE id=?",
            (name, fake_email('customer', c['id'], name), fake_phone('customer', c['id']),
             fake_address('customer', c['id']) if c['address'] else c['address'], c['id']))

    # ── contacts ──────────────────────────────────────────────────────────
    if cols('customer_contacts'):
        for c in conn.execute("SELECT * FROM customer_contacts").fetchall():
            name = fake_name('contact', c['id'])
            upd('customer_contacts',
                "UPDATE customer_contacts SET name=?, email=?, phone=?, notes=? WHERE id=?",
                (name, fake_email('contact', c['id'], name), fake_phone('contact', c['id']),
                 scrub(c['notes'], [] if (c['email'] or '').endswith('@example.com')
                       else [c['name'] or ''], name, 'contact', c['id']), c['id']))

    # ── jobs ──────────────────────────────────────────────────────────────
    jcols = cols('jobs')
    job_customer_email = {}
    job_names = {}          # job id → (real names to scrub, fake name)
    for j in conn.execute("SELECT * FROM jobs").fetchall():
        if is_internal(j['customer_email']):
            continue
        cid = j['customer_id']
        if cid and cid in real_names_by_customer:
            name = fake_name('customer', cid)
            email = fake_email('customer', cid, name)
            phone = fake_phone('customer', cid)
            addr = fake_address('customer', cid)
        else:
            name = fake_name('job', j['id'])
            email = fake_email('job', j['id'], name)
            phone = fake_phone('job', j['id'])
            addr = fake_address('job', j['id'])
        job_customer_email[j['id']] = email
        done = (j['customer_email'] or '').endswith('@example.com') or \
               (j['customer_name'] or '') == name
        reals = [] if done else [j['customer_name'] or '', real_names_by_customer.get(cid, '')]
        first = name.split()[0]
        job_names[j['id']] = ([r for r in reals if r], name)
        sets = {
            'customer_name':  name,
            'customer_email': email if j['customer_email'] else j['customer_email'],
            'customer_phone': phone if j['customer_phone'] else j['customer_phone'],
            'address':        addr if j['address'] else j['address'],
            'description':    scrub(j['description'], reals, first, 'job', j['id']),
            'notes':          scrub(j['notes'], reals, first, 'job', j['id']),
            'bike_description': scrub(j['bike_description'], reals, first, 'job', j['id']),
            'portal_token':   (hashlib.sha256(f"demo|{j['id']}".encode()).hexdigest()[:32]
                               if j['portal_token'] else j['portal_token']),
        }
        sets = {k: v for k, v in sets.items() if k in jcols}
        upd('jobs', "UPDATE jobs SET " + ', '.join(f"{k}=?" for k in sets) + " WHERE id=?",
            (*sets.values(), j['id']))

    # ── email threads ─────────────────────────────────────────────────────
    if cols('email_imports'):
        for e in conn.execute("SELECT * FROM email_imports").fetchall():
            sender = job_customer_email.get(e['job_id']) or fake_email('sender', e['id'])
            reals, fname = job_names.get(e['job_id'], ([], 'Customer'))
            upd('email_imports',
                "UPDATE email_imports SET sender=?, subject=?, body=? WHERE id=?",
                (sender, scrub(e['subject'], reals, fname, 'mail', e['id']),
                 pick(INBOUND, 'in', e['id']), e['id']))
    if cols('email_replies'):
        for e in conn.execute("SELECT * FROM email_replies").fetchall():
            to = job_customer_email.get(e['job_id']) or fake_email('to', e['id'])
            reals, fname = job_names.get(e['job_id'], ([], 'Customer'))
            upd('email_replies',
                "UPDATE email_replies SET to_address=?, subject=?, body=? WHERE id=?",
                (to, scrub(e['subject'], reals, fname, 'reply', e['id']),
                 pick(OUTBOUND, 'out', e['id']), e['id']))
    if cols('email_import_attachments'):
        rows = conn.execute("SELECT id, filepath FROM email_import_attachments").fetchall()
        for a in rows:
            counts['attachment files'] = counts.get('attachment files', 0) + 1
            if args.apply and a['filepath']:
                p = a['filepath'] if os.path.isabs(a['filepath']) else os.path.join(data_dir, a['filepath'])
                try:
                    os.remove(p)
                except OSError:
                    pass
        if rows:
            counts['email_import_attachments'] = len(rows)
            if args.apply:
                conn.execute("DELETE FROM email_import_attachments")

    # ── SMS ───────────────────────────────────────────────────────────────
    if cols('sms_log'):
        for s in conn.execute("SELECT id FROM sms_log").fetchall():
            upd('sms_log', "UPDATE sms_log SET to_number=?, body=? WHERE id=?",
                (fake_phone('sms', s['id']), pick(SMS, 'sms', s['id']), s['id']))

    # ── calendar events (non-job entries) ─────────────────────────────────
    if cols('calendar_events'):
        for ev in conn.execute("SELECT * FROM calendar_events").fetchall():
            upd('calendar_events',
                "UPDATE calendar_events SET title=?, description=?, address=? WHERE id=?",
                (scrub(ev['title'], (), 'someone', 'event', ev['id']),
                 scrub(ev['description'], (), 'someone', 'event', ev['id']),
                 fake_address('event', ev['id']) if ev['address'] else ev['address'], ev['id']))

    # ── EFTPOS ────────────────────────────────────────────────────────────
    if 'card_number' in cols('eftpos_transactions'):
        for t in conn.execute("SELECT id FROM eftpos_transactions WHERE card_number IS NOT NULL").fetchall():
            upd('eftpos_transactions', "UPDATE eftpos_transactions SET card_number=? WHERE id=?",
                (f"XXXX-XXXX-XXXX-{h('card', t['id']) % 10000:04d}", t['id']))

    # ── staff logins ──────────────────────────────────────────────────────
    from werkzeug.security import generate_password_hash
    for u in conn.execute("SELECT * FROM users").fetchall():
        if (u['email'] or '').lower() in keep:
            continue
        name = fake_name('staff', u['id'])
        upd('users',
            "UPDATE users SET name=?, email=?, phone=?, password_hash=?, "
            "totp_secret=NULL, totp_enabled=0 WHERE id=?",
            (name, fake_email('staff', u['id'], name), fake_phone('staff', u['id']),
             generate_password_hash(secrets.token_urlsafe(24)) if args.apply else '', u['id']))

    # ── settings ──────────────────────────────────────────────────────────
    for key, val in [('business_bsb', '000-000'), ('business_account', '00000000')]:
        if conn.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone():
            upd('settings', "UPDATE settings SET value=? WHERE key=?", (val, key))
    if conn.execute("SELECT 1 FROM settings WHERE key='xero_refresh_token'").fetchone():
        upd('settings', "DELETE FROM settings WHERE key=? AND 1=?", ('xero_refresh_token', 1))

    # ── report ────────────────────────────────────────────────────────────
    print(("Applied" if args.apply else "Dry run — would change") + ":")
    for t, n in counts.items():
        print(f"  {t:28s} {n}")
    if args.apply:
        conn.commit()
        print("\nStill showing your business details (review in Settings → Business):")
        for r in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'business_%' "
                              "AND value != '' ORDER BY key"):
            print(f"  {r['key']:28s} {r['value']}")
    else:
        print("\nNothing changed. Re-run with --apply --keep-user <your login email>.")
    conn.close()


if __name__ == '__main__':
    main()
