# Flying Bike ServiceDesk — Deployment Runbook
# Version 1.5.0
# ==============================================
# Architecture: Docker Compose (nginx + Flask/gunicorn)
# DNS/SSL:      Cloudflare (free SSL, proxy)
# Servers:
#   Production:  AWS Lightsail (Amazon Linux 2023) — company AWS account
#   Legacy:      AWS EC2 t3.small (personal account) — being decommissioned

# ─────────────────────────────────────────────────────────────────────────────
# PART 1 — LIGHTSAIL SERVER SETUP (current production target)
# ─────────────────────────────────────────────────────────────────────────────

# Instance spec:
#   Platform:  Amazon Linux 2023
#   Plan:      512 MB RAM, 2 vCPU, 20 GB SSD  (upgrade to 1 GB if builds fail)
#   IP:        13.210.197.67 (static Lightsail IP)
#   SSH key:   your Lightsail .pem

ssh -i your-lightsail-key.pem ec2-user@13.210.197.67

# 1a. System update + swap (512 MB RAM needs swap for Docker builds)
sudo dnf update -y
sudo dd if=/dev/zero of=/swapfile bs=128M count=16
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab

# 1b. Docker
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
# Log out and back in for group to take effect

# 1c. Docker Compose (manual install — dnf package not available on AL2023)
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
     -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version   # verify

# 1d. Docker Buildx (required by newer docker compose build)
BUILDX_VERSION=$(curl -s https://api.github.com/repos/docker/buildx/releases/latest \
  | grep '"tag_name"' | cut -d'"' -f4)
sudo curl -SL "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-amd64" \
  -o /usr/local/lib/docker/cli-plugins/docker-buildx
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx
docker buildx version

# 1e. Clone the repo
git clone https://github.com/terry-flander/service_manager.git ~/servicedesk
cd ~/servicedesk

# 1f. Environment
cp .env.example .env
nano .env
# Set: SECRET_KEY, GMAIL_USER, Gmail/GCal/Xero OAuth credentials, BIKES_FOR_SALE_URL

# 1g. Lightsail firewall — open port 80 in the Lightsail console:
#   Instance → Networking → Add rule → HTTP (port 80)

# 1h. Build and start
docker compose up -d --build
docker compose ps    # both flask and nginx should be Up

# ─────────────────────────────────────────────────────────────────────────────
# PART 2 — NGINX CONFIG (HTTP only — Cloudflare handles SSL)
# ─────────────────────────────────────────────────────────────────────────────

# nginx/nginx.conf uses a single HTTP server block.
# Cloudflare sits in front and terminates HTTPS — nginx never sees TLS.
# server_name includes the Lightsail IP for direct testing + the domain once
# Cloudflare A record is updated.
#
# No SSL certificates needed on the server.
# Set Cloudflare SSL mode to "Flexible" (Cloudflare ↔ server is plain HTTP).

# ─────────────────────────────────────────────────────────────────────────────
# PART 3 — DATABASE COPY FROM EC2
# ─────────────────────────────────────────────────────────────────────────────

# Find the volume mount point:
docker volume inspect $(docker volume ls -q | grep data) | grep Mountpoint

# Copy DB from EC2 to local:
scp -i ec2-key.pem \
  ec2-user@<EC2-IP>:/var/lib/docker/volumes/servicedesk_app_data/_data/field_service.db \
  /tmp/field_service.db

# Copy DB from local to Lightsail:
scp -i lightsail-key.pem /tmp/field_service.db ec2-user@13.210.197.67:/tmp/

# On Lightsail — copy into volume and fix permissions:
MOUNTPOINT=$(docker volume inspect $(docker volume ls -q | grep data) \
  | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['Mountpoint'])")
sudo cp /tmp/field_service.db $MOUNTPOINT/field_service.db
sudo chown 1000:1000 $MOUNTPOINT/field_service.db

# Run migrations (always inside the container, after DB is in place):
docker compose run --rm flask python3 /app/migrate.py

docker compose restart flask

# ─────────────────────────────────────────────────────────────────────────────
# PART 4 — CODE UPDATES (ongoing)
# ─────────────────────────────────────────────────────────────────────────────

cd ~/servicedesk
git pull origin main
find . -name "*.pyc" -delete 2>/dev/null

# If schema changes (new columns / tables):
docker compose run --rm flask python3 /app/migrate.py

docker compose restart flask

# ─────────────────────────────────────────────────────────────────────────────
# PART 5 — BIKE PHOTO SYNC (EC2 → Lightsail)
# ─────────────────────────────────────────────────────────────────────────────

# Run from local machine:
EC2_KEY=~/.ssh/ec2-key.pem
LS_KEY=~/.ssh/lightsail-key.pem
EC2_HOST=ec2-user@<EC2-IP>
LS_HOST=ec2-user@13.210.197.67
DATA_PATH=/var/lib/docker/volumes/servicedesk_app_data/_data/attachments

rsync -avz -e "ssh -i $EC2_KEY" "$EC2_HOST:$DATA_PATH/" /tmp/bike-images/
rsync -avz -e "ssh -i $LS_KEY"  /tmp/bike-images/ "$LS_HOST:$DATA_PATH/"
ssh -i $LS_KEY $LS_HOST "sudo chown -R 1000:1000 $DATA_PATH"

# ─────────────────────────────────────────────────────────────────────────────
# PART 6 — CLOUDFLARE CUTOVER (once Lightsail validated)
# ─────────────────────────────────────────────────────────────────────────────

# 1. Validate app works at http://13.210.197.67
# 2. In Cloudflare DNS for each domain:
#      A record → update IP from EC2 IP to 13.210.197.67
#      Proxy: ON (orange cloud) — Cloudflare handles SSL
# 3. Domains to update:
#      app.theflyingbike.com.au         (after domain recovered from hostile party)
#      melbournebikeeducationandhire.com.au  (already on Cloudflare NS — just update A)
#      pistabikes.com.au                (after domain transfer from Melbourne IT)
# 4. Decommission EC2 instance once traffic confirmed on Lightsail

# ─────────────────────────────────────────────────────────────────────────────
# PART 7 — AUTO-START ON REBOOT
# ─────────────────────────────────────────────────────────────────────────────

sudo tee /etc/systemd/system/servicedesk.service << 'SVCEOF'
[Unit]
Description=ServiceDesk Docker Compose
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/ec2-user/servicedesk
ExecStart=/usr/local/lib/docker/cli-plugins/docker-compose up -d
ExecStop=/usr/local/lib/docker/cli-plugins/docker-compose down
TimeoutStartSec=0
User=ec2-user

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl enable servicedesk
sudo systemctl start servicedesk

# ─────────────────────────────────────────────────────────────────────────────
# PART 8 — DIAGNOSTICS
# ─────────────────────────────────────────────────────────────────────────────

# Container status:
docker compose ps

# Logs:
docker compose logs flask --tail 50
docker compose logs nginx --tail 20
docker compose logs flask 2>&1 | grep -A 10 "Traceback\|ERROR"

# App won't start — test imports directly:
docker compose run --rm flask python3 -c "import app; print('OK')"

# DB access:
docker compose exec flask python3 -c "
from models import get_db
with get_db() as c:
    print(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 'jobs')
"

# ─────────────────────────────────────────────────────────────────────────────
# PART 9 — COSTS (ap-southeast-2 Sydney, 2026)
# ─────────────────────────────────────────────────────────────────────────────
#
#   Lightsail 512MB plan:    ~$5/month   (company AWS account)
#   Lightsail static IP:      free while attached
#   Cloudflare Free plan:     $0          (DNS, SSL, CDN, DMARC reports)
#   Domain (if .com.au):     ~$20/year
#                             ─────────
#   Total:                   ~$5/month
#
#   (EC2 + ALB was ~$40/month on personal account — now decommissioned)

# ─────────────────────────────────────────────────────────────────────────────
# PART 10 — EC2 LEGACY (personal account — being decommissioned)
# ─────────────────────────────────────────────────────────────────────────────

# EC2 instance: t3.small, Amazon Linux 2023
# SSH: ssh -i ec2-key.pem ec2-user@<EC2-ELASTIC-IP>
# App dir: ~/servicedesk
# Promote script: ~/promote.sh <relative-path>
#
# The promote.sh script copies files from local to ~/servicedesk on EC2.
# After promoting files:
#   docker compose restart flask
#
# After schema changes:
#   docker compose run --rm flask python3 /app/migrate.py
#   docker compose restart flask
#
# Decommission checklist:
#   ✓ DB copied to Lightsail
#   ✓ Cloudflare A records updated to Lightsail IP
#   ✓ Traffic confirmed on Lightsail
#   □ Stop EC2 instance
#   □ Release Elastic IP
#   □ Terminate instance + delete EBS volume
