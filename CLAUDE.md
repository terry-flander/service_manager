# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in SECRET_KEY at minimum

# Run (debug mode, http://localhost:5000)
python app.py

# Default login: admin@flyingbike.com.au / changeme123
```

No test suite exists. No lint/format tooling is configured.

## Architecture

Flask app for field service management (job scheduling, invoicing, customer management) for a bike repair business. Entry point is `app.py` (`create_app()`), WSGI entry is `wsgi.py` (gunicorn in production).

**Blueprints** in `routes/`: `auth`, `jobs`, `customers`, `regions`, `calendar`, `invoice`, `parts`, `reports`, `email_replies`, `import_jobs`. All registered in `create_app()`.

**Database**: SQLite via raw `sqlite3` (no ORM). Schema lives entirely in `models.py:init_db()`, which runs on every startup using `CREATE TABLE IF NOT EXISTS` — safe to re-run. One-off schema changes go in `migrate.py` or `update_db.py`. DB path: `field_service.db` locally, `/data/field_service.db` in Docker (controlled by `DATA_DIR` env var).

**Standard DB pattern** used everywhere:
```python
from models import get_db
with get_db() as conn:
    rows = conn.execute("SELECT * FROM jobs WHERE status=?", ('pending',)).fetchall()
    conn.execute("UPDATE ...", (...,))
    conn.commit()
```
Rows are `sqlite3.Row` — use `row['column']` syntax. Foreign keys are enforced (`PRAGMA foreign_keys = ON`).

**Auth**: Global `@before_request` gate in `app.py` redirects unauthenticated users to `/login`. Only `auth.login`, `auth.totp_verify`, and `static` are public. `g.user` is attached on every request and available in all templates. Optional TOTP 2FA per user (via `totp.py`).

**Templates**: Jinja2, all extend `templates/base.html`. Bootstrap 5.3 + Bootstrap Icons loaded from CDN. Two themes (dark/light) stored per user in DB and cached in session; `data-theme` attribute on `<html>` controls CSS variables. Every template gets `current_user`, `theme`, `status_colors`, and `google_maps_api_key` via `@app.context_processor`. Custom filter: `{{ value|fmt_date }}` (full: "Tuesday 1 April 2025", short: "Tue 1 Apr 2025").

**Email polling** (`email_poller.py`): Background thread started at app startup if `GMAIL_USER` + `GMAIL_REFRESH_TOKEN` env vars are set. Polls Gmail via IMAP+OAuth2. New booking emails become jobs; replies are threaded against existing jobs via `In-Reply-To`/`References` headers or subject matching. Run `gmail_oauth_setup.py` once to generate the refresh token.

**Invoice PDF** (`invoice_pdf.py`): Generated with reportlab (not a template engine). Supports tax-inclusive (GST back-calc) and tax-exclusive modes per job.

**Seed data** (`seed.py`): Called on every startup. Parts are always upserted from `parts.csv`. Regions/suburbs are loaded from `regions_suburbs.csv` only once (skipped if any regions exist). Default admin created only if `admin@flyingbike.com.au` doesn't exist.

## Key Non-Obvious Details

- `email_imports.body` stores `parsed['message']` (extracted field) for new bookings, but the full raw body for thread replies — these differ.
- Status colors are stored in the `settings` table (key: `status_color_<status>`) with hardcoded fallback defaults in `app.py`.
- `parts.csv` is authoritative for the parts list — edits to parts in the DB will be overwritten on next startup unless the CSV is also updated.
- `regions_suburbs.csv` is only read once — subsequent changes to the file require manual DB edits or a data reset.
- Jobs store denormalised customer fields (`customer_name`, `customer_email`, etc.) in addition to the FK `customer_id`. Both must be kept in sync when updating customers.
- The `job_type` field distinguishes `booking` (from website form) from `workshop` (walk-in). Workshop jobs have no address or scheduled time.
- `generate_reference()` in `routes/jobs.py` creates human-readable job references (e.g. `BK-2025-001`). Retried up to 5× on collision.
