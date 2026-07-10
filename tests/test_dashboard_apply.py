"""Interactive apply sessions: the CLI's review gate, moved into the web UI.

Safety properties pinned here:
  * the browser opens VISIBLE (headless=False) — the human can act in it;
  * consent/legal fields are never auto-filled and never auto-submitted;
  * submit happens ONLY on the explicit submit() call, and is refused while a
    required field is unfilled — exactly the CLI's approval gate;
  * every outcome lands in the tracker (submitted / paused).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from job_agent.apply.answer_bank import AnswerBank, Contact
from job_agent.apply.tracker import load_applications
from job_agent.dashboard.app import create_app
from job_agent.dashboard.apply_session import ApplySession, SubmitBlocked

BANK = AnswerBank.model_validate({"authorized_us": True, "requires_sponsorship": False})
CONTACT = Contact(name="Jordan Rivers", email="j@x.com", phone="1")
RECORD = {"id": "j1", "title": "ML Engineer", "company": "Plaid",
          "apply_url": "http://apply.example/j1", "url": "http://apply.example/j1",
          "source": "ashby", "description": ""}

_SCAN = [
    {"tag": "input", "type": "email", "name": "email", "label": "Email",
     "groupLabel": "", "required": True, "maxlength": None, "options": [],
     "selector": "#email"},
    {"tag": "textarea", "type": "", "name": "why",
     "label": "Why do you want to work here?", "groupLabel": "", "required": True,
     "maxlength": None, "options": [], "selector": "#why"},
    {"tag": "input", "type": "checkbox", "name": "", "label":
     "I consent to the privacy policy", "groupLabel": "", "required": True,
     "maxlength": None, "options": [], "selector": "#consent"},
]


class _Loc:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def fill(self, value):
        self.page.calls.append(("fill", self.selector, value))

    def check(self):
        self.page.calls.append(("check", self.selector, True))

    def click(self, timeout=None):
        self.page.calls.append(("click", self.selector, None))

    def set_input_files(self, path):
        self.page.calls.append(("upload", self.selector, path))

    def get_by_role(self, role, name=None, exact=False):
        return _Loc(self.page, f"{self.selector}::{role}={name}")


class _Page:
    def __init__(self, scan=None):
        self.calls = []
        self.scan = _SCAN if scan is None else scan

    def goto(self, url, wait_until=None):
        self.calls.append(("goto", url, None))

    def inner_text(self, sel):
        return ""

    def evaluate(self, js):
        return self.scan

    def locator(self, selector):
        return _Loc(self, selector)

    def get_by_label(self, label):
        return _Loc(self, f"label:{label}")

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path):
        from pathlib import Path
        Path(path).write_bytes(b"png")


def _session(tmp_path, page=None, record=RECORD, **kw):
    page = page or _Page()
    seen = {}

    @contextmanager
    def factory(headless=False, **_):
        seen["headless"] = headless
        seen["closed"] = False
        try:
            yield page
        finally:
            seen["closed"] = True

    session = ApplySession(
        record=record, bank=BANK, contact=CONTACT, resume_path=None, drafter=None,
        tracker_path=tmp_path / "applications.json", out_dir=tmp_path / "apply",
        browser_factory=factory, **kw)
    return session, page, seen


# --- navigation: the record's stored apply_url, VERBATIM ------------------------------

# Real sr-search record shape: url is the posting page, apply_url carries the
# ?oga=true query that routes to the per-job apply flow. Regression pin for the
# "apply opened the SmartRecruiters company page" report: navigation must start
# at the stored apply_url byte-for-byte — never a company/board/template URL.
SR_RECORD = {
    "id": "744000136873684",
    "title": "Senior Machine Learning Engineer",
    "company": "Ginas Tech Jobs",
    "url": ("https://jobs.smartrecruiters.com/GinasTechJobs/"
            "744000136873684-senior-machine-learning-engineer"),
    "apply_url": ("https://jobs.smartrecruiters.com/GinasTechJobs/"
                  "744000136873684-senior-machine-learning-engineer?oga=true"),
    "source": "sr-search", "description": "",
}


def test_sr_search_apply_navigates_to_the_stored_apply_url_verbatim(tmp_path):
    session, page, _ = _session(tmp_path, record=SR_RECORD)
    session.start()
    gotos = [(kind, url) for kind, url, _ in page.calls if kind == "goto"]
    assert gotos == [("goto", SR_RECORD["apply_url"])]
    session.cancel()


def test_resolve_apply_url_prefers_apply_url_then_url_verbatim():
    from job_agent.store import resolve_apply_url

    assert resolve_apply_url(SR_RECORD) == SR_RECORD["apply_url"]
    assert resolve_apply_url({"url": "http://x/job?a=1"}) == "http://x/job?a=1"
    assert resolve_apply_url({"apply_url": "", "url": "http://x"}) == "http://x"
    assert resolve_apply_url({}) is None


def test_apply_navigation_falls_back_to_url_when_apply_url_is_missing(tmp_path):
    record = {**SR_RECORD}
    del record["apply_url"]
    session, page, _ = _session(tmp_path, record=record)
    session.start()
    assert ("goto", record["url"], None) in page.calls
    session.cancel()


# --- start: visible browser, plan built, consent untouched ---------------------------

def test_start_opens_a_visible_browser_and_fills_the_safe_fields(tmp_path):
    session, page, seen = _session(tmp_path)
    state = session.start()
    assert seen["headless"] is False                       # VISIBLE, always
    assert state["form_readable"] is True
    by_label = {p["label"]: p for p in state["planned"]}
    assert by_label["Email"]["tag"] == "[FROM ANSWER_BANK]"
    assert ("fill", "#email", "j@x.com") in page.calls     # filled on the real page
    consent = next(u for u in state["unfilled"] if "consent" in u["label"].lower())
    assert consent["tag"] == "[PAUSED: consent]"
    assert state["missing_required"] == 2                  # consent + why
    assert not any(sel == "#consent" for _, sel, _ in page.calls)   # NEVER touched
    session.cancel()


def test_start_records_a_paused_attempt_in_the_tracker(tmp_path):
    session, _, _ = _session(tmp_path)
    session.start()
    (rec,) = load_applications(tmp_path / "applications.json")
    assert rec.status == "paused" and rec.company == "Plaid"
    session.cancel()


def test_unreadable_form_is_reported_clearly_not_an_empty_plan(tmp_path):
    session, page, _ = _session(tmp_path, page=_Page(scan=[]))
    state = session.start()
    assert state["form_readable"] is False
    assert "could not read" in state["message"].lower()
    assert state["planned"] == [] and state["unfilled"] == []
    # the session stays alive: the human can act in the visible window, then rescan
    page.scan = _SCAN
    state = session.rescan()
    assert state["form_readable"] is True
    assert any(p["label"] == "Email" for p in state["planned"])
    session.cancel()


# --- edit: the CLI's `edit`, over HTTP ------------------------------------------------

def test_edit_updates_the_plan_and_the_live_page(tmp_path):
    session, page, _ = _session(tmp_path)
    session.start()
    state = session.edit("#why", "Because I build ML platforms.")
    row = next(p for p in state["planned"] if p["label"].startswith("Why"))
    assert row["value"] == "Because I build ML platforms."
    assert row["tag"] == "[MANUAL]"
    assert ("fill", "#why", "Because I build ML platforms.") in page.calls
    assert state["missing_required"] == 1                  # consent still blocks
    session.cancel()


def test_mark_done_resolves_the_field_without_touching_the_page(tmp_path):
    session, page, _ = _session(tmp_path)
    session.start()
    state = session.edit("#consent", "(completed in the browser)", mark_done=True)
    assert not any(sel == "#consent" for _, sel, _ in page.calls)   # page untouched
    row = next(p for p in state["planned"] if "consent" in p["label"].lower())
    assert row["value"] == "(completed in the browser)"
    assert all(u["label"] != row["label"] for u in state["unfilled"])
    session.cancel()


# --- submit: explicit, gated exactly like the CLI ------------------------------------

def test_submit_is_refused_while_required_fields_are_missing(tmp_path):
    session, page, _ = _session(tmp_path)
    session.start()
    with pytest.raises(SubmitBlocked):
        session.submit()
    assert not any(kind == "click" for kind, _, _ in page.calls)    # nothing clicked
    session.cancel()


def test_submit_clicks_only_after_explicit_call_and_tracks_submitted(tmp_path):
    session, page, seen = _session(tmp_path)
    session.start()
    session.edit("#why", "Because I build ML platforms.")
    session.edit("#consent", "(completed in the browser)", mark_done=True)
    assert not any(kind == "click" for kind, _, _ in page.calls)    # not yet
    result = session.submit()
    assert result["result"]["status"] == "submitted"
    clicks = [sel for kind, sel, _ in page.calls if kind == "click"]
    assert len(clicks) == 1 and "submit" in clicks[0].lower()
    (rec,) = load_applications(tmp_path / "applications.json")
    assert rec.status == "submitted"
    assert seen["closed"] is True                          # browser torn down


def test_cancel_marks_the_attempt_paused_and_closes_the_browser(tmp_path):
    session, _, seen = _session(tmp_path)
    session.start()
    session.cancel()
    (rec,) = load_applications(tmp_path / "applications.json")
    assert rec.status == "paused"
    assert seen["closed"] is True


# --- endpoints -------------------------------------------------------------------------

def _app(tmp_path, page=None):
    import json
    (tmp_path / "last_search.json").write_text(json.dumps(
        {"generated_at": "2026-07-09T00:00:00+00:00",
         "jobs": {"j1": {**RECORD, "board": "plaid", "score": 80,
                         "verdict": "strong", "reasons": []}}}))
    holder = {}

    def session_factory(job_id):
        session, pg, seen = _session(tmp_path, page=page)
        holder["page"], holder["seen"] = pg, seen
        return session
    return create_app(data_dir=tmp_path, apply_session_factory=session_factory), holder


def test_apply_endpoints_run_the_full_gated_flow(tmp_path):
    app, holder = _app(tmp_path)
    c = TestClient(app)

    state = c.post("/api/apply/start", json={"job_id": "j1"}).json()
    sid = state["session_id"]
    assert state["form_readable"] is True
    assert any(u["tag"] == "[PAUSED: consent]" for u in state["unfilled"])

    # submit refused while required fields missing -> 409, nothing clicked
    resp = c.post("/api/apply/submit", json={"session_id": sid})
    assert resp.status_code == 409
    assert not any(k == "click" for k, _, _ in holder["page"].calls)

    c.post("/api/apply/edit", json={"session_id": sid, "selector": "#why",
                                    "value": "Because I build ML platforms."})
    c.post("/api/apply/edit", json={"session_id": sid, "selector": "#consent",
                                    "value": "(completed in the browser)",
                                    "mark_done": True})
    resp = c.post("/api/apply/submit", json={"session_id": sid})
    assert resp.status_code == 200
    assert resp.json()["result"]["status"] == "submitted"
    # session is gone afterwards
    assert c.post("/api/apply/submit", json={"session_id": sid}).status_code == 404


def test_apply_start_404s_for_unknown_job(tmp_path):
    app, _ = _app(tmp_path)
    resp = TestClient(app).post("/api/apply/start", json={"job_id": "nope"})
    assert resp.status_code == 404
