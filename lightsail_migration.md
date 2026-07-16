# ServiceDesk — EC2 to Lightsail Migration Runbook

## Prerequisites
- AWS Console access
- SSH key for EC2 instance (`servicedesk-kp.pem`)
- GitHub repo access
- `.env` file contents (keep local copy safe)
- Current EC2 IP: `3.27.91.236`

---

## Phase 1 — Create Lightsail Instance

### 1.1 In AWS Console → Lightsail

1. Click **Create instance**
2. Region: **Asia Pacific (Sydney)** — same region as current EC2
3. Platform: **Linux/Unix**
4. Blueprint: **Amazon Linux 2023** (OS Only)
5. Instance plan: **$5/month** (1GB RAM, 1 vCPU, 40GB SSD)
6. Key pair: Create new or use existing — download the `.pem` file
7. Instance name: `servicedesk`
8. Click **Create instance**

### 1.2 Allocate a Static IP

1. Lightsail → **Networking** → **Create static IP**
2. Attach to `servicedesk` instance
3. Note the static IP address — this replaces `3.27.91.236`

### 1.3 Configure Firewall

In Lightsail → instance → **Networking** tab, ensure these inbound rules exist:
- SSH: TCP 22 (your IP or 0.0.0.0/0)
- HTTP: TCP 80
- HTTPS: TCP 443

---

## Phase 2 — Prepare EC2 (Source)

SSH into your existing EC2:
```bash
ssh -i ./certs/servicedesk-kp.pem ec2-user@3.27.91.236
```

### 2.1 Take a database backup via the app admin first

Then also export via Docker:
```bash
cd /home/ec2-user/servicedesk

# Export database from Docker volume to a tar file
docker run --rm \
  -v servicedesk_app_data:/data \
  -v /home/ec2-user:/backup \
  alpine tar czf /backup/servicedesk_db_$(date +%Y%m%d_%H%M%S).tar.gz /data

ls -lh /home/ec2-user/servicedesk_db_*.tar.gz
```

### 2.2 Copy files to local Mac

On your Mac (in a convenient directory):
```bash
# Copy database backup
scp -i ./certs/servicedesk-kp.pem \
  ec2-user@3.27.91.236:/home/ec2-user/servicedesk_db_*.tar.gz .

# Copy .env file (if you don't already have a local copy)
scp -i ./certs/servicedesk-kp.pem \
  ec2-user@3.27.91.236:/home/ec2-user/servicedesk/.env .

# Copy SSL certificates
scp -i ./certs/servicedesk-kp.pem -r \
  ec2-user@3.27.91.236:/home/ec2-user/servicedesk/certs ./servicedesk-certs-backup
```

---

## Phase 3 — Provision Lightsail Instance

SSH into the new Lightsail instance (use the key from Step 1.3):
```bash
ssh -i ./lightsail-key.pem ec2-user@<LIGHTSAIL_STATIC_IP>
```

### 3.1 Add Swap File (prevents RAM exhaustion during Docker builds)
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h  # confirm swap shows 2GB
```

### 3.2 Install Docker
```bash
sudo yum update -y
sudo yum install -y docker git
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user

# Install Docker Compose plugin
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Re-login so docker group takes effect
exit
```

SSH back in:
```bash
ssh -i ./lightsail-key.pem ec2-user@<LIGHTSAIL_STATIC_IP>
docker --version
docker compose version
```

### 3.3 Clone the Repository
```bash
cd /home/ec2-user
git clone https://github.com/terry-flander/service_manager.git servicedesk
cd servicedesk
git checkout main
```

### 3.4 Copy .env File

On your Mac:
```bash
scp -i ./lightsail-key.pem .env \
  ec2-user@<LIGHTSAIL_STATIC_IP>:/home/ec2-user/servicedesk/.env
```

Then on Lightsail, update `BASE_URL` in `.env` to the new static IP or domain:
```bash
nano /home/ec2-user/servicedesk/.env
# Update BASE_URL=https://<LIGHTSAIL_STATIC_IP>
```

### 3.5 Copy SSL Certificates

On your Mac:
```bash
scp -i ./lightsail-key.pem -r ./servicedesk-certs-backup/ \
  ec2-user@<LIGHTSAIL_STATIC_IP>:/home/ec2-user/servicedesk/certs
```

---

## Phase 4 — Restore Database

### 4.1 Copy database backup to Lightsail

On your Mac:
```bash
scp -i ./lightsail-key.pem servicedesk_db_*.tar.gz \
  ec2-user@<LIGHTSAIL_STATIC_IP>:/home/ec2-user/
```

### 4.2 Restore into Docker volume

On Lightsail:
```bash
cd /home/ec2-user/servicedesk

# Create the volume first by starting the stack briefly
docker compose up -d flask
sleep 5
docker compose down

# Restore the database backup into the volume
docker run --rm \
  -v servicedesk_app_data:/data \
  -v /home/ec2-user:/backup \
  alpine sh -c "cd / && tar xzf /backup/servicedesk_db_*.tar.gz"

# Verify the database is there
docker run --rm \
  -v servicedesk_app_data:/data \
  alpine ls -lh /data/
```

---

## Phase 5 — Build and Start

```bash
cd /home/ec2-user/servicedesk

# First build (installs Python dependencies into image)
# This is the last time --build is needed unless requirements.txt changes
docker compose up --build -d

# Watch the logs
docker compose logs -f flask
```

Wait for the healthcheck to pass (up to 30 seconds). You should see gunicorn startup lines.

### 5.1 Run Migrations
```bash
docker exec -it servicedesk-flask-1 python3 migrate.py
```

### 5.2 Smoke Test

Open a browser to `http://<LIGHTSAIL_STATIC_IP>` and confirm:
- Login works
- Jobs list loads
- A job detail opens
- Calendar loads
- Email imports load

---

## Phase 6 — Enable Automatic Snapshots

In AWS Console → Lightsail → instance → **Snapshots** tab:
1. Enable **Automatic snapshots**
2. Set preferred snapshot time (e.g. 3:00 AM — low traffic)
3. Retention: 7 days (free up to 1 snapshot/day)

This gives you a daily full-instance backup that survives instance deletion — the key protection you didn't have on EC2.

---

## Phase 7 — Set Up S3 Database Backup (Optional but Recommended)

This adds a second independent backup path — daily SQLite file to S3.

### 7.1 Create S3 Bucket

In AWS Console → S3:
1. Create bucket: `servicedesk-backups-tfb` (or similar)
2. Region: Sydney
3. Block all public access: ON
4. Versioning: optional

### 7.2 Create IAM User with S3-Only Access

In IAM → Users → Create user:
1. Name: `servicedesk-backup`
2. Attach policy: **AmazonS3FullAccess** (or create a custom policy limited to your bucket)
3. Create access key → note `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`

### 7.3 Install AWS CLI on Lightsail
```bash
sudo yum install -y awscli
aws configure
# Enter: Access Key ID, Secret Key, Region (ap-southeast-2), output (json)
```

### 7.4 Create Backup Script
```bash
cat > /home/ec2-user/backup_db.sh << 'EOF'
#!/bin/bash
# Daily SQLite backup to S3
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="/tmp/servicedesk_db_${TIMESTAMP}.tar.gz"
S3_BUCKET="s3://servicedesk-backups-tfb/daily/"

# Export from Docker volume
docker run --rm \
  -v servicedesk_app_data:/data \
  -v /tmp:/backup \
  alpine tar czf /backup/servicedesk_db_${TIMESTAMP}.tar.gz /data

# Upload to S3
aws s3 cp ${BACKUP_FILE} ${S3_BUCKET}

# Remove local temp file
rm -f ${BACKUP_FILE}

echo "Backup complete: ${TIMESTAMP}"
EOF

chmod +x /home/ec2-user/backup_db.sh
```

### 7.5 Schedule Daily Cron Job
```bash
crontab -e
# Add this line (runs at 2 AM daily):
0 2 * * * /home/ec2-user/backup_db.sh >> /home/ec2-user/backup.log 2>&1
```

Test it works:
```bash
/home/ec2-user/backup_db.sh
aws s3 ls s3://servicedesk-backups-tfb/daily/
```

---

## Phase 8 — Cutover

Once you've confirmed everything works on Lightsail:

### 8.1 Final Database Sync

Stop EC2 app briefly, take a fresh database export, and restore to Lightsail (repeat Phase 2.1 and Phase 4). This captures any jobs created since the initial migration.

### 8.2 Update .env BASE_URL

If you have a domain name pointing to EC2, update DNS to point to the Lightsail static IP. Otherwise update `BASE_URL` in `.env` on Lightsail to the new IP.

### 8.3 Update Google Calendar BASE_URL

If BASE_URL changed, the gcal event links will use the new URL automatically on next job save (it reads from `.env` at call time).

### 8.4 Decommission EC2

Once Lightsail has been running stably for a few days:
1. Stop the EC2 instance (don't terminate yet)
2. Run for a week on Lightsail only
3. If no issues, terminate the EC2 instance
4. Release the old Elastic IP if it's not attached (it costs money unattached)

---

## Promote Script Update

Update your local `promote.sh` to point to the Lightsail IP:

```bash
# Current (EC2)
HOST=ec2-user@3.27.91.236

# New (Lightsail) — update this line
HOST=ec2-user@<LIGHTSAIL_STATIC_IP>
```

---

## Troubleshooting

**Docker build fails (OOM):**
```bash
free -h  # check swap is active
sudo swapon /swapfile  # re-enable if missing
```

**App not responding:**
```bash
docker compose ps
docker compose logs flask --tail 50
docker compose logs nginx --tail 20
```

**Database missing after restore:**
```bash
docker run --rm -v servicedesk_app_data:/data alpine ls -la /data/
# If empty, re-run the restore step in Phase 4.2
```

**SSL certificate issues:**
If certs don't transfer cleanly, re-run your existing SSL setup — your domain/IP cert process is the same on Lightsail as EC2.

**Permission denied on Docker:**
```bash
# If docker commands fail without sudo
sudo usermod -aG docker ec2-user
newgrp docker
```
