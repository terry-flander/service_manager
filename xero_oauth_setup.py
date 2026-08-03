#!/usr/bin/env python3
"""
xero_oauth_setup.py — Run this ONCE locally to authorise Xero API access.

Usage:
  python3 xero_oauth_setup.py

What it does:
  1. Opens your browser to Xero's OAuth2 consent screen
  2. You log in and click Allow
  3. Xero redirects back to localhost — this script captures the code
  4. Exchanges the code for access + refresh tokens
  5. Fetches your Xero tenant ID (organisation)
  6. Saves XERO_CLIENT_ID, XERO_CLIENT_SECRET, XERO_REFRESH_TOKEN,
     and XERO_TENANT_ID to your .env file

Xero Developer Portal setup (one-time):
  1. Go to https://developer.xero.com/app/manage
  2. Click "New app"
     - App name: ServiceDesk TFB
     - Company URL: https://theflyingbike.com.au
     - OAuth 2.0 redirect URI: http://localhost:8787/callback
     - App type: Web app
  3. Note the Client ID and Client Secret
  4. Add scopes: accounting.contacts accounting.invoices accounting.payments offline_access
  5. Set XERO_CLIENT_ID and XERO_CLIENT_SECRET in .env before running this script

Requirements:
  pip install requests (or use urllib — this script uses only stdlib)
"""
import os
import sys
import json
import urllib.request
import urllib.parse
import webbrowser
import http.server
import threading
import base64
import secrets

# ── Load .env ─────────────────────────────────────────────────────────────────
env_path = os.path.join(os.path.dirname(__file__), '.env')

def load_env():
    env = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def save_env_key(key, value):
    """Write or update a key in .env"""
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
    print(f"  Saved {key} to .env")

# ── Config ────────────────────────────────────────────────────────────────────
env = load_env()

CLIENT_ID     = env.get('XERO_CLIENT_ID', '').strip()
CLIENT_SECRET = env.get('XERO_CLIENT_SECRET', '').strip()
REDIRECT_URI  = 'http://localhost:8787/callback'
SCOPES        = 'accounting.contacts accounting.invoices accounting.payments offline_access'

XERO_AUTH_URL  = 'https://login.xero.com/identity/connect/authorize'
XERO_TOKEN_URL = 'https://identity.xero.com/connect/token'
XERO_CONNECTIONS_URL = 'https://api.xero.com/connections'

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: Set XERO_CLIENT_ID and XERO_CLIENT_SECRET in .env first.")
    sys.exit(1)

# ── Step 1: Build auth URL ─────────────────────────────────────────────────────
state = secrets.token_hex(16)

params = {
    'response_type': 'code',
    'client_id':     CLIENT_ID,
    'redirect_uri':  REDIRECT_URI,
    'scope':         SCOPES,
    'state':         state,
}
auth_url = XERO_AUTH_URL + '?' + urllib.parse.urlencode(params)

print("=== Xero OAuth Setup ===")
print(f"\nOpening browser to:\n{auth_url}\n")
print("If browser doesn't open, paste the URL manually.")

# ── Step 2: Local server to capture the callback ───────────────────────────────
auth_code = None

class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code   = params.get('code', [None])[0]
        st     = params.get('state', [None])[0]

        if code and st == state:
            auth_code = code
            body = b"<h1>Authorised!</h1><p>You can close this tab.</p>"
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h1>Error</h1>")

    def log_message(self, format, *args):
        pass  # Suppress request logs

server = http.server.HTTPServer(('localhost', 8787), CallbackHandler)
thread = threading.Thread(target=server.serve_forever)
thread.daemon = True
thread.start()

webbrowser.open(auth_url)

print("Waiting for Xero authorisation (complete in your browser)...")
while auth_code is None:
    import time
    time.sleep(0.5)

server.shutdown()
print(f"\nAuthorisation code received.")

# ── Step 3: Exchange code for tokens ──────────────────────────────────────────
credentials = base64.b64encode(
    f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

token_data = urllib.parse.urlencode({
    'grant_type':   'authorization_code',
    'code':         auth_code,
    'redirect_uri': REDIRECT_URI,
}).encode()

req = urllib.request.Request(
    XERO_TOKEN_URL,
    data=token_data,
    headers={
        'Authorization': f'Basic {credentials}',
        'Content-Type':  'application/x-www-form-urlencoded',
    }
)

print("\nExchanging code for tokens...")
with urllib.request.urlopen(req) as resp:
    tokens = json.loads(resp.read().decode())

access_token  = tokens['access_token']
refresh_token = tokens['refresh_token']
print(f"  Access token:  {access_token[:20]}...")
print(f"  Refresh token: {refresh_token[:20]}...")

# ── Step 4: Fetch tenant ID ───────────────────────────────────────────────────
print("\nFetching Xero organisation (tenant ID)...")
req2 = urllib.request.Request(
    XERO_CONNECTIONS_URL,
    headers={'Authorization': f'Bearer {access_token}'}
)
with urllib.request.urlopen(req2) as resp:
    connections = json.loads(resp.read().decode())

if not connections:
    print("ERROR: No Xero organisations found for this account.")
    sys.exit(1)

if len(connections) > 1:
    print("Multiple organisations found:")
    for i, c in enumerate(connections):
        print(f"  {i+1}. {c['tenantName']} ({c['tenantId']})")
    idx = int(input("Select organisation number: ")) - 1
    tenant = connections[idx]
else:
    tenant = connections[0]

tenant_id   = tenant['tenantId']
tenant_name = tenant['tenantName']
print(f"  Organisation: {tenant_name}")
print(f"  Tenant ID:    {tenant_id}")

# ── Step 5: Save to .env ──────────────────────────────────────────────────────
print("\nSaving to .env...")
save_env_key('XERO_REFRESH_TOKEN', refresh_token)
save_env_key('XERO_TENANT_ID',     tenant_id)

print(f"""
=== Done ===

The following have been saved to .env:
  XERO_REFRESH_TOKEN
  XERO_TENANT_ID

The access token expires every 30 minutes — xero_sync.py refreshes it
automatically using the refresh token.

Xero refresh tokens expire after 60 days if unused. Running the app
regularly (which you do) keeps them alive automatically.

Next step: add TFB_API_SECRET to .env if not already set, then:
  docker compose down && docker compose up -d
""")
