"""Label resolution for Lever "cards" custom questions (extension scan.js).

The fixture reproduces, verbatim, the DOM captured read-only from the live
incident form (jobs.lever.co/employ/..., 2026-07-15): question text lives in
<div class="application-label"> inside li.application-question — never in a
<label> element — and radio/checkbox options each sit in their OWN <label>.
Ancestor chains match the capture exactly:
    radio/checkbox: label > li > ul > div.application-field > div > li.application-question
    text:           div.application-field > div > li.application-question
No textarea card existed on the captured form, so none is fixtured here.
"""

from __future__ import annotations

import pytest

playwright = pytest.importorskip("playwright.sync_api")

SCAN_JS = "/Users/aravindpranav/job-agent/extension/content/scan.js"

# Question texts and option texts below are the capture, verbatim.
FIXTURE = """
<ul>
  <li class="application-question custom-question">
    <div>
      <div class="application-label full-width text">Please specify your target base salary and variable pay structure (bonus or commission). ✱</div>
      <div class="application-field full-width required-field">
        <input type="text" name="cards[3d54fbc9-6793-40d6-aac7-764ccb01e57c][field0]">
      </div>
    </div>
  </li>
  <li class="application-question custom-question">
    <div>
      <div class="application-label full-width multiple-choice">Have you previously been employed by Employ? ✱</div>
      <div class="application-field full-width required-field">
        <ul>
          <li><label><input type="radio" name="cards[87bb9c07-e6bb-4166-a29d-67bce107a068][field0]" value="Yes"> Yes</label></li>
          <li><label><input type="radio" name="cards[87bb9c07-e6bb-4166-a29d-67bce107a068][field0]" value="No"> No</label></li>
        </ul>
      </div>
    </div>
  </li>
  <li class="application-question custom-question">
    <div>
      <div class="application-label full-width multiple-select">I identify my ethnicity as (select all that apply) ✱</div>
      <div class="application-field full-width required-field">
        <ul>
          <li><label><input type="checkbox" name="cards[4908d9ac-9e5e-44f8-970e-6f1ede8f5374][field4]" value="Asian"> Asian</label></li>
          <li><label><input type="checkbox" name="cards[4908d9ac-9e5e-44f8-970e-6f1ede8f5374][field4]" value="I prefer to not answer"> I prefer to not answer</label></li>
        </ul>
      </div>
    </div>
  </li>
  <li class="application-question custom-question">
    <div>
      <div class="application-label full-width multiple-choice">What gender do you identify as? ✱</div>
      <div class="application-field full-width required-field">
        <ul>
          <li><label><input type="radio" name="cards[4908d9ac-9e5e-44f8-970e-6f1ede8f5374][field3]" value="Male"> Male</label></li>
          <li><label><input type="radio" name="cards[4908d9ac-9e5e-44f8-970e-6f1ede8f5374][field3]" value="Female"> Female</label></li>
        </ul>
      </div>
    </div>
  </li>
</ul>
<!-- a control with NO li.application-question ancestor and no label at all,
     nested at the capture's real depth (live chains are 5+ levels, so the
     4-level ancestor walk never reaches document scope): resolution must
     yield nothing, so it pauses/surfaces exactly as today -->
<div class="section page-centered application-form"><div><div><div><div>
  <input type="text" name="cards[deadbeef-0000][field0]">
</div></div></div></div></div>
"""


@pytest.fixture(scope="module")
def scanned():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(FIXTURE)
        page.add_script_tag(path=SCAN_JS)
        raw = page.evaluate("() => JA_scanForm(document)")
        browser.close()
    return raw


def _rows(scanned, name):
    return [r for r in scanned if r["name"] == name]


def test_text_card_resolves_the_visible_question(scanned):
    (row,) = _rows(scanned, "cards[3d54fbc9-6793-40d6-aac7-764ccb01e57c][field0]")
    assert row["label"].startswith(
        "Please specify your target base salary and variable pay structure "
        "(bonus or commission).")


def test_radio_group_resolves_its_question_not_the_option_label(scanned):
    rows = _rows(scanned, "cards[87bb9c07-e6bb-4166-a29d-67bce107a068][field0]")
    assert len(rows) == 2
    for r in rows:
        assert r["groupLabel"].startswith("Have you previously been employed by Employ?")
    assert sorted(r["label"] for r in rows) == ["No", "Yes"]   # options untouched


def test_checkbox_group_resolves_its_question(scanned):
    rows = _rows(scanned, "cards[4908d9ac-9e5e-44f8-970e-6f1ede8f5374][field4]")
    assert len(rows) == 2
    for r in rows:
        assert r["groupLabel"].startswith(
            "I identify my ethnicity as (select all that apply)")
    assert "Asian" in [r["label"] for r in rows]               # options untouched


def test_control_without_the_card_ancestor_still_surfaces_unresolved(scanned):
    (row,) = _rows(scanned, "cards[deadbeef-0000][field0]")
    assert row["label"] == ""     # nothing guessed — pauses/surfaces as before


def test_eeo_group_label_carries_no_option_text(scanned):
    # regression guard for a2dda3e: the question div must not smuggle option
    # texts back into the EEO routing haystack
    rows = _rows(scanned, "cards[4908d9ac-9e5e-44f8-970e-6f1ede8f5374][field3]")
    for r in rows:
        assert r["groupLabel"].startswith("What gender do you identify as?")
        assert "Male" not in r["groupLabel"]
        assert "Female" not in r["groupLabel"]
