#!/usr/bin/env python3
"""
gmail_oauth_setup.py — Run this ONCE locally to authorise Gmail access.

Usage:
  python3 gmail_oauth_setup.py

What it does:
  1. Opens your browser to Google's OAuth2 consent screen
  2. You log in as info@theflyingbike.com.au and click Allow
  3. Google redirects back to localhost — this script captures the code
  4. Exchanges the code for a refresh token
  5. Saves GMAIL_REFRESH_TOKEN to your .env file

Requirements:
  - A Google Cloud project with the Gmail API enabled
  - OAuth2 credentials (Client ID + Client Secret) from Google Cloud Console
  - Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env before running

Google Cloud Console setup (one-time):
  1. Go to https://console.cloud.google.com
  2. Create a project (or select existing)
  3. APIs & Services → Enable APIs → search "Gmail API" → Enable
  4. APIs & Services → Credentials → Create Credentials → OAuth client ID
     - Application type: Desktop app
     - Name: ServiceDesk
  5. Download the JSON — copy client_id and client_secret into .env
  6. APIs & Services → OAuth consent screen
     - User Type: External (or Internal if Workspace)
     - Add scope: https://mail.google.com/
     - Add your email as a Test user (if External)
"""
import os
import sys
import json
import urllib.request
import urllib.parse
import webbrowser
import http.server
import threading
import hashlib
import base64
import secrets
import re

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

def save_refresh_token(token):
    """Write GMAIL_REFRESH_TOKEN= into .env, replacing if already present."""
    if not os.path.exists(env_path):
        with open(env_path, 'w') as f:
            f.write(f'GMAIL_REFRESH_TOKEN={token}\n')
        return

    with open(env_path) as f:
        content = f.read()

    if 'GMAIL_REFRESH_TOKEN' in content:
        content = re.sub(
            r'^GMAIL_REFRESH_TOKEN=.*$',
            f'GMAIL_REFRESH_TOKEN={token}',
            content, flags=re.MULTILINE
        )
    else:
        content += f'\nGMAIL_REFRESH_TOKEN={token}\n'

    with open(env_path, 'w') as f:
        f.write(content)

# ── PKCE helpers ──────────────────────────────────────────────────────────────
def make_pkce():
    verifier  = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b'=').decode()
    return verifier, challenge

# ── Local callback server ─────────────────────────────────────────────────────
class CallbackHandler(http.server.BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = dict(urllib.parse.parse_qsl(qs))
        CallbackHandler.code = params.get('code')
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        if CallbackHandler.code:
            body = b'''<html><body style="font-family:sans-serif;padding:2rem">
            <h2>&#10003; Authorisation successful!</h2>
            <p>You can close this window and return to the terminal.</p>
            </body></html>'''
        else:
            body = b'''<html><body style="font-family:sans-serif;padding:2rem">
            <h2>&#10007; Authorisation failed</h2>
            <p>No code received. Please try again.</p>
            </body></html>'''
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # suppress request logging

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    env = load_env()

    client_id     = env.get('GMAIL_CLIENT_ID', '').strip()
    client_secret = env.get('GMAIL_CLIENT_SECRET', '').strip()

    if not client_id or not client_secret:
        print("""
ERROR: GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set in .env

Add these lines to your .env file:
  GMAIL_CLIENT_ID=your-client-id.apps.googleusercontent.com
  GMAIL_CLIENT_SECRET=your-client-secret

Get them from:
  https://console.cloud.google.com → APIs & Services → Credentials
  Create an OAuth 2.0 Client ID (Desktop app type)
""")
        sys.exit(1)

    redirect_uri = 'http://localhost:8765/callback'
    scope        = 'https://mail.google.com/'
    verifier, challenge = make_pkce()
    state        = secrets.token_urlsafe(16)

    # Start local callback server in background thread
    server = http.server.HTTPServer(('localhost', 8765), CallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    # Build authorisation URL
    auth_url = 'https://accounts.google.com/o/oauth2/v2/auth?' + \
        urllib.parse.urlencode({
            'client_id':             client_id,
            'redirect_uri':          redirect_uri,
            'response_type':         'code',
            'scope':                 scope,
            'access_type':           'offline',
            'prompt':                'consent',  # forces refresh_token to be issued
            'state':                 state,
            'code_challenge':        challenge,
            'code_challenge_method': 'S256',
        })

    print(f"""
Gmail OAuth2 Setup
==================
Opening your browser to authorise access...

If the browser doesn't open automatically, visit:
  {auth_url[:80]}...

Log in as the Gmail account that receives booking emails
(e.g. info@theflyingbike.com.au) and click Allow.
""")

    webbrowser.open(auth_url)

    # Wait for the callback
    print("Waiting for authorisation... (press Ctrl+C to cancel)")
    timeout = 120
    for _ in range(timeout * 10):
        threading.Event().wait(0.1)
        if CallbackHandler.code:
            break
    else:
        print("Timed out waiting for authorisation.")
        sys.exit(1)

    server.shutdown()
    code = CallbackHandler.code
    print(f"\nAuthorisation code received. Exchanging for tokens...")

    # Exchange code for tokens
    token_data = urllib.parse.urlencode({
        'client_id':     client_id,
        'client_secret': client_secret,
        'code':          code,
        'redirect_uri':  redirect_uri,
        'grant_type':    'authorization_code',
        'code_verifier': verifier,
    }).encode()

    req = urllib.request.Request(
        'https://oauth2.googleapis.com/token',
        data=token_data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST'
    )

    try:
        with urllib.request.urlopen(req) as resp:
            tokens = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"\nToken exchange failed: {e.code} {e.reason}")
        print(error_body)
        sys.exit(1)

    refresh_token = tokens.get('refresh_token')
    if not refresh_token:
        print("""
ERROR: No refresh_token in response.

This usually means the account already has a token issued.
To force a new one:
  1. Go to https://myaccount.google.com/permissions
  2. Find your app and revoke access
  3. Run this script again
""")
        print("Response:", json.dumps(tokens, indent=2))
        sys.exit(1)

    save_refresh_token(refresh_token)

    print(f"""
Success!
========
Refresh token saved to .env as GMAIL_REFRESH_TOKEN.

Also add these to your .env if not already set:
  GMAIL_USER=info@theflyingbike.com.au
  GMAIL_CLIENT_ID={client_id}
  GMAIL_CLIENT_SECRET={client_secret}
  GMAIL_LABEL=Booking Email/Open Bookings
  GMAIL_POLL_MINUTES=5

Your ServiceDesk will now automatically import booking emails.
""")

if __name__ == '__main__':
    main()