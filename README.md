# Flying Bike ServiceDesk

Field service management PWA for **The Flying Bike Group** — a Melbourne-based bicycle service and hire business operating across three brands:

| Brand | Domain | Type |
|---|---|---|
| The Flying Bike | theflyingbike.com.au | Mobile on-site repair |
| Pista Bikes | pistabikes.com.au | Workshop at 255 Hawthorn Rd, Caulfield |
| Melbourne Bike Education & Hire | melbournebikeeducationandhire.com.au | School / group fleet hire |

Live at **https://app.theflyingbike.com.au** · GitHub: `terry-flander/service_manager`

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Framework | Flask 3 + Gunicorn |
| Database | SQLite 3 (single file, Docker volume) |
| Frontend | Vanilla JS, Bootstrap Icons, FullCalendar |
| Container | Docker Compose — nginx + Flask |
| DNS / SSL | Cloudflare (proxy + free SSL) |
| Email | Gmail SMTP via OAuth2 |
| Calendar | Google Calendar API (OAuth2) |
| Accounting | Xero API (OAuth2) |

---

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in secrets
python3 migrate.py     # create / update DB schema
python3 seed.py        # seed reference data
flask run
```

---

## Key Directories

```
routes/          Flask blueprints (one per feature domain)
templates/       Jinja2 HTML templates
static/          Public static files (public-facing websites)
models.py        DB schema — CREATE TABLE IF NOT EXISTS
migrate.py       ALTER TABLE migrations — safe to re-run
seed.py          Reference data (parts CSV, regions, system customers)
gcal_sync.py     Google Calendar push/pull
xero_sync.py     Xero invoice push
email_poller.py  Gmail IMAP poller (background thread)
email_sender.py  Outbound SMTP via Gmail OAuth2
invoice_pdf.py   PDF invoice generation
```

---

## Job Types

| Type | Prefix | Description |
|---|---|---|
| `booking` | FB- | On-site customer visit (The Flying Bike) |
| `workshop` | PB- | Workshop repair (Pista Bikes) |
| `rental` | RB- | Bike / trailer hire (MBEH) |
| `sale` | CS- | Counter sale |
| `sale_bike` | BK- | Second-hand bike for sale |

---

## Promote Workflow (production)

```bash
../promote.sh <file>   # copies file to ~/servicedesk/ on EC2
docker compose restart flask
```

After schema changes:
```bash
docker compose run --rm flask python3 /app/migrate.py
```

See **DEPLOY.md** for full server setup.
