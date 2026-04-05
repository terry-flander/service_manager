# Flying Bike ServiceDesk — AWS Deployment Runbook
# ===================================================
# Architecture: EC2 (t3.small) + Docker Compose (nginx + Flask/gunicorn)
#               behind an AWS ALB that terminates HTTPS via ACM certificate.

# ─────────────────────────────────────────────────────────────────────────────
# PART 1 — AWS INFRASTRUCTURE
# ─────────────────────────────────────────────────────────────────────────────

# 1a. Create a Security Group (call it "servicedesk-sg"):
#   Inbound:
#     HTTP  (80)   from 0.0.0.0/0          ← ALB health checks + redirect
#     HTTPS (443)  from 0.0.0.0/0          ← user traffic (ALB only)
#     SSH   (22)   from YOUR_IP/32          ← your office IP only
#   Outbound:
#     All traffic  to 0.0.0.0/0            ← for apt/pip/docker pulls

# 1b. Launch EC2 instance:
#   AMI:           Amazon Linux 2023 (x86_64)
#   Instance type: t3.small  (2 vCPU, 2 GB RAM)
#   Storage:       20 GB gp3
#   Security Group: servicedesk-sg (from 1a)
#   Key pair:      create or use existing .pem

# 1c. Allocate and attach an Elastic IP to the instance
#     (so the IP doesn't change on restart)

# 1d. Request an ACM certificate:
#   Go to: AWS Console → Certificate Manager → Request certificate
#   Domain: yourdomain.com (and *.yourdomain.com if you want subdomains)
#   Validation: DNS validation (add the CNAME record ACM provides to your DNS)
#   Wait ~5 minutes for validation

# 1e. Create an Application Load Balancer:
#   Scheme:         Internet-facing
#   Listeners:
#     HTTP  (80)  → Redirect to HTTPS (301)
#     HTTPS (443) → Forward to Target Group
#   Target Group:
#     Type:     Instance
#     Protocol: HTTP, Port: 80
#     Health check path: /health
#     Register your EC2 instance
#   SSL Certificate: select the ACM cert from 1d

# 1f. Point your domain to the ALB:
#   In Route 53 (or your DNS provider):
#   Create an A record (Alias) → ALB DNS name
#   e.g.  servicedesk.flyingbike.com.au → ALB-xyz.ap-southeast-2.elb.amazonaws.com

# ─────────────────────────────────────────────────────────────────────────────
# PART 2 — EC2 SERVER SETUP (run once, SSH in as ec2-user)
# ─────────────────────────────────────────────────────────────────────────────

# SSH in:
ssh -i your-key.pem ec2-user@<ELASTIC_IP>

# Install Docker and Docker Compose
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
newgrp docker  # apply group without re-login

# Install Docker Compose plugin
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
     -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version   # verify

# ─────────────────────────────────────────────────────────────────────────────
# PART 3 — DEPLOY THE APP
# ─────────────────────────────────────────────────────────────────────────────

# Copy code to server (from your local machine):
scp -i your-key.pem -r ./field_service ec2-user@<ELASTIC_IP>:~/servicedesk

# OR clone from your git repository:
git clone https://github.com/YOUR_ORG/servicedesk.git ~/servicedesk

# Set up environment:
cd ~/servicedesk
cp .env.example .env
nano .env
#   Set SECRET_KEY to a long random string:
#   python3 -c "import secrets; print(secrets.token_hex(32))"
#   Set GOOGLE_MAPS_API_KEY if used

# Build and start:
docker compose up -d --build

# Check it's running:
docker compose ps
docker compose logs -f

# The app is now accessible at http://<ELASTIC_IP>
# And at https://yourdomain.com once DNS propagates

# ─────────────────────────────────────────────────────────────────────────────
# PART 4 — ONGOING OPERATIONS
# ─────────────────────────────────────────────────────────────────────────────

# Deploy an update:
cd ~/servicedesk
git pull                          # or re-upload changed files
docker compose up -d --build      # rebuilds only changed layers, zero downtime
                                  # (nginx keeps serving while flask restarts)

# View logs:
docker compose logs flask         # app logs
docker compose logs nginx         # access/error logs
docker compose logs -f --tail=50  # live tail

# Backup the database:
docker compose exec flask \
  cp /data/field_service.db /data/backup_$(date +%Y%m%d).db
# Copy backup to your machine:
scp -i your-key.pem ec2-user@<IP>:/var/lib/docker/volumes/servicedesk_app_data/_data/field_service.db ./

# Restore from backup:
docker compose down
sudo cp field_service.db /var/lib/docker/volumes/servicedesk_app_data/_data/
docker compose up -d

# Run DB migrations (ALTER TABLE commands):
docker compose exec flask python3 -c "
from models import get_db
with get_db() as conn:
    conn.execute('ALTER TABLE ...')
    conn.commit()
"

# Restart the app only (no rebuild):
docker compose restart flask

# Stop everything:
docker compose down

# ─────────────────────────────────────────────────────────────────────────────
# PART 5 — AUTO-START ON REBOOT
# ─────────────────────────────────────────────────────────────────────────────

# Create a systemd service so Docker Compose starts on boot:
sudo tee /etc/systemd/system/servicedesk.service << 'EOF'
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
EOF

sudo systemctl enable servicedesk
sudo systemctl start servicedesk

# ─────────────────────────────────────────────────────────────────────────────
# PART 6 — ESTIMATED COSTS (ap-southeast-2 Sydney, March 2026)
# ─────────────────────────────────────────────────────────────────────────────
#
#   EC2 t3.small (on-demand):    ~$18/month
#   EBS gp3 20GB:                 ~$2/month
#   Elastic IP (attached):         free
#   ALB:                          ~$18/month  (LCU-based pricing)
#   ACM certificate:                free
#   Route 53 hosted zone:          ~$0.50/month
#   Data transfer (low volume):    ~$1-2/month
#                                  ──────────
#   Total:                        ~$40/month
#
#   To reduce costs:
#   - Use a t4g.small (ARM) instead of t3.small: ~$13/month
#   - Skip the ALB and use nginx to terminate SSL directly: saves ~$18/month
#     (requires copying the ACM cert or using Let's Encrypt/Certbot)
#
# ─────────────────────────────────────────────────────────────────────────────
# PART 7 — SECURITY CHECKLIST
# ─────────────────────────────────────────────────────────────────────────────
#
#   ✓ HTTPS enforced (ALB redirects HTTP → HTTPS)
#   ✓ Flask SECRET_KEY is a long random value in .env (not in git)
#   ✓ SQLite DB in a Docker volume (not in the container layer)
#   ✓ App runs as non-root user (uid 1000) inside container
#   ✓ Security headers set by nginx (X-Frame-Options, CSP, etc.)
#   ✓ SSH access locked to your IP only
#   ✓ Flask debug mode off (gunicorn in production)
#   ✓ Session auth + 2FA for all users
#
#   Recommended additions:
#   - Enable AWS CloudWatch for logs and alerts
#   - Enable EC2 automatic security patches (AWS Systems Manager Patch Manager)
#   - Schedule daily DB backups to S3:
#       aws s3 cp /var/lib/docker/volumes/servicedesk_app_data/_data/field_service.db \
#                 s3://your-backup-bucket/servicedesk/$(date +%Y/%m/%d)/field_service.db
#   - Add to crontab: 0 2 * * * /home/ec2-user/backup.sh
