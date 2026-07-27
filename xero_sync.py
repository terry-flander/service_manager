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

    # If Xero rotated the refresh token, save the new one back to .env
    new_refresh = tokens.get('refresh_token', refresh_token)
    if new_refresh != refresh_token:
        _save_env_key('XERO_REFRESH_TOKEN', new_refresh)
        os.environ['XERO_REFRESH_TOKEN'] = new_refresh
        log.info("Xero: refresh token rotated and saved to .env")

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
        log.error(f"Xero API {method} {path} → {e.code}: {body[:400]}")
        raise RuntimeError(f"Xero API error {e.code}: {body[:200]}")


def _tax_type_and_inclusive(job):
    """Map ServiceDesk tax_inclusive field to Xero tax fields."""
    tax_raw = job.get('tax_inclusive') or 0
    payment = (job.get('payment_type') or '').lower()
    gst_exempt = (tax_raw == 2)

    if payment == 'cash' or gst_exempt:
        return 'EXEMPTOUTPUT', 'EXCLUSIVE'  # GST Free Income, exclusive
    elif tax_raw == 1:
        return 'OUTPUT', 'INCLUSIVE'        # GST on Income, inclusive
    else:
        return 'OUTPUT', 'EXCLUSIVE'        # GST on Income, exclusive


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

    # Build line items
    line_items = []
    if job_parts:
        for jp in job_parts:
            line_items.append({
                'ItemCode':   jp['part_number'] or '',
                'Description': jp['description'] or '',
                'Quantity':    float(jp['quantity']),
                'UnitAmount':  float(jp['unit_cost']),
                'AccountCode': '240',
                'TaxType':     tax_type,
            })
    else:
        # No parts — single line with total
        line_items.append({
            'Description': f"Bicycle service — {job['reference']}",
            'Quantity':    1.0,
            'UnitAmount':  float(job['total'] or 0),
            'AccountCode': '240',
            'TaxType':     tax_type,
        })

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
        'DueDate':         f'/Date({int(date.fromisoformat(due_date).strftime("%s")) * 1000})/',
        'LineAmountTypes': line_amount_type,
        'LineItems':       line_items,
        'CurrencyCode':    'AUD',
        'SentToContact':   True,
    }

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
        dict mapping invoice_number → Xero status string
        e.g. {'fb0001': 'PAID', 'fb0002': 'AUTHORISED'}
    """
    if not invoice_numbers:
        return {}

    # Xero supports filtering by InvoiceNumbers as a comma-separated param
    nums_param = ','.join(invoice_numbers)
    path = f'Invoices?InvoiceNumbers={urllib.parse.quote(nums_param)}&summaryOnly=true'

    result = _xero_request('GET', path)
    invoices = result.get('Invoices', [])

    return {
        inv['InvoiceNumber']: inv['Status']
        for inv in invoices
        if inv.get('InvoiceNumber')
    }
