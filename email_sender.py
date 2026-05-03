"""
email_sender.py — Send email via Gmail using OAuth2 (SMTP + XOAUTH2).

Reuses the same OAuth2 credentials as the IMAP poller.
"""
import os
import smtplib
import email.mime.text
import email.mime.multipart
import email.mime.base
import email.encoders
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


def _smtp_connect():
    """Return an authenticated SMTP connection."""
    from_addr    = os.environ.get('GMAIL_USER', '')
    access_token = _get_access_token()
    auth_str     = base64.b64encode(
        f"user={from_addr}\x01auth=Bearer {access_token}\x01\x01".encode()
    ).decode()

    smtp = smtplib.SMTP('smtp.gmail.com', 587)
    smtp.ehlo()
    smtp.starttls()
    smtp.ehlo()
    code, resp = smtp.docmd('AUTH', 'XOAUTH2 ' + auth_str)
    if code != 235:
        raise RuntimeError(f"SMTP AUTH failed {code}: {resp}")
    return smtp, from_addr


def _new_message_id(from_addr):
    import uuid, time
    domain = from_addr.split('@')[-1]
    return f"<{int(time.time())}.{uuid.uuid4().hex[:12]}@{domain}>"


def send_reply(to_address, subject, body_text,
               in_reply_to=None, references=None,
               message_id_out=None):
    """Send a plain-text reply. Returns the Message-ID."""
    from_addr = os.environ.get('GMAIL_USER', '')
    if not from_addr:
        raise RuntimeError("GMAIL_USER not set")

    if not message_id_out:
        message_id_out = _new_message_id(from_addr)

    msg = email.mime.text.MIMEText(body_text, 'plain', 'utf-8')
    msg['From']       = from_addr
    msg['To']         = to_address
    msg['Subject']    = subject
    msg['Message-ID'] = message_id_out

    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        refs = references or in_reply_to
        if in_reply_to not in refs:
            refs = refs + ' ' + in_reply_to
        msg['References'] = refs.strip()

    smtp, from_addr = _smtp_connect()
    with smtp:
        smtp.sendmail(from_addr, [to_address], msg.as_bytes())

    log.info(f"Sent email to {to_address}, Message-ID: {message_id_out}")
    return message_id_out


def send_reply_with_attachment(to_address, subject, body_text,
                                attachment_bytes, attachment_filename,
                                attachment_mimetype='application/pdf',
                                in_reply_to=None, references=None,
                                message_id_out=None):
    """Send a reply with a binary attachment. Returns the Message-ID."""
    from_addr = os.environ.get('GMAIL_USER', '')
    if not from_addr:
        raise RuntimeError("GMAIL_USER not set")

    if not message_id_out:
        message_id_out = _new_message_id(from_addr)

    # Build multipart message
    outer = email.mime.multipart.MIMEMultipart()
    outer['From']       = from_addr
    outer['To']         = to_address
    outer['Subject']    = subject
    outer['Message-ID'] = message_id_out

    if in_reply_to:
        outer['In-Reply-To'] = in_reply_to
        refs = references or in_reply_to
        if in_reply_to not in refs:
            refs = refs + ' ' + in_reply_to
        outer['References'] = refs.strip()

    # Plain text body
    outer.attach(email.mime.text.MIMEText(body_text, 'plain', 'utf-8'))

    # PDF attachment
    part = email.mime.base.MIMEBase(*attachment_mimetype.split('/'))
    part.set_payload(attachment_bytes)
    email.encoders.encode_base64(part)
    part.add_header('Content-Disposition', 'attachment',
                    filename=attachment_filename)
    outer.attach(part)

    smtp, from_addr = _smtp_connect()
    with smtp:
        smtp.sendmail(from_addr, [to_address], outer.as_bytes())

    log.info(f"Sent email+attachment to {to_address}, Message-ID: {message_id_out}")
    return message_id_out

