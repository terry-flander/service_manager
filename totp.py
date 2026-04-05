"""
TOTP (Time-based One-Time Password) — RFC 6238
Implemented using Python stdlib only: hmac, hashlib, struct, base64, os, time.
Compatible with Google Authenticator, Authy, 1Password, Microsoft Authenticator, etc.
"""
import hmac
import hashlib
import struct
import time
import base64
import os
import urllib.parse


def generate_secret():
    """Generate a random 20-byte base32-encoded TOTP secret."""
    raw = os.urandom(20)
    return base64.b32encode(raw).decode('utf-8')


def _hotp(secret: str, counter: int) -> int:
    """HMAC-based OTP (RFC 4226)."""
    key = base64.b32decode(secret.upper().replace(' ', ''))
    msg = struct.pack('>Q', counter)
    h   = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code   = struct.unpack('>I', h[offset:offset + 4])[0] & 0x7FFFFFFF
    return code % 1_000_000


def get_totp(secret: str, at_time: float = None) -> str:
    """Return the current 6-digit TOTP code."""
    t = int((at_time or time.time()) // 30)
    return f"{_hotp(secret, t):06d}"


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """
    Verify a TOTP code. Accepts codes from window steps either side
    of the current time step (default ±30 seconds) to handle clock drift.
    """
    code = code.replace(' ', '').strip()
    if len(code) != 6 or not code.isdigit():
        return False
    t = int(time.time() // 30)
    for step in range(-window, window + 1):
        if f"{_hotp(secret, t + step):06d}" == code:
            return True
    return False


def otp_auth_uri(secret: str, email: str, issuer: str = "ServiceDesk") -> str:
    """
    Build an otpauth:// URI for QR code generation.
    Format: otpauth://totp/ISSUER:EMAIL?secret=SECRET&issuer=ISSUER
    """
    label   = urllib.parse.quote(f"{issuer}:{email}")
    params  = urllib.parse.urlencode({
        'secret': secret,
        'issuer': issuer,
        'algorithm': 'SHA1',
        'digits': 6,
        'period': 30,
    })
    return f"otpauth://totp/{label}?{params}"
