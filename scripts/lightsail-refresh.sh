#!/bin/bash
# Script to perform complete sync of EC2 -> Lightsail (DB, code, photos)
set -e

EC2_KEY=~/certs/servicedesk-kp.pem
EC2_HOST=ec2-user@3.24.133.21
DATA_PATH=/var/lib/docker/volumes/servicedesk_app_data/_data
PROJECT_DIR=~/servicedesk

# Stop everything before copying DB and installing source changes
cd "$PROJECT_DIR"
docker compose down

# Pull bike images from EC2 into Lightsail
echo "Pulling bike images from EC2 to Lightsail ..."
rsync -avz -e "ssh -i $EC2_KEY" \
  "$EC2_HOST:$DATA_PATH/attachments/" "$DATA_PATH/attachments/"

sudo chown -R 1000:1000 "$DATA_PATH/attachments"

# Pull database from EC2 into Lightsail
echo "Pulling database from EC2 ..."
scp -i "$EC2_KEY" \
  "$EC2_HOST:$DATA_PATH/field_service.db" \
  "$DATA_PATH/field_service.db"

sudo chown 1000:1000 "$DATA_PATH/field_service.db"

# Get latest changes from GitHub
git pull origin main
find . -name "*.pyc" -delete 2>/dev/null

# Start with build in case source changes need to be included
docker compose up --build -d

# Run migrations (DB copied from EC2 so usually a no-op)
docker compose exec flask python3 /app/migrate.py

echo "Done."