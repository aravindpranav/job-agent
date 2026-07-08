"""Regression tests against the REAL saved Ashby DOM (Plaid application form).

The synthetic toggle mock passed while the live form crashed: the scan stamped
``data-ja-toggle`` onto a React-managed element, Ashby re-rendered the form
after the resume upload, the stamp was wiped, and the click timed out. These
tests run the actual scan + click path against markup captured from
https://jobs.ashbyhq.com/plaid (tests/fixtures/ashby_plaid_form.html), so the
selector contract is validated against what Ashby really renders: the toggle
must anchor to Ashby's own re-render-stable ``data-field-path`` attribute.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.apply.answer_bank import AnswerBank, Contact
from job_agent.apply.fields import FieldType
from job_agent.apply.filler import apply_plan, build_fill_plan
from job_agent.apply.form_reader import read_form

FIXTURE = Path(__file__).parent / "fixtures" / "ashby_plaid_form.html"

BANK = AnswerBank.model_validate({"authorized_us": True, "requires_sponsorship": False})
CONTACT = Contact(name="Jordan Rivers", email="jordan@example.com", phone="+1 555 010 0100")


@pytest.fixture(scope="module")
def ashby_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(FIXTURE.read_text())
        yield page
        browser.close()


def _toggles(page):
    return [f for f in read_form(page) if f.field_type == FieldType.TOGGLE]


def test_real_ashby_yes_no_pairs_are_read_as_toggles(ashby_page):
    toggles = _toggles(ashby_page)
    labels = " | ".join(t.label for t in toggles)
    assert len(toggles) == 3
    assert "sponsorship" in labels
    assert "previously employed by Plaid" in labels


def test_toggle_selectors_anchor_to_ashbys_stable_field_path(ashby_page):
    # THE regression: a stamped attribute dies with Ashby's React re-render;
    # data-field-path is Ashby's own key and survives it.
    for t in _toggles(ashby_page):
        assert t.selector.startswith('[data-field-path="'), t.selector


def test_sponsorship_and_previously_employed_resolve_to_no(ashby_page):
    plan = build_fill_plan(_toggles(ashby_page), BANK, CONTACT, None)
    by_label = {p.field.label: p for p in plan.planned}
    spon = next(p for label, p in by_label.items() if "sponsorship" in label)
    prev = next(p for label, p in by_label.items() if "previously employed" in label)
    assert spon.value == "No" and spon.source == "answer_bank.requires_sponsorship"
    assert prev.value == "No" and prev.source == "answer_bank.previously_employed_here"


def test_the_hybrid_office_question_pauses_for_the_human(ashby_page):
    plan = build_fill_plan(_toggles(ashby_page), BANK, CONTACT, None)
    paused = [u for u in plan.unfilled if "office" in u.field.label]
    assert len(paused) == 1                     # a human decides office attendance


def test_apply_plan_clicks_the_no_buttons_on_the_real_dom(ashby_page):
    plan = build_fill_plan(_toggles(ashby_page), BANK, CONTACT, None)
    failed = apply_plan(ashby_page, plan)
    assert failed == ()                         # both No buttons found and clicked
