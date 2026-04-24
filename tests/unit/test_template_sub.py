"""
Unit tests for routes/email_replies.py — template substitution helpers.
All functions are pure string transformations.
"""
import pytest
from routes.email_replies import _fmt_date, _fmt_time_range, _substitute


# ── _fmt_date ─────────────────────────────────────────────────────────────────

def test_fmt_date_formats_correctly():
    # 2026-05-01 is a Friday
    assert _fmt_date('2026-05-01') == 'Friday, 1 May 2026'


def test_fmt_date_no_leading_zero_on_day():
    result = _fmt_date('2026-05-01')
    assert ', 1 ' in result  # day '1' not '01'


def test_fmt_date_double_digit_day():
    # 2026-01-15 is a Thursday
    assert _fmt_date('2026-01-15') == 'Thursday, 15 January 2026'


def test_fmt_date_empty_returns_empty():
    assert _fmt_date('') == ''


def test_fmt_date_none_returns_empty():
    assert _fmt_date(None) == ''


def test_fmt_date_invalid_returns_original():
    assert _fmt_date('not-a-date') == 'not-a-date'


def test_fmt_date_accepts_datetime_prefix():
    # Only first 10 chars are used
    assert _fmt_date('2026-05-01T09:00:00') == 'Friday, 1 May 2026'


# ── _fmt_time_range ───────────────────────────────────────────────────────────

def test_fmt_time_range_morning():
    assert _fmt_time_range('09:00', '10:00') == '9:00am to 10:00am'


def test_fmt_time_range_afternoon():
    assert _fmt_time_range('13:30', '14:30') == '1:30pm to 2:30pm'


def test_fmt_time_range_ten_am():
    assert _fmt_time_range('10:00', '11:00') == '10:00am to 11:00am'


def test_fmt_time_range_noon():
    assert _fmt_time_range('12:00', '13:00') == '12:00pm to 1:00pm'


def test_fmt_time_range_no_end():
    result = _fmt_time_range('09:00', '')
    assert result == '9:00am'


def test_fmt_time_range_no_start():
    result = _fmt_time_range('', '10:00')
    assert result == '10:00am'


def test_fmt_time_range_both_empty():
    assert _fmt_time_range('', '') == ''


def test_fmt_time_range_both_none():
    assert _fmt_time_range(None, None) == ''


# ── _substitute ───────────────────────────────────────────────────────────────

def _make_job(**kwargs):
    """Return a dict that satisfies _substitute's key expectations."""
    defaults = {
        'customer_name':  'Alice Smith',
        'customer_email': 'alice@example.com',
        'customer_phone': '0412345678',
        'suburb':         'Fitzroy',
        'address':        '123 Main St, Fitzroy',
        'reference':      'BK-2026-001',
        'scheduled_date': '2026-05-01',
        'scheduled_time': '09:00',
        'end_time':       '10:00',
        'service_types':  'General Service',
        'description':    'Full service needed',
        'region_name':    'Inner North',
    }
    defaults.update(kwargs)
    return defaults


def test_substitute_customer_name():
    job = _make_job()
    result = _substitute('Hello {{customer_name}}', job)
    assert result == 'Hello Alice Smith'


def test_substitute_first_name():
    job = _make_job(customer_name='Alice Smith')
    result = _substitute('Hi {{first_name}}', job)
    assert result == 'Hi Alice'


def test_substitute_reference():
    job = _make_job()
    result = _substitute('Your reference is {{reference}}', job)
    assert result == 'Your reference is BK-2026-001'


def test_substitute_scheduled_date_raw():
    job = _make_job()
    result = _substitute('Date: {{scheduled_date}}', job)
    assert result == 'Date: 2026-05-01'


def test_substitute_scheduled_date_formatted():
    job = _make_job()
    result = _substitute('Date: {{scheduled_date_formatted}}', job)
    assert result == 'Date: Friday, 1 May 2026'


def test_substitute_scheduled_time_formatted():
    job = _make_job()
    result = _substitute('Time: {{scheduled_time_formatted}}', job)
    assert result == 'Time: 9:00am to 10:00am'


def test_substitute_multiple_fields():
    job = _make_job()
    template = 'Hi {{first_name}}, your job {{reference}} is on {{scheduled_date_formatted}}.'
    result = _substitute(template, job)
    assert 'Alice' in result
    assert 'BK-2026-001' in result
    assert 'Friday, 1 May 2026' in result


def test_substitute_unknown_placeholder_unchanged():
    job = _make_job()
    result = _substitute('Value: {{unknown_field}}', job)
    assert result == 'Value: {{unknown_field}}'


def test_substitute_empty_field_value():
    job = _make_job(customer_phone='')
    result = _substitute('Phone: {{customer_phone}}', job)
    assert result == 'Phone: '


def test_substitute_no_placeholders():
    job = _make_job()
    text = 'No placeholders here.'
    assert _substitute(text, job) == text


def test_substitute_missing_end_time():
    job = _make_job(scheduled_time='09:00')
    del job['end_time']
    result = _substitute('Time: {{scheduled_time_formatted}}', job)
    assert '9:00am' in result
