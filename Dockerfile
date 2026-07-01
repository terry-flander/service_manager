FROM python:3.12-slim

# System deps
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libpng-dev \
        libjpeg-dev \
        poppler-utils \
        curl \
 && apt-get clean \
 && find /var/cache/apt /var/lib/apt/lists -type f -delete

WORKDIR /app

# Install dependencies — this layer is cached and only rebuilds when
# requirements.txt changes. App code is mounted as a volume at runtime
# so no rebuild is ever needed for code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
        --use-deprecated=legacy-resolver \
        -r requirements.txt

# App code is NOT copied here — it is mounted from the host via the
# volume mount in docker-compose.yml (.:/app), so changes are
# immediately visible after a simple: docker compose restart flask

# Persistent data directory (database lives here)
RUN mkdir -p /data

# Non-root user — only /data needs chown since /app is a volume mount
RUN useradd -m -u 1000 appuser \
 && chown -R appuser:appuser /data
USER appuser

EXPOSE 5000

CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "wsgi:app"]
