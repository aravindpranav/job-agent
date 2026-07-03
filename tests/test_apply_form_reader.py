"""Pure field classification (no browser)."""

from __future__ import annotations

from job_agent.apply.fields import FieldType
from job_agent.apply.form_reader import classify_control


def test_textarea_is_textarea():
    assert classify_control("textarea", "", "Why us?") == FieldType.TEXTAREA


def test_file_input_is_file():
    assert classify_control("input", "file", "Resume") == FieldType.FILE


def test_password_is_credential_never_filled():
    assert classify_control("input", "password", "Password") == FieldType.CREDENTIAL


def test_plain_text_is_text():
    assert classify_control("input", "text", "Full name") == FieldType.TEXT
    assert classify_control("input", "email", "Email") == FieldType.TEXT


def test_select_is_select():
    assert classify_control("select", "", "Work arrangement", has_options=True) == FieldType.SELECT


def test_eeo_select_is_eeo():
    assert classify_control("select", "", "Gender") == FieldType.EEO
    assert classify_control("select", "", "Veteran status") == FieldType.EEO
    assert classify_control("input", "text", "Race / Ethnicity") == FieldType.EEO


def test_checkbox_and_radio():
    assert classify_control("input", "checkbox", "Agree") == FieldType.CHECKBOX
    assert classify_control("input", "radio", "Option") == FieldType.RADIO
