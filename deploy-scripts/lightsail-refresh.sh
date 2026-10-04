#!/bin/bash
# Script to perform complete sync of EC2 -> Lightsail (DB, code, photos)
set -e

PROJECT_DIR=/home/ec2-user/service_manager
EC2_KEY=$PROJECT_DIR/certs/servicedesk-kp.pem
EC2_HOST=ec2-user@3.24.133.21
LS_DATA_PATH=/var/lib/docker/volumes/service_manager_app_data/_data
EC2_DATA_PATH=/var/lib/docker/volumes/servicedesk_app_data/_data

# Stop everything before copying DB and installing source changes
cd "$PROJECT_DIR"
docker compose down
# Pull bike images from EC2 into Lightsail
echo "Pulling bike images from EC2 to Lightsail ..."
rsync -avz --rsync-path="sudo rsync" \
  -e "ssh -i $EC2_KEY" \
  "$EC2_HOST:$EC2_DATA_PATH/attachments/" "/tmp/attachments/"

sudo mkdir -p "$LS_DATA_PATH/attachments"
sudo rsync -a /tmp/attachments/ "$LS_DATA_PATH/attachments/"
sudo chown -R 1000:1000 "$LS_DATA_PATH/attachments"
rm -rf /tmp/attachments

# Pull database from EC2 into Lightsail
echo "Pulling database from EC2 ..."
rsync -avz --rsync-path="sudo rsync" \
  -e "ssh -i $EC2_KEY" \
  "$EC2_HOST:$EC2_DATA_PATH/field_service.db" \
  "/tmp/field_service.db"

sudo cp /tmp/field_service.db "$LS_DATA_PATH/field_service.db"
sudo chown 1000:1000 "$LS_DATA_PATH/field_service.db"
rm /tmp/field_service.db

# Get latest changes from GitHub
git pull origin main
find . -name "*.pyc" -delete 2>/dev/null

# reset the local nginx.conf which is overwritten
sudo cp nginx/lightsail_nginx.conf nginx/nginx.conf

# Start with build in case source changes need to be included
docker compose up --build -d

# Disable email polling (EC2 is the live mailer, not Lightsail)
echo "Disabling email polling ..."
docker compose exec flask python3 -c "
from models import get_db
with get_db() as conn:
    conn.execute(\"UPDATE settings SET value='off' WHERE key='email_polling'\")
"

# Run migrations (DB copied from EC2 so usually a no-op)
docker compose exec flask python3 /app/migrate.py

echo "Done."