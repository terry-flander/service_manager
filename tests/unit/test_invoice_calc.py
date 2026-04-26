"""
Unit tests for routes/invoice.py::calc_totals.
Pure math — no DB, no Flask context required.
"""
import pytest
from routes.invoice import calc_totals


def _parts(*line_totals):
    """Build a list of fake job_part dicts from (quantity, unit_cost) pairs."""
    return [{'quantity': q, 'unit_cost': c} for q, c in line_totals]


# ── Tax-inclusive (GST already in price) ─────────────────────────────────────

def test_inclusive_single_part():
    subtotal, gst, total = calc_totals(_parts((1, 110.0)), tax_inclusive=True)
    assert total    == 110.0
    assert gst      == round(110.0 / 11, 2)
    assert subtotal == round(110.0 - gst, 2)


def test_inclusive_gst_is_one_eleventh():
    _, gst, total = calc_totals(_parts((1, 55.0)), tax_inclusive=True)
    assert gst == round(55.0 / 11, 2)


def test_inclusive_subtotal_plus_gst_equals_total():
    subtotal, gst, total = calc_totals(_parts((2, 55.0)), tax_inclusive=True)
    assert abs(subtotal + gst - total) < 0.01


def test_inclusive_multiple_parts():
    parts = _parts((1, 44.0), (2, 33.0), (1, 11.0))
    subtotal, gst, total = calc_totals(parts, tax_inclusive=True)
    assert total == 121.0
    assert gst   == round(121.0 / 11, 2)


# ── Tax-exclusive (GST added on top) ─────────────────────────────────────────

def test_exclusive_single_part():
    subtotal, gst, total = calc_totals(_parts((1, 100.0)), tax_inclusive=False)
    assert subtotal == 100.0
    assert gst      == 10.0
    assert total    == 110.0


def test_exclusive_gst_is_ten_percent():
    subtotal, gst, _ = calc_totals(_parts((1, 250.0)), tax_inclusive=False)
    assert gst == round(subtotal * 0.10, 2)


def test_exclusive_total_equals_subtotal_plus_gst():
    subtotal, gst, total = calc_totals(_parts((3, 33.33)), tax_inclusive=False)
    assert abs(total - (subtotal + gst)) < 0.01


def test_exclusive_multiple_parts():
    parts = _parts((1, 50.0), (2, 25.0))
    subtotal, gst, total = calc_totals(parts, tax_inclusive=False)
    assert subtotal == 100.0
    assert gst      == 10.0
    assert total    == 110.0


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_parts_inclusive():
    subtotal, gst, total = calc_totals([], tax_inclusive=True)
    assert total == 0
    assert gst   == 0


def test_empty_parts_exclusive():
    subtotal, gst, total = calc_totals([], tax_inclusive=False)
    assert subtotal == 0
    assert gst      == 0
    assert total    == 0


def test_fractional_quantity():
    parts = _parts((0.5, 80.0))
    subtotal, gst, total = calc_totals(parts, tax_inclusive=False)
    assert subtotal == 40.0
    assert gst      == 4.0
    assert total    == 44.0
