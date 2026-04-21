"""
email_sender.py — Send email via Gmail using OAuth2 (SMTP + XOAUTH2).

Reuses the same OAuth2 credentials as the IMAP poller.
"""
import os
import smtplib
import email.mime.text
import email.mime.multipart
import base64
import json
import urllib.request
import urllib.parse
import logging

log = logging.getLogger('email_sender')


def _get_access_token():
    """Exchange refresh token for a fresh access token."""
    client_id     = os.environ.get('GMAIL_CLIENT_ID', '')
    client_secret = os.environ.get('GMAIL_CLIENT_SECRET', '')
    refresh_token = os.environ.get('GMAIL_REFRESH_TOKEN', '')

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError("Missing GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, or GMAIL_REFRESH_TOKEN")

    data = urllib.parse.urlencode({
        'client_id':     client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type':    'refresh_token',
    }).encode()

    req = urllib.request.Request(
        'https://oauth2.googleapis.com/token',
        data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read())
            token  = tokens.get('access_token')
            if not token:
                raise RuntimeError(f"No access_token in response: {tokens}")
            return token
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Token refresh failed {e.code}: {e.read().decode()}")


def send_reply(to_address, subject, body_text,
               in_reply_to=None, references=None,
               message_id_out=None):
    """
    Send an email from GMAIL_USER via Gmail SMTP+OAuth2.

    Args:
        to_address:     Recipient email
        subject:        Email subject line
        body_text:      Plain-text body
        in_reply_to:    Message-ID of the email being replied to
        references:     Full References header string
        message_id_out: Pre-generated Message-ID to use (optional)

    Returns:
        message_id: The Message-ID header of the sent email
    """
    from_addr = os.environ.get('GMAIL_USER', '')
    if not from_addr:
        raise RuntimeError("GMAIL_USER not set")

    # Build Message-ID if not supplied
    if not message_id_out:
        import uuid, time
        domain = from_addr.split('@')[-1]
        message_id_out = f"<{int(time.time())}.{uuid.uuid4().hex[:12]}@{domain}>"

    # Build MIME message
    msg = email.mime.text.MIMEText(body_text, 'plain', 'utf-8')
    msg['From']       = from_addr
    msg['To']         = to_address
    msg['Subject']    = subject
    msg['Message-ID'] = message_id_out

    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        refs = (references or in_reply_to)
        if in_reply_to not in refs:
            refs = refs + ' ' + in_reply_to
        msg['References'] = refs.strip()

    # Get access token
    access_token = _get_access_token()

    # Build XOAUTH2 string for SMTP
    auth_str = base64.b64encode(
        f"user={from_addr}\x01auth=Bearer {access_token}\x01\x01".encode()
    ).decode()

    # Connect and send
    with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        # SMTP XOAUTH2 — send pre-encoded base64 directly
        code, resp = smtp.docmd('AUTH', 'XOAUTH2 ' + auth_str)
        if code != 235:
            raise RuntimeError(f"SMTP AUTH failed {code}: {resp}")
        smtp.sendmail(from_addr, [to_address], msg.as_bytes())

    log.info(f"Sent email to {to_address}, Message-ID: {message_id_out}")
    return message_id_out
