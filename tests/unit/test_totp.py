"""
Unit tests for totp.py — RFC 6238 / RFC 4226 implementation.
All functions are pure (deterministic given inputs), no side effects.
"""
import base64
import time

import pytest

from totp import _hotp, get_totp, verify_totp, otp_auth_uri, generate_secret


# RFC 4226 Appendix D test vectors
# Secret: ASCII bytes of "12345678901234567890", base32-encoded
RFC_SECRET = base64.b32encode(b'12345678901234567890').decode()

RFC_VECTORS = [
    (0,  755224),
    (1,  287082),
    (2,  359152),
    (3,  969429),
    (4,  338314),
    (5,  254676),
    (6,  287922),
    (7,  162583),
    (8,  399871),
    (9,  520489),
]


# ── _hotp (RFC 4226) ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("counter,expected", RFC_VECTORS)
def test_hotp_rfc_vectors(counter, expected):
    assert _hotp(RFC_SECRET, counter) == expected


def test_hotp_returns_int_less_than_million():
    secret = generate_secret()
    for i in range(5):
        code = _hotp(secret, i)
        assert 0 <= code < 1_000_000


def test_hotp_deterministic():
    secret = generate_secret()
    assert _hotp(secret, 42) == _hotp(secret, 42)


# ── get_totp ──────────────────────────────────────────────────────────────────

def test_get_totp_returns_six_digits():
    secret = generate_secret()
    code = get_totp(secret)
    assert len(code) == 6
    assert code.isdigit()


def test_get_totp_deterministic_at_time():
    secret = generate_secret()
    t = 1_700_000_000.0
    assert get_totp(secret, at_time=t) == get_totp(secret, at_time=t)


def test_get_totp_changes_each_30s_step():
    secret = generate_secret()
    t = 1_700_000_000.0
    code_now  = get_totp(secret, at_time=t)
    code_next = get_totp(secret, at_time=t + 30)
    # Codes are for different time steps — they should differ
    # (extremely unlikely to collide)
    assert code_now != code_next


def test_get_totp_same_within_30s_window():
    secret = generate_secret()
    t = 1_700_000_014.0
    code_a = get_totp(secret, at_time=t)
    code_b = get_totp(secret, at_time=t + 10)  # still within same 30s step
    assert code_a == code_b


# ── verify_totp ───────────────────────────────────────────────────────────────

def test_verify_totp_current_code_passes():
    secret = generate_secret()
    t_step = int(time.time() // 30)
    code = f"{_hotp(secret, t_step):06d}"
    assert verify_totp(secret, code)


def test_verify_totp_previous_step_passes():
    secret = generate_secret()
    t_step = int(time.time() // 30)
    code = f"{_hotp(secret, t_step - 1):06d}"
    assert verify_totp(secret, code)


def test_verify_totp_next_step_passes():
    secret = generate_secret()
    t_step = int(time.time() // 30)
    code = f"{_hotp(secret, t_step + 1):06d}"
    assert verify_totp(secret, code)


def test_verify_totp_expired_code_fails():
    secret = generate_secret()
    t_step = int(time.time() // 30)
    # Two steps back is outside the default window=1
    code = f"{_hotp(secret, t_step - 2):06d}"
    assert not verify_totp(secret, code)


def test_verify_totp_wrong_code_fails():
    secret = generate_secret()
    assert not verify_totp(secret, '000000')


def test_verify_totp_rejects_non_digit():
    secret = generate_secret()
    assert not verify_totp(secret, 'abcdef')


def test_verify_totp_rejects_wrong_length():
    secret = generate_secret()
    assert not verify_totp(secret, '12345')
    assert not verify_totp(secret, '1234567')


def test_verify_totp_strips_spaces():
    secret = generate_secret()
    t_step = int(time.time() // 30)
    code = f"{_hotp(secret, t_step):06d}"
    spaced = f"{code[:3]} {code[3:]}"
    assert verify_totp(secret, spaced)


def test_verify_totp_wider_window():
    secret = generate_secret()
    t_step = int(time.time() // 30)
    code = f"{_hotp(secret, t_step - 2):06d}"
    assert verify_totp(secret, code, window=2)


# ── otp_auth_uri ──────────────────────────────────────────────────────────────

def test_otp_auth_uri_scheme():
    uri = otp_auth_uri('ABCDEFGH', 'user@example.com')
    assert uri.startswith('otpauth://totp/')


def test_otp_auth_uri_contains_secret():
    secret = 'ABCDEFGH'
    uri = otp_auth_uri(secret, 'user@example.com')
    assert f'secret={secret}' in uri


def test_otp_auth_uri_contains_issuer():
    uri = otp_auth_uri('ABCDEFGH', 'user@example.com', issuer='MyApp')
    assert 'issuer=MyApp' in uri


def test_otp_auth_uri_contains_email():
    uri = otp_auth_uri('ABCDEFGH', 'user@example.com')
    assert 'user%40example.com' in uri or 'user@example.com' in uri


def test_otp_auth_uri_algorithm():
    uri = otp_auth_uri('ABCDEFGH', 'user@example.com')
    assert 'algorithm=SHA1' in uri


def test_otp_auth_uri_period():
    uri = otp_auth_uri('ABCDEFGH', 'user@example.com')
    assert 'period=30' in uri


# ── generate_secret ───────────────────────────────────────────────────────────

def test_generate_secret_is_base32():
    secret = generate_secret()
    # Must decode without error
    base64.b32decode(secret)


def test_generate_secret_length():
    secret = generate_secret()
    # 20 bytes → 32 base32 chars (with padding)
    assert len(secret) >= 32


def test_generate_secret_is_unique():
    secrets = {generate_secret() for _ in range(10)}
    assert len(secrets) == 10
