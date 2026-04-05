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

# Upgrade pip first, then install deps one at a time to avoid
# thread exhaustion from pip's parallel resolver
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
        --use-deprecated=legacy-resolver \
        -r requirements.txt

# Copy application code
COPY . .

# Persistent data directory
RUN mkdir -p /data

# Non-root user
RUN useradd -m -u 1000 appuser \
 && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 5000

CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "wsgi:app"]
