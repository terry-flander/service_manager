"""
Unit tests for date/time parsing helpers in routes/import_jobs.py.
All functions are pure transformations — no DB or Flask required.
"""
import pytest
from routes.import_jobs import _parse_date, _parse_time, _end_time


# ── _parse_date ───────────────────────────────────────────────────────────────

def test_parse_date_dmy_slash():
    assert _parse_date('15/04/2026') == '2026-04-15'


def test_parse_date_dmy_single_digit():
    assert _parse_date('5/4/2026') == '2026-04-05'


def test_parse_date_iso():
    assert _parse_date('2026-04-15') == '2026-04-15'


def test_parse_date_two_digit_year():
    assert _parse_date('15/04/26') == '2026-04-15'


def test_parse_date_invalid_returns_none():
    assert _parse_date('not-a-date') is None


def test_parse_date_empty_returns_none():
    assert _parse_date('') is None


def test_parse_date_none_returns_none():
    assert _parse_date(None) is None


def test_parse_date_strips_whitespace():
    assert _parse_date('  15/04/2026  ') == '2026-04-15'


# ── _parse_time ───────────────────────────────────────────────────────────────

def test_parse_time_hhmm_on_slot():
    assert _parse_time('09:00') == '09:00'


def test_parse_time_snaps_to_nearest_half_hour_down():
    # 09:10 → nearest slot is 09:00
    result = _parse_time('09:10')
    assert result == '09:00'


def test_parse_time_snaps_to_nearest_half_hour_up():
    # 09:20 → nearest slot is 09:30
    result = _parse_time('09:20')
    assert result == '09:30'


def test_parse_time_am_pm_format():
    result = _parse_time('9:00 AM')
    assert result == '09:00'


def test_parse_time_pm_format():
    result = _parse_time('1:30 PM')
    assert result == '13:30'


def test_parse_time_no_space_ampm():
    result = _parse_time('9:00AM')
    assert result == '09:00'


def test_parse_time_invalid_returns_none():
    assert _parse_time('not-a-time') is None


def test_parse_time_empty_returns_none():
    assert _parse_time('') is None


def test_parse_time_none_returns_none():
    assert _parse_time(None) is None


def test_parse_time_with_seconds():
    result = _parse_time('09:00:00')
    assert result == '09:00'


# ── _end_time ─────────────────────────────────────────────────────────────────

def test_end_time_adds_one_hour():
    result = _end_time('09:00')
    assert result == '10:00'


def test_end_time_afternoon():
    result = _end_time('13:30')
    assert result == '14:30'


def test_end_time_late_start_snaps_to_last_slot():
    # 19:00 + 1h = 20:00; nearest TIME_SLOT is 19:30 (the last one)
    from routes.jobs import TIME_SLOTS
    result = _end_time('19:00')
    assert result == TIME_SLOTS[-1]


def test_end_time_none_returns_none():
    assert _end_time(None) is None


def test_end_time_result_is_valid_slot():
    from routes.jobs import TIME_SLOTS
    result = _end_time('08:00')
    assert result in TIME_SLOTS
