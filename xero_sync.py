"""
xero_sync.py — Xero API integration for ServiceDesk.

Environment variables required (.env):
  XERO_CLIENT_ID       OAuth2 client ID from Xero developer portal
  XERO_CLIENT_SECRET   OAuth2 client secret
  XERO_REFRESH_TOKEN   Long-lived refresh token (from xero_oauth_setup.py)
  XERO_TENANT_ID       Xero organisation/tenant ID (from xero_oauth_setup.py)

Functions:
  push_invoice(job, job_parts, invoice_number)
    → Create invoice in Xero, send email to customer, return Xero invoice ID

  check_paid_invoices(job_ids)
    → Check a list of job_ids against Xero; return dict {job_id: status}
    → status values: 'paid', 'authorised', 'voided'
"""
import os
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from datetime import date, timedelta

log = logging.getLogger('app')

XERO_TOKEN_URL = 'https://identity.xero.com/connect/token'
XERO_API_BASE  = 'https://api.xero.com/api.xro/2.0'

# In-memory token cache — refreshed when expired
_cached_token = None


def _get_access_token():
    """Return a valid Xero access token, refreshing if necessary."""
    global _cached_token
    import time

    # Refresh if no cached token or within 60s of expiry
    if _cached_token and time.time() < _cached_token['expires_at'] - 60:
        return _cached_token['token']

    client_id     = os.environ.get('XERO_CLIENT_ID', '')
    client_secret = os.environ.get('XERO_CLIENT_SECRET', '')
    # Prefer DB-stored token (updated on rotation) over env var
    refresh_token = ''
    try:
        from models import get_db
        with get_db() as _conn:
            _row = _conn.execute(
                "SELECT value FROM settings WHERE key='xero_refresh_token'").fetchone()
            if _row:
                refresh_token = _row['value']
    except Exception:
        pass
    if not refresh_token:
        refresh_token = os.environ.get('XERO_REFRESH_TOKEN', '')

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(
            "Missing XERO_CLIENT_ID, XERO_CLIENT_SECRET or XERO_REFRESH_TOKEN in .env")

    import base64
    credentials = base64.b64encode(
        f"{client_id}:{client_secret}".encode()).decode()

    data = urllib.parse.urlencode({
        'grant_type':    'refresh_token',
        'refresh_token': refresh_token,
    }).encode()

    req = urllib.request.Request(
        XERO_TOKEN_URL, data=data,
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type':  'application/x-www-form-urlencoded',
        })

    with urllib.request.urlopen(req, timeout=15) as resp:
        tokens = json.loads(resp.read().decode())

    # If Xero rotated the refresh token, save the new one
    new_refresh = tokens.get('refresh_token', refresh_token)
    if new_refresh != refresh_token:
        os.environ['XERO_REFRESH_TOKEN'] = new_refresh
        # Save to DB settings so it persists across container restarts
        try:
            from models import get_db
            with get_db() as _conn:
                _conn.execute(
                    "INSERT INTO settings (key,value) VALUES ('xero_refresh_token',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (new_refresh,))
                _conn.commit()
        except Exception as _e:
            log.warning(f"Xero: could not save rotated refresh token to DB: {_e}")
        log.info("Xero: refresh token rotated and saved")

    _cached_token = {
        'token':      tokens['access_token'],
        'expires_at': time.time() + tokens.get('expires_in', 1800),
    }
    return _cached_token['token']


def _save_env_key(key, value):
    """Update a single key in .env without disturbing other lines."""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith(f'{key}='):
                    lines.append(f'{key}={value}\n')
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f'{key}={value}\n')
    with open(env_path, 'w') as f:
        f.writelines(lines)


def _xero_request(method, path, payload=None):
    """Make an authenticated Xero API request. Returns parsed JSON."""
    token     = _get_access_token()
    tenant_id = os.environ.get('XERO_TENANT_ID', '')
    if not tenant_id:
        raise RuntimeError("Missing XERO_TENANT_ID in .env")

    url  = f"{XERO_API_BASE}/{path}"
    body = json.dumps(payload).encode() if payload else None

    req = urllib.request.Request(
        url, data=body, method=method,
        headers={
            'Authorization':  f'Bearer {token}',
            'Xero-Tenant-Id': tenant_id,
            'Content-Type':   'application/json',
            'Accept':         'application/json',
        })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error(f"Xero API {method} {path} → {e.code}: {body}")
        raise RuntimeError(f"Xero API error {e.code}: {body}")


def _tax_type_and_inclusive(job):
    """Map ServiceDesk tax_inclusive field to Xero tax fields."""
    tax_raw = job['tax_inclusive'] or 0
    payment = (job['payment_type'] or '').lower()
    gst_exempt = (tax_raw == 2)

    if payment == 'cash' or gst_exempt:
        return 'EXEMPTOUTPUT', None          # GST Free — no LineAmountTypes needed
    elif tax_raw == 1:
        return 'OUTPUT', 'Inclusive'         # Tax inclusive — Xero format
    else:
        return 'OUTPUT', None                # Tax exclusive — Xero default


def push_invoice(job, job_parts, invoice_number):
    """
    Create an AUTHORISED invoice in Xero, then trigger Xero to email it
    to the customer.

    Args:
        job            — sqlite3.Row or dict from jobs table
        job_parts      — list of sqlite3.Row or dicts from job_parts table
        invoice_number — the pre-assigned invoice number (e.g. 'fb0042')

    Returns:
        xero_invoice_id (str) — Xero's internal invoice UUID
    """
    import re as _re

    tax_type, line_amount_type = _tax_type_and_inclusive(job)
    due_date = (date.today() + timedelta(days=7)).strftime('%Y-%m-%d')

    # Address
    address  = job['address'] or ''
    suburb   = job['suburb']  or ''
    pc_match = _re.search(r'\b(\d{4})\b', address)
    postcode = pc_match.group(1) if pc_match else ''

    is_sale_bike = (job['job_type'] or '') == 'sale_bike'

    if is_sale_bike:
        # Bike for Sale: single line, description from bikes_for_sale,
        # total = full sale price, always tax inclusive
        try:
            from models import get_db as _gdb
            with _gdb() as _conn:
                _brow = _conn.execute(
                    "SELECT short_desc, year_est FROM bikes_for_sale WHERE job_id=?",
                    (job['id'],)).fetchone()
            if _brow and _brow['short_desc']:
                bike_desc = _brow['short_desc']
                if _brow['year_est']:
                    bike_desc = f"{_brow['year_est']} {bike_desc}"
            else:
                bike_desc = job['bike_description'] or 'Bicycle'
        except Exception:
            bike_desc = job['bike_description'] or 'Bicycle'

        description     = bike_desc
        unit_amount     = float(job['total'] or 0)
        tax_type        = 'OUTPUT'
        line_amount_type = 'Inclusive'
    else:
        # Send a single consolidated line item using the job's stored total.
        if line_amount_type == 'Inclusive':
            unit_amount = float(job['total'] or 0)
        else:
            unit_amount = float(job['subtotal'] or job['total'] or 0)

        if job_parts:
            desc_lines = []
            for jp in job_parts:
                pnum  = (jp.get('part_number') or '').strip()
                pdesc = (jp.get('description') or '').strip()
                qty   = float(jp['quantity'])
                price = float(jp['unit_cost'])
                if pnum and pdesc and pnum != pdesc:
                    label = f"{pnum} ({pdesc})"
                elif pnum:
                    label = pnum
                else:
                    label = pdesc
                desc_lines.append(f"{qty:.0f}x {label} ${price:.2f}")
            description = '\n'.join(desc_lines)
        else:
            description = f"Bicycle service — {job['reference']}"

    line_items = [{
        'Description': description,
        'Quantity':    1.0,
        'UnitAmount':  unit_amount,
        'AccountCode': '240',
        'TaxType':     tax_type,
    }]

    payload = {
        'Type':    'ACCREC',
        'Status':  'AUTHORISED',
        'Contact': {
            'Name':         job['customer_name'] or '',
            'EmailAddress': job['customer_email'] or '',
            'Addresses': [{
                'AddressType': 'POBOX',
                'AddressLine1': address,
                'City':         suburb,
                'Region':       'Victoria',
                'PostalCode':   postcode,
                'Country':      'Australia',
            }],
        },
        'InvoiceNumber':   invoice_number,
        'Reference':       job['reference'] or '',
        'DueDate':         due_date,
        'LineItems':       line_items,
        'CurrencyCode':    'AUD',
        'SentToContact':   True,
    }
    if line_amount_type:
        payload['LineAmountTypes'] = line_amount_type

    log.info(f"Xero invoice payload: {json.dumps(payload, default=str)[:800]}")
    result = _xero_request('POST', 'Invoices', {'Invoices': [payload]})
    invoices = result.get('Invoices', [])
    if not invoices:
        raise RuntimeError("Xero returned no invoice in response")

    inv = invoices[0]
    if inv.get('StatusAttributeString') == 'ERROR' or inv.get('Status') == 'ERROR':
        errors = inv.get('ValidationErrors', [])
        msg = '; '.join(e.get('Message', '') for e in errors)
        raise RuntimeError(f"Xero invoice validation error: {msg}")

    xero_id = inv['InvoiceID']
    log.info(f"Xero: invoice {invoice_number} created (ID: {xero_id})")

    # Send the invoice email via Xero
    try:
        _xero_request('POST', f'Invoices/{xero_id}/Email', {})
        log.info(f"Xero: invoice email sent for {invoice_number}")
    except Exception as e:
        # Email send failure is non-fatal — invoice is still created
        log.warning(f"Xero: invoice created but email send failed: {e}")

    return xero_id


def check_paid_invoices(invoice_numbers):
    """
    Check the payment status of a list of invoice numbers in Xero.

    Args:
        invoice_numbers — list of strings e.g. ['fb0001', 'fb0002']

    Returns:
        dict mapping invoice_number → dict with keys:
            status      — Xero status string ('PAID', 'AUTHORISED', etc.)
            paid_date   — ISO date string or None
            amount_paid — float or None
    """
    if not invoice_numbers:
        return {}

    import re as _re

    # Do NOT use summaryOnly — we need FullyPaidOnDate and AmountPaid
    nums_param = ','.join(invoice_numbers)
    path = f'Invoices?InvoiceNumbers={urllib.parse.quote(nums_param)}'

    result = _xero_request('GET', path)
    invoices = result.get('Invoices', [])

    out = {}
    for inv in invoices:
        inv_num = inv.get('InvoiceNumber')
        if not inv_num:
            continue

        # Parse FullyPaidOnDate — Xero returns /Date(ms+offset)/ format
        paid_date = None
        fpod = inv.get('FullyPaidOnDate') or ''
        if fpod:
            ms_match = _re.search(r'\d+', fpod)
            if ms_match:
                import datetime as _dt
                ts = int(ms_match.group()) / 1000
                paid_date = _dt.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')

        amount_paid = inv.get('AmountPaid') or None
        if amount_paid is not None:
            try:
                amount_paid = float(amount_paid)
            except (TypeError, ValueError):
                amount_paid = None

        out[inv_num] = {
            'status':      inv.get('Status', ''),
            'paid_date':   paid_date,
            'amount_paid': amount_paid,
        }

    return out
