#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# refresh-staging.sh — run ON THE STAGING SERVER.
# Copies the production database + attachments onto staging, then makes the
# copy safe: staging must never email customers, send SMS, touch the real
# Google Calendar, or talk to Xero.
#
# Why Xero matters most: the Xero refresh token is stored in the DB
# (settings.xero_refresh_token) and ROTATES on every use. If staging used the
# copied token, production's token would be invalidated and Xero sync would
# break until re-authorised. So it is deleted here.
#
# Does NOT pull code — run scripts/deploy.sh afterwards (or before) for that.
#
# Set these for your servers (or export them before running):
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
PROD_HOST="${PROD_HOST:-ec2-user@app.theflyingbike.com.au}"      # production Lightsail
PROD_KEY="${PROD_KEY:-$PROJECT_DIR/certs/lightsail-prod.pem}"     # key that can ssh to prod
PROD_DATA_PATH="${PROD_DATA_PATH:-/var/lib/docker/volumes/service_manager_app_data/_data}"
STG_DATA_PATH="${STG_DATA_PATH:-/var/lib/docker/volumes/service_manager_app_data/_data}"

cd "$PROJECT_DIR"

# Safety: refuse to run on a server whose .env looks like production
if grep -qE '^(GMAIL_REFRESH_TOKEN|TWILIO_AUTH_TOKEN|XERO_CLIENT_SECRET)=.+' .env 2>/dev/null; then
  echo "This server's .env has live Gmail/Twilio/Xero credentials."
  echo "Staging .env must leave GMAIL_*, TWILIO_* and XERO_* blank. Aborting."
  exit 1
fi

echo "── Taking a consistent snapshot on production ──"
STAMP=$(date +%Y%m%d_%H%M%S)
ssh -i "$PROD_KEY" "$PROD_HOST" "cd ~/service_manager && docker compose exec -T flask python3 -c \"
import sqlite3; s=sqlite3.connect('/data/field_service.db'); d=sqlite3.connect('/data/staging_export.db'); s.backup(d); d.close()\""

docker compose down

echo "── Copying database ──"
rsync -avz --rsync-path="sudo rsync" -e "ssh -i $PROD_KEY" \
  "$PROD_HOST:$PROD_DATA_PATH/staging_export.db" /tmp/field_service.db
ssh -i "$PROD_KEY" "$PROD_HOST" "sudo rm -f $PROD_DATA_PATH/staging_export.db"
sudo cp "$STG_DATA_PATH/field_service.db" "$STG_DATA_PATH/field_service_before_refresh_$STAMP.db" 2>/dev/null || true
sudo cp /tmp/field_service.db "$STG_DATA_PATH/field_service.db"
sudo chown 1000:1000 "$STG_DATA_PATH/field_service.db"
rm -f /tmp/field_service.db

echo "── Copying attachments / bike images ──"
sudo mkdir -p "$STG_DATA_PATH/attachments"
rsync -avz --delete --rsync-path="sudo rsync" -e "ssh -i $PROD_KEY" \
  "$PROD_HOST:$PROD_DATA_PATH/attachments/" /tmp/attachments/
sudo rsync -a --delete /tmp/attachments/ "$STG_DATA_PATH/attachments/"
sudo chown -R 1000:1000 "$STG_DATA_PATH/attachments"
rm -rf /tmp/attachments

echo "── Neutralising integrations in the copied DB (before the app starts) ──"
docker compose run --rm -T flask python3 - <<'PY'
from models import get_db
with get_db() as c:
    c.execute("INSERT INTO settings (key,value) VALUES ('email_polling','off') "
              "ON CONFLICT(key) DO UPDATE SET value='off'")
    for k in ('gcal_enabled', 'sms_enabled'):
        c.execute("INSERT INTO settings (key,value) VALUES (?, '0') "
                  "ON CONFLICT(key) DO UPDATE SET value='0'", (k,))
    c.execute("DELETE FROM settings WHERE key='xero_refresh_token'")
    c.commit()
    print("email_polling=off, gcal_enabled=0, sms_enabled=0, xero token removed")
PY

docker compose run --rm -T flask python3 /app/migrate.py
docker compose up -d
echo "Staging refreshed from production ($STAMP)."
