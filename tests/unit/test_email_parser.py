"""
Unit tests for email_poller pure functions.
No DB, no IMAP, no network — all functions tested here have no side effects.
"""
import base64
import email as _email_module
from email.mime.text import MIMEText

import pytest

from email_poller import (
    _strip_footer,
    _extract_field,
    _extract_message,
    _detect_service_types,
    _parse_email,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_msg(body_text, from_addr='test@example.com', subject='Test', from_name=''):
    """Build an email.Message with a UTF-8 text/plain body."""
    msg = MIMEText(body_text, 'plain', 'utf-8')
    msg['From'] = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg['Subject'] = subject
    return msg


CONTACT_FORM_FOOTER = (
    "\n--\n"
    "This e-mail was sent from a contact form on The Flying Bike "
    "(https://theflyingbike.com.au)"
)


# ── _strip_footer ─────────────────────────────────────────────────────────────

def test_strip_footer_removes_dash_separator():
    text = "Hello world\n--\nSome signature here"
    assert _strip_footer(text) == "Hello world"


def test_strip_footer_removes_contact_form_line():
    text = "Hello world\nThis e-mail was sent from a contact form on The Flying Bike"
    result = _strip_footer(text)
    assert "contact form" not in result
    assert "Hello world" in result


def test_strip_footer_leaves_clean_text_unchanged():
    text = "Name: John\nEmail: john@example.com\nMessage: Hello"
    assert _strip_footer(text) == text


def test_strip_footer_removes_multiline_after_separator():
    text = "Body text\n--\nLine one\nLine two\nLine three"
    assert _strip_footer(text) == "Body text"


# ── _extract_field ────────────────────────────────────────────────────────────

def test_extract_field_basic():
    text = "Name: John Smith\nEmail: john@example.com"
    assert _extract_field(text, 'Name') == 'John Smith'


def test_extract_field_stops_at_next_label():
    text = "Name: John Smith\nEmail: john@example.com\nPhone: 0412345678"
    assert _extract_field(text, 'Name') == 'John Smith'


def test_extract_field_case_insensitive():
    text = "name: Alice\nemail: alice@example.com"
    assert _extract_field(text, 'Name') == 'Alice'


def test_extract_field_missing_returns_empty():
    text = "Name: John\nEmail: john@example.com"
    assert _extract_field(text, 'Phone') == ''


def test_extract_field_multiple_label_fallback():
    text = "Location: Fitzroy\nMessage: test"
    assert _extract_field(text, 'Suburb', 'Location') == 'Fitzroy'


def test_extract_field_strips_whitespace():
    text = "Name:   Alice   \nEmail: alice@example.com"
    assert _extract_field(text, 'Name') == 'Alice'


def test_extract_field_dash_separator():
    text = "Name- Bob\nEmail: bob@example.com"
    assert _extract_field(text, 'Name') == 'Bob'


# ── _extract_message ──────────────────────────────────────────────────────────

def test_extract_message_single_line():
    text = "Name: John\nEmail: john@example.com\nMessage: My bike needs fixing"
    assert _extract_message(text) == "My bike needs fixing"


def test_extract_message_multi_line():
    text = (
        "Name: John\n"
        "Email: john@example.com\n"
        "Message: Line one\n"
        "Line two\n"
        "Line three"
    )
    result = _extract_message(text)
    assert "Line one" in result
    assert "Line two" in result
    assert "Line three" in result


def test_extract_message_normalised_label():
    # After body_norm step, 'Message Body' is replaced with 'Message'
    text = "Name: Jane\nMessage: Need a service"
    assert _extract_message(text) == "Need a service"


def test_extract_message_stops_at_next_field():
    text = "Message: First line\nSecond line\nName: John"
    result = _extract_message(text)
    assert "First line" in result
    assert "John" not in result


def test_extract_message_returns_empty_when_absent():
    text = "Name: John\nEmail: john@example.com\nPhone: 0412345678"
    assert _extract_message(text) == ''


def test_extract_message_strips_footer():
    body = "Name: Jane\nMessage: Need a service\n--\nThis e-mail was sent from a contact form"
    stripped = _strip_footer(body)
    result = _extract_message(stripped)
    assert "contact form" not in result
    assert "Need a service" in result


# ── _detect_service_types ─────────────────────────────────────────────────────

def test_detect_service_general_default():
    assert _detect_service_types("I need a general service") == "General Service"


def test_detect_service_ebike_keyword():
    result = _detect_service_types("My ebike needs attention")
    assert "eBike Service" in result


def test_detect_service_electric_bike():
    result = _detect_service_types("I have an electric bike")
    assert "eBike Service" in result


def test_detect_service_ecargo():
    result = _detect_service_types("My e-cargo bike needs a tune")
    assert "eBike Service" in result


def test_detect_service_cargo_bike():
    result = _detect_service_types("I have a cargo bike that needs work")
    assert "Tribe/Cargo Bike Service" in result


def test_detect_service_three_or_more_count():
    result = _detect_service_types("3 bikes need a service")
    assert "3 or More Bikes" in result


def test_detect_service_fleet():
    result = _detect_service_types("We have a fleet of bikes")
    assert "3 or More Bikes" in result


def test_detect_service_case_insensitive():
    result = _detect_service_types("My EBIKE needs servicing")
    assert "eBike Service" in result


def test_detect_service_no_general_when_specific():
    result = _detect_service_types("My ebike needs new brakes")
    types = [t.strip() for t in result.split(',')]
    # eBike should be present
    assert any("eBike" in t for t in types)


# ── _parse_email ──────────────────────────────────────────────────────────────

STANDARD_BODY = (
    "Name: John Smith\n"
    "Email: john@example.com\n"
    "Phone: 0412 345 678\n"
    "Suburb: Fitzroy\n"
    "Service Type: General Service\n"
    "Message: I need my bike serviced." + CONTACT_FORM_FOOTER
)


def test_parse_email_name():
    msg = _make_msg(STANDARD_BODY, from_addr='john@example.com', subject='Booking')
    result = _parse_email(msg)
    assert result['name'] == 'John Smith'


def test_parse_email_email():
    msg = _make_msg(STANDARD_BODY, from_addr='john@example.com', subject='Booking')
    result = _parse_email(msg)
    assert result['email'] == 'john@example.com'


def test_parse_email_phone_normalised():
    msg = _make_msg(STANDARD_BODY, from_addr='john@example.com', subject='Booking')
    result = _parse_email(msg)
    assert result['phone'] == '0412345678'


def test_parse_email_suburb_title_case():
    msg = _make_msg(STANDARD_BODY, from_addr='john@example.com', subject='Booking')
    result = _parse_email(msg)
    assert result['suburb'] == 'Fitzroy'


def test_parse_email_service_type():
    msg = _make_msg(STANDARD_BODY, from_addr='john@example.com', subject='Booking')
    result = _parse_email(msg)
    assert 'General Service' in result['service_types']


def test_parse_email_subject():
    msg = _make_msg(STANDARD_BODY, from_addr='john@example.com', subject='New Booking')
    result = _parse_email(msg)
    assert result['subject'] == 'New Booking'


def test_parse_email_footer_stripped_from_message():
    msg = _make_msg(STANDARD_BODY, from_addr='john@example.com', subject='Booking')
    result = _parse_email(msg)
    assert 'contact form' not in result['message']
    assert 'theflyingbike.com.au' not in result['message']


def test_parse_email_multiline_message():
    body = (
        "Name: Jane Doe\n"
        "Email: jane@example.com\n"
        "Phone: 0498765432\n"
        "Suburb: Carlton\n"
        "Service Type: eBike Service\n"
        "Message Body: My e-bike needs attention.\n"
        "The battery isn't charging properly.\n"
        "Also the brakes need adjustment." + CONTACT_FORM_FOOTER
    )
    msg = _make_msg(body, from_addr='jane@example.com', subject='eBike Booking')
    result = _parse_email(msg)
    assert "battery" in result['message']
    assert "brakes" in result['message']


def test_parse_email_name_falls_back_to_from_header():
    body = (
        "Email: bob@example.com\n"
        "Phone: 0412000000\n"
        "Suburb: Richmond\n"
        "Message: Quick service please."
    )
    msg = _make_msg(body, from_addr='bob@example.com',
                    subject='Enquiry', from_name='Bob Jones')
    result = _parse_email(msg)
    assert result['name'] == 'Bob Jones'


def test_parse_email_ebike_service_type():
    body = (
        "Name: Alice\n"
        "Email: alice@example.com\n"
        "Phone: 0400111222\n"
        "Suburb: Collingwood\n"
        "Service Type: eBike Service\n"
        "Message: My electric bike needs a full service." + CONTACT_FORM_FOOTER
    )
    msg = _make_msg(body, from_addr='alice@example.com', subject='eBike')
    result = _parse_email(msg)
    assert 'eBike Service' in result['service_types']
