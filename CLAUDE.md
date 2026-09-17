# CLAUDE.md — AI Assistant Context for ServiceDesk

This file gives Claude (or any AI assistant) the context needed to work
effectively on this codebase without re-explaining conventions each session.

---

## Project

**Flying Bike ServiceDesk** — Flask/SQLite PWA for field service management.
Three job types across three brands; email threading; GCal/Xero integration.
Version: `1.5.0`  (see `version.py`)

---

## Critical Conventions

### SQLite rows — ALWAYS bracket access
```python
# CORRECT
row['column_name']
row['job_type'] or ''

# WRONG — will throw AttributeError at runtime
row.get('column_name')
row.get('job_type', '')
```
`sqlite3.Row` objects do NOT have `.get()`. This is a recurring bug source.

### Jinja2 — tojson in onclick attributes
```html
<!-- WRONG — double quotes break the HTML attribute -->
<button onclick="fn({{ value|tojson }})">

<!-- CORRECT — assign to a JS variable in a script block -->
<script>var VALUE = {{ value|tojson }};</script>
<button onclick="fn(VALUE)">
```

### Email addresses
- System customers use `@flyingbike.internal` suffix (Counter Sales, Bikes for Sale).
- `email_sender.is_sendable_email(addr)` guards all outbound sends — returns `False`
  for empty, `@unknown.local`, or missing `@`. Call this before any `send_reply()`.

### Coordinates / URLs — ASCII hyphens only
When writing latitude/longitude in URLs, query strings, or any text that will
be parsed as code or a URL, always use ASCII hyphen `-` (U+002D) for negative
values. Never use em dash `—`, en dash `–`, or Unicode minus `−` — they look
identical in some editors but break URL parsers.

### pyc / bytecode cache
Stale `.pyc` files are a recurring source of "code change not taking effect"
bugs. After any significant file promotion, clear them:
```bash
find ~/servicedesk -name "*.pyc" -delete 2>/dev/null
```

### version.py
Never edit with a Python one-liner `open(...,'w')` — if interrupted it leaves
an empty file that crashes startup. Use `echo` or `promote.sh`:
```bash
echo 'VERSION = "1.5.0"' > version.py
```

---

## DB Patterns

```python
# Always use context manager
with get_db() as conn:
    rows = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchall()

# Bracket access on results
job['customer_name']   # correct
job.get('customer_name')  # WRONG
```

### Migrations
- `models.py` — `CREATE TABLE IF NOT EXISTS` (startup, idempotent)
- `migrate.py` — `ALTER TABLE ... ADD COLUMN` (run manually after deploy)
- Always run migrate **inside the container**:
  ```bash
  docker compose run --rm flask python3 /app/migrate.py
  ```

---

## Promote Workflow

```bash
# From local repo root
../promote.sh routes/jobs.py
../promote.sh templates/jobs/detail.html
```

```bash
# Separately on the server
docker compose restart flask
```

**Never mix promote.sh and docker commands in the same shell block.**
They run in different places — promote.sh is local, docker commands are on server.

---

## Key Files

| File | Purpose |
|---|---|
| `models.py` | Full DB schema |
| `migrate.py` | Schema migrations (append-only, safe to re-run) |
| `seed.py` | Reference data — system customers, spec templates, parts |
| `routes/jobs.py` | Core job CRUD, JOB_TYPES dict, add_part |
| `routes/bikes.py` | Bikes for Sale CRUD + public JSON endpoint |
| `routes/email_replies.py` | Email compose, templates, thread view |
| `routes/calendar.py` | FullCalendar feed, GCal drag/drop sync |
| `routes/invoice.py` | Invoice PDF generation route |
| `invoice_pdf.py` | PDF layout |
| `xero_sync.py` | Xero invoice push |
| `gcal_sync.py` | Google Calendar event create/update/delete |
| `email_poller.py` | Background IMAP poller |
| `email_sender.py` | Gmail OAuth2 SMTP + `is_sendable_email()` |
| `templates/base.html` | Shared layout, sidebar, reply modal, `initRichTextEditor()` |
| `templates/jobs/detail.html` | Job edit form (the main working screen) |
| `static/pistabikes.html` | Pista Bikes public website |
| `static/pistabikes-bikes.html` | Second-hand bikes gallery (calls `/bikes-for-sale` API) |

---

## Job Type Routing Logic

`sale_bike` jobs behave like `workshop` in most places:
- No suburb, address, region, service types, phone, portal link
- Not on calendar or GCal
- Always tax inclusive
- Auto-assigned to "Bikes for Sale" internal customer
- Invoice: single line from `bikes_for_sale.short_desc`, no parts lines
- Xero: single line, `OUTPUT/Inclusive` tax type

---

## Public Endpoints (no auth required)
```
GET  /bikes-for-sale          JSON feed for pistabikes-bikes.html
GET  /bikes/images/<id>       Bike photo serving
POST /booking/submit          Booking form submission
```

---

## Environment Variables (.env)
```
SECRET_KEY
GMAIL_USER
GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN
GCAL_CLIENT_ID / GCAL_CLIENT_SECRET / GCAL_REFRESH_TOKEN / GCAL_CALENDAR_ID
XERO_CLIENT_ID / XERO_CLIENT_SECRET / XERO_REFRESH_TOKEN / XERO_TENANT_ID
BIKES_FOR_SALE_URL   (default: https://app.theflyingbike.com.au)
```
