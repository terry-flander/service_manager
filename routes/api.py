"""
routes/api.py — Public booking API for The Flying Bike website.

POST /api/v1/booking
  Creates a job and an email_imports record (status='ok', read=1 unread)
  exactly as the email poller does for an incoming booking email.
  Returns JSON with job reference and portal URL.

No authentication required for the booking endpoint — it is rate-limited
by nginx and the shared secret matches what home.html sends.

The email poller continues to operate unchanged. Email is still the primary
intake channel. This API is an additional intake path only.
"""
import os
import secrets
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify
from models import get_db

api_bp  = Blueprint('api', __name__)
log     = logging.getLogger('app')

# Shared secret — must match TFB_API_SECRET in home.html
# Set in .env as TFB_API_SECRET=<value>
_SECRET = os.environ.get('TFB_API_SECRET', '')


def _check_secret(data):
    """Return True if the request carries the correct shared secret."""
    if not _SECRET:
        # If no secret configured, allow all (dev mode)
        return True
    return data.get('_secret', '') == _SECRET


@api_bp.route('/api/v1/booking', methods=['POST'])
def create_booking():
    """
    Accept a booking form submission from the website and create a job.

    Creates:
      - customer record (upsert)
      - jobs record (status=pending, job_type=booking)
      - email_imports record (status='ok', read=1 unread) — same as email poller
      - job_parts for each selected service type (if found in parts table)
      - portal_token for the status link

    Returns JSON:
      { ok: true, reference: "FB-2026-001", portal_url: "https://..." }
    """
    data = request.get_json(silent=True) or {}

    if not _check_secret(data):
        log.warning("API: rejected booking — bad secret")
        return jsonify({'ok': False, 'error': 'Unauthorised'}), 401

    # Required fields
    name    = (data.get('name')    or '').strip()
    email   = (data.get('email')   or '').strip().lower()
    phone   = (data.get('phone')   or '').strip()
    suburb  = (data.get('suburb')  or '').strip()
    message = (data.get('message') or '').strip()
    services = (data.get('services') or '').strip()  # comma-separated string

    if not name or not email:
        return jsonify({'ok': False, 'error': 'Name and email are required'}), 400

    # Build a synthetic subject and body matching what the email poller produces
    subject = f"Booking Request — {name}"
    body = (
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Phone: {phone}\n"
        f"Suburb: {suburb}\n"
        f"Services: {services}\n\n"
        f"Message:\n{message}"
    )
    # Synthetic message ID — unique, won't collide with real email message IDs
    message_id = f"<api-{secrets.token_hex(16)}@theflyingbike.com.au>"
    received_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    try:
        with get_db() as conn:
            from routes.jobs import upsert_customer, generate_reference, recalc_job_totals

            # Upsert customer
            customer_id, stored_address = upsert_customer(
                conn, name, email, phone, suburb, '')

            # Find region from suburb
            row = conn.execute(
                "SELECT region_id FROM suburbs WHERE LOWER(name)=LOWER(?)",
                (suburb,)).fetchone()
            region_id = row['region_id'] if row else \
                conn.execute("SELECT id FROM regions ORDER BY id LIMIT 1").fetchone()['id']

            # Generate portal token
            portal_token = secrets.token_hex(32)

            # Create job
            for attempt in range(5):
                import sqlite3 as _sqlite3
                ref = generate_reference('booking', conn)
                try:
                    conn.execute("""
                        INSERT INTO jobs (
                            reference, job_type, customer_id,
                            customer_name, customer_email, customer_phone,
                            suburb, address, description,
                            service_types, region_id, tax_inclusive,
                            status, notes, portal_token)
                        VALUES (?, 'booking', ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, 1, 'pending', ?, ?)
                    """, (ref, customer_id,
                          name, email, phone,
                          suburb, stored_address or suburb,
                          message, services, region_id,
                          f"Web booking form: {services}",
                          portal_token))

                    job_id = conn.execute(
                        "SELECT id FROM jobs WHERE reference=?", (ref,)).fetchone()['id']

                    # Create email_imports record — identical to what _create_job does
                    conn.execute("""
                        INSERT INTO email_imports
                            (message_id, thread_id, in_reply_to, subject, sender,
                             body, imported_at, received_at, job_id, status, read)
                        VALUES (?, ?, NULL, ?, ?, ?,
                                datetime('now'), ?, ?, 'ok', 1)
                    """, (message_id, message_id,
                          subject, email, body[:8000],
                          received_at, job_id))

                    conn.commit()

                    # Auto-add job_parts for each service type
                    if services:
                        for stype in [s.strip() for s in services.split(',') if s.strip()]:
                            part = conn.execute(
                                """SELECT id, name, part_number, unit_cost FROM parts
                                   WHERE LOWER(name)=LOWER(?) AND active=1 LIMIT 1""",
                                (stype,)).fetchone()
                            if part:
                                conn.execute(
                                    """INSERT INTO job_parts
                                       (job_id, part_id, description, part_number, quantity, unit_cost)
                                       VALUES (?, ?, ?, ?, 1, ?)""",
                                    (job_id, part['id'], part['name'],
                                     part['part_number'] or '', part['unit_cost']))
                        conn.commit()
                        recalc_job_totals(conn, job_id)

                    break

                except _sqlite3.IntegrityError as e:
                    if 'reference' in str(e) and attempt < 4:
                        conn.rollback()
                        continue
                    raise

        base = os.environ.get('BASE_URL', '').rstrip('/')
        portal_url = f"{base}/job/{portal_token}" if base else ''

        log.info(f"API booking created: {ref} for {email}")
        return jsonify({
            'ok':         True,
            'reference':  ref,
            'portal_url': portal_url,
        })

    except Exception as e:
        log.error(f"API booking error: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': 'Server error — please try again'}), 500
