#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh — update THIS server (staging or production) from GitHub.
#
#   1. refuse to run if tracked files have local edits (server must stay clean)
#   2. back up the live database (consistent online copy, kept in the volume)
#   3. git pull --ff-only
#   4. rebuild the image only if requirements.txt / Dockerfile changed
#   5. run migrate.py BEFORE restarting (new code may need new columns)
#   6. restart and wait for the health check
#   7. on failure, print the exact rollback commands
#
# Usage (on the server, from anywhere):
#   ~/service_manager/scripts/deploy.sh            # deploys origin/main
#   ~/service_manager/scripts/deploy.sh some-branch
#
# Optional overrides: PROJECT_DIR, KEEP_BACKUPS (default 30)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BRANCH="${1:-main}"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
KEEP_BACKUPS="${KEEP_BACKUPS:-30}"
cd "$PROJECT_DIR"

say() { echo -e "\n── $* ──"; }

# 1. Clean working tree? (untracked files such as .env, certs/, nginx/nginx.conf
#    once it is untracked, are fine — only edits to tracked files block a pull.)
say "Checking for local edits"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Tracked files have local changes on this server:"
  git status --short --untracked-files=no
  echo "Commit them in your local repo instead, or discard with: git checkout -- <file>"
  exit 1
fi
PREV=$(git rev-parse HEAD)
echo "Currently at $(git log -1 --format='%h %s')"

# 2. Database backup — sqlite backup API inside the running container gives a
#    consistent copy even while the app is serving requests.
say "Backing up database"
STAMP=$(date +%Y%m%d_%H%M%S)
docker compose exec -T flask python3 - "$STAMP" "$KEEP_BACKUPS" <<'PY'
import os, sqlite3, sys, glob
stamp, keep = sys.argv[1], int(sys.argv[2])
os.makedirs('/data/backups', exist_ok=True)
dest = f'/data/backups/field_service_{stamp}.db'
src = sqlite3.connect('/data/field_service.db')
dst = sqlite3.connect(dest)
src.backup(dst); dst.close(); src.close()
print('Backup:', dest, os.path.getsize(dest), 'bytes')
old = sorted(glob.glob('/data/backups/field_service_*.db'))[:-keep]
for f in old:
    os.remove(f)
PY

# 3. Pull
say "Pulling origin/$BRANCH"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
NEW=$(git rev-parse HEAD)
if [ "$PREV" = "$NEW" ]; then
  echo "Already up to date — restarting anyway."
else
  git log --oneline "$PREV..$NEW"
fi
find . -name "*.pyc" -delete 2>/dev/null || true

# 4. Rebuild only when the image itself changed
if git diff --name-only "$PREV" "$NEW" | grep -qE '^(requirements\.txt|Dockerfile)$'; then
  say "requirements.txt / Dockerfile changed — rebuilding image"
  docker compose build flask
fi

# 5. Migrate before restart (code is volume-mounted, so this runs the new migrate.py)
say "Running migrate.py"
docker compose run --rm -T flask python3 /app/migrate.py

# 6. Restart and wait for healthy
say "Restarting"
docker compose up -d
docker compose restart flask nginx

say "Waiting for health check"
for i in $(seq 1 30); do
  if docker compose exec -T flask curl -fs -o /dev/null http://localhost:5000/login; then
    VERSION=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' version.py || echo '?')
    echo "OK — running $(git log -1 --format='%h %s') (version $VERSION)"
    exit 0
  fi
  sleep 2
done

# 7. Failed
cat <<EOF

!! App did not become healthy within 60 s.
   Logs:      docker compose logs --tail=100 flask
   Roll back code:
              git checkout $PREV && docker compose restart flask nginx
   Roll back database (only if migrate.py changed data):
              docker compose stop flask
              docker compose run --rm -T flask cp /data/backups/field_service_$STAMP.db /data/field_service.db
              docker compose start flask
EOF
exit 1
