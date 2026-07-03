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


# --- grouping: same-name radios/checkboxes are ONE field ---------------------

class _FakePage:
    """Stands in for a Playwright page: returns a canned scan result."""

    def __init__(self, raw):
        self._raw = raw

    def evaluate(self, _js):
        return self._raw


def _radio(name, label, *, required=False, group_label="", id_=""):
    return {"tag": "input", "type": "radio", "name": name, "label": label,
            "groupLabel": group_label, "required": required, "options": [],
            "selector": f"#{id_}" if id_ else f'input[name="{name}"]'}


def test_same_name_radios_group_into_one_field_with_options():
    from job_agent.apply.form_reader import read_form
    page = _FakePage([
        _radio("country", "Australia", required=True, group_label="Country", id_="c1"),
        _radio("country", "Belgium", required=True, group_label="Country", id_="c2"),
        _radio("country", "United States", required=True, group_label="Country", id_="c3"),
    ])
    fields = read_form(page)
    assert len(fields) == 1                       # ONE field, not 3 "required" ones
    f = fields[0]
    assert f.label == "Country"                   # the fieldset legend
    assert f.options == ("Australia", "Belgium", "United States")
    assert f.required is True
    assert f.selector == 'input[name="country"]'
    assert f.field_type == FieldType.RADIO


def test_group_without_legend_falls_back_to_the_shared_name():
    from job_agent.apply.form_reader import read_form
    page = _FakePage([
        _radio("visa_status", "Yes", id_="v1"),
        _radio("visa_status", "No", id_="v2"),
    ])
    (f,) = read_form(page)
    assert f.label == "visa_status"
    assert f.options == ("Yes", "No")
    assert f.required is False                    # no member was required


def test_singles_and_groups_keep_document_order():
    from job_agent.apply.form_reader import read_form
    text = {"tag": "input", "type": "text", "name": "email", "label": "Email",
            "groupLabel": "", "required": True, "options": [], "selector": "#email"}
    page = _FakePage([
        text,
        _radio("country", "United States", group_label="Country", id_="c1"),
        _radio("country", "Canada", group_label="Country", id_="c2"),
    ])
    fields = read_form(page)
    assert [f.label for f in fields] == ["Email", "Country"]
