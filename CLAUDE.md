# CLAUDE.md

Guidance for Claude Code when working with this repository.

## Development Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY at minimum

# Run (debug mode — http://localhost:5000)
python app.py

# Default login: admin@flyingbike.com.au / changeme123

# Run DB migrations (after pulling schema changes)
python3 migrate.py
```

No test suite. No lint/format tooling.

## Architecture

Flask field-service management app for a mobile bicycle repair business (The Flying Bike). Entry point `app.py` (`create_app()`), WSGI entry `wsgi.py` (gunicorn in production). Deployed via Docker Compose + nginx on AWS EC2.

### Blueprints (`routes/`)

| Blueprint | Prefix | Purpose |
|---|---|---|
| `auth` | `/login`, `/logout` | Login, TOTP 2FA, user management |
| `jobs` | `/`, `/jobs/` | Job CRUD, status, parts, email imports |
| `customers` | `/customers/` | Customer list, edit, merge, import |
| `regions` | `/regions/` | Region/suburb management, region dates |
| `calendar` | `/calendar/` | FullCalendar view, event CRUD |
| `invoice` | `/jobs/<id>/invoice` | HTML + PDF invoice, shop ticket |
| `parts` | `/parts/` | Parts catalogue |
| `reports` | `/reports/` | Sales report |
| `email_replies` | `/jobs/<id>/email-*` | Compose/send email replies |
| `import_jobs` | `/admin/import-jobs` | CSV job import |
| `import_customers` | `/admin/import-customers` | CSV customer import |

### Database

SQLite via raw `sqlite3` (no ORM). Schema in `models.py:init_db()` — runs every startup with `CREATE TABLE IF NOT EXISTS`, safe to re-run. One-off changes go in `migrate.py`.

DB path: `field_service.db` locally, `/data/field_service.db` in Docker (`DATA_DIR` env var).

```python
from models import get_db
with get_db() as conn:
    rows = conn.execute("SELECT * FROM jobs WHERE status=?", ('pending',)).fetchall()
    conn.execute("UPDATE jobs SET status=? WHERE id=?", ('complete', job_id))
    conn.commit()
# Rows are sqlite3.Row — use row['column'] syntax
# PRAGMA foreign_keys = ON is set on every connection
```

### Key Tables

**`jobs`**: `id`, `reference`, `job_type`, `customer_id`, `customer_name`, `customer_email`, `customer_phone`, `suburb`, `address`, `description`, `bike_description`, `service_types`, `region_id`, `tax_inclusive`, `scheduled_date`, `scheduled_time`, `end_time`, `end_date`, `invoice_number`, `status`, `paid_date`, `amount_paid`, `payment_type`, `notes`

**`customers`**: `id`, `name`, `email` UNIQUE, `phone`, `suburb`, `address`

**`regions`** / **`suburbs`**: regions have a `visit_day`; suburbs have `region_id` FK

**`region_dates`**: `id`, `region_id`, `date`, `status` (open/pending/closed) — bookable dates per region. Auto-closed when calendar loads if date has passed.

**`job_parts`**: `job_id`, `part_id`, `description`, `part_number`, `quantity`, `unit_cost`

**`email_imports`** / **`email_replies`**: inbound/outbound email thread per job

**`settings`**: key/value store for per-user prefs (filters, report prefs, cal view), status colours, etc.

## Job Types

Three types, each with its own reference prefix:

| Type | Prefix | Scheduling | Visible Fields |
|---|---|---|---|
| `booking` | `FB-NNNN` | region + date select + start/end time | all fields |
| `workshop` | `PB-NNNN` | plain date input (default today) | no region/suburb/address/service_types; bike_description shown |
| `rental` | `RB-NNNN` | start date + end date | no region/suburb/time/bike_description/service_types |

`generate_reference(job_type, conn)` in `routes/jobs.py` creates the reference. Retried up to 5× on UNIQUE collision. `change_type` route re-numbers with new prefix.

## Tax Treatment

`jobs.tax_inclusive` is an integer with three values:

| Value | Label | Behaviour |
|---|---|---|
| `1` | Tax Inclusive | Prices include GST. Back-calc: `gst = total / 11` |
| `0` | Tax Exclusive | Prices ex-GST. `total = subtotal × 1.1` |
| `2` | GST Exempt | No GST. `gst = 0`, `total = raw sum`. Displays "nil" on invoice. |

Cash `payment_type` always zeros GST regardless of tax_inclusive. All three cases handled in `calc_totals()` in `routes/invoice.py`.

## Auth

Global `@before_request` in `app.py` redirects unauthenticated users to `/login`. Only `auth.login`, `auth.totp_verify`, and `static` are public. `g.user` attached on every request, available in templates. Optional TOTP 2FA per user (`totp.py`).

## Templates

All extend `templates/base.html`. Bootstrap 5.3 + Bootstrap Icons from CDN. Two themes (dark/light) stored per user in DB, cached in session. Every template receives `current_user`, `theme`, `status_colors`, `google_maps_api_key`, `JOB_TYPES`, `TIME_LABELS`, `TIME_SLOTS` via `@app.context_processor`.

**Custom Jinja filter `fmt_date`:**
```jinja
{{ value|fmt_date }}          {# "Tuesday 1 April 2025" #}
{{ value|fmt_date('short') }} {# "Tue 1 Apr 2025" #}
{{ value|fmt_date('dmy') }}   {# "01/04/2025" #}
```

**Fixed-position dropdowns**: Any `<select>` or custom dropdown inside a card with `overflow:hidden` must use `position:fixed` + `getBoundingClientRect()` to avoid rendering at offset 0,0. See customer search, part search, merge customer, and suburb pickers for the pattern.

## Job Detail Page (`/jobs/<id>`)

The detail page is merged with editing — no separate edit page. `GET /jobs/<id>` renders the full detail. `POST /jobs/<id>` saves the editable fields: `description`, `address`, `bike_description` (workshop only), scheduling fields, `tax_inclusive`, `notes`. The `update_status` form (`POST /jobs/<id>/status`) saves status, payment fields, and `invoice_number` separately.

`edit_job` route redirects to `job_detail` for backwards compatibility.

## Customer Upsert

`upsert_customer(conn, name, email, phone, suburb, address)` in `routes/jobs.py`:
1. Matches on `email` first (case-insensitive)
2. If no email: tries `LOWER(name)=LOWER(?)`, then `phone=?`
3. Falls back to synthetic email `unknown_<name>@unknown.local` if no match

Jobs store denormalised customer fields (`customer_name`, `customer_email`, etc.) in addition to `customer_id` FK. Keep both in sync when updating.

## Email System

**Poller** (`email_poller.py`): Background thread started at startup if `GMAIL_USER` + `GMAIL_REFRESH_TOKEN` are set. Polls Gmail via IMAP+OAuth2. New booking emails → jobs. Replies threaded via `In-Reply-To`/`References` headers, then subject match, then customer email match. UNSEEN fallback: `SINCE 2-days-ago` when Gmail marks messages read externally. Run `gmail_oauth_setup.py` once to get the refresh token.

**Replies** (`routes/email_replies.py`): Template-based with substitutions (`{{first_name}}`, `{{reference}}`, `{{invoice_pdf}}`, etc.). `{{invoice_pdf}}` attaches a generated PDF.

**Environment vars**: `GMAIL_USER`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_LABEL` (default: `Booking Email/Open Bookings`), `GMAIL_POLL_MINUTES` (default: 5).

## Invoice & PDF

`routes/invoice.py` serves HTML invoice (`/jobs/<id>/invoice`) and PDF (`/jobs/<id>/invoice/pdf/file`). PDF generated with ReportLab in `invoice_pdf.py` — not a template engine. Shop ticket at `/jobs/<id>/shop-ticket/file`.

GST Exempt jobs show "nil" in the HTML invoice totals section and omit the GST row in the PDF.

## Reports

Sales report (`/reports/sales`) supports filtering by date range, job type (booking/workshop/rental), and status. Selections persist per user in `settings`. Workshop jobs with NULL `scheduled_date` match on `paid_date` instead. Grand total row is a table row aligned to the same columns as the month rows.

## Calendar

FullCalendar 6 (`/calendar`). Three event types:
- **Job events**: coloured by status, link to job detail
- **Region date events** (canary yellow `#FFEF00`): all-day, one per open region date. Click → Delete Region Date dialog. Click empty all-day area → Add Region Date dialog.
- **Custom events**: created via the event modal (timed slot click)

Region dates auto-close (status → `closed`) when their date has passed, on each calendar load.

Routes: `POST /regions/add-date`, `POST /regions/delete-date/<id>`, `GET /regions/<id>/open-dates`.

`window.calendar` and `window.showToast` are exposed globally so dialog `onclick` handlers can call `calendar.refetchEvents()` after mutations.

## Seed Data

`seed.py` called on every startup:
- Parts always upserted from `parts.csv` (authoritative — DB edits overwritten on restart)
- Regions/suburbs loaded from `regions_suburbs.csv` only once (skipped if any regions exist)
- Default admin created only if `admin@flyingbike.com.au` doesn't exist

## Deployment

```bash
# EC2 — Docker Compose
docker compose up -d
docker compose exec -w /app flask python3 migrate.py   # run after schema changes

# DB backup
GET /admin/backup-db   # admin only, downloads live SQLite file

# Poll log
GET /admin/poll-log    # admin only, returns plain text email poll log
```

DB volume: `servicedesk_app_data` mounted at `/data/`.

## Non-Obvious Details

- `jobs.tax_inclusive` is `INTEGER` (0, 1, or 2) — never cast to `bool` in Python; pass the raw int to `calc_totals()`.
- `email_imports.body` differs by source: extracted `message` field for new bookings, full raw body for thread replies.
- Status colours stored in `settings` table as `status_color_<status>` with hardcoded fallbacks in `app.py`.
- `parts.csv` is authoritative — DB part edits are overwritten on next startup.
- `regions_suburbs.csv` loaded once only — changes require manual DB edits or full reset.
- `job_filter_{user_id}` and `customer_search_{user_id}` in `settings` persist list filters per user; cleared via dedicated POST routes.
- All three job types share the same `jobs` table — fields not applicable to a type are stored as NULL (e.g. `scheduled_time` for workshop/rental, `suburb` for rental).
- `invoice_number` is user-entered (e.g. `INV-0042`), not auto-generated.
