"""The submit gate: needs BOTH approval and --submit; logs every outcome."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from job_agent.apply.fields import FieldType, FillPlan, FormField, PlannedFill
from job_agent.apply.review import Decision, ReviewOutcome
from job_agent.apply.submit import (
    SubmitResult,
    log_result,
    run_submit,
    submit_block_reason,
)

PLAN = FillPlan(planned=(PlannedFill(
    FormField("#email", FieldType.TEXT, "Email"), "a@b.com", "career_facts.email"),))
APPROVED = ReviewOutcome(Decision.APPROVE, PLAN)
SKIPPED = ReviewOutcome(Decision.SKIP, PLAN)


# --- the pure gate ----------------------------------------------------------

def test_gate_blocks_without_submit_flag():
    assert submit_block_reason(Decision.APPROVE, submit_flag=False) is not None


def test_gate_blocks_without_approval():
    assert submit_block_reason(Decision.SKIP, submit_flag=True) is not None


def test_gate_blocks_when_neither():
    assert submit_block_reason(Decision.SKIP, submit_flag=False) is not None


def test_gate_allows_only_with_both():
    assert submit_block_reason(Decision.APPROVE, submit_flag=True) is None


# --- run_submit refuses to touch the page unless both locks are satisfied ----

class _FakeLocator:
    def __init__(self, page, kind, n):
        self._page, self._kind, self._n = page, kind, n

    def count(self):
        return self._n

    @property
    def first(self):
        return self

    def click(self):
        self._page.clicked = True
        self._page.clicked_via = self._kind


class _FakePage:
    """type_matches / text_matches control which lookup finds the button."""

    def __init__(self, type_matches=1, text_matches=0):
        self.clicked = False
        self.clicked_via = None
        self._type, self._text = type_matches, text_matches
        self.text_queries = []

    def locator(self, selector):
        return _FakeLocator(self, "type", self._type)

    def get_by_role(self, role, name=None):
        self.text_queries.append((role, name))
        return _FakeLocator(self, "text", self._text)

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path):
        Path(path).write_bytes(b"%PDF-fake-png")


def test_dry_run_does_not_click_submit(tmp_path):
    page = _FakePage()
    result = run_submit(page, PLAN, APPROVED, submit_flag=False,
                        submit_selector="#submit", screenshot_dir=tmp_path, job_label="J")
    assert result.status == "dry_run"
    assert page.clicked is False


def test_skip_does_not_click_submit(tmp_path):
    page = _FakePage()
    result = run_submit(page, PLAN, SKIPPED, submit_flag=True,
                        submit_selector="#submit", screenshot_dir=tmp_path, job_label="J")
    assert result.status == "skipped"
    assert page.clicked is False


def test_real_submit_clicks_and_screenshots(tmp_path):
    page = _FakePage()
    when = datetime(2025, 7, 2, 12, 0, tzinfo=timezone.utc)
    result = run_submit(page, PLAN, APPROVED, submit_flag=True,
                        submit_selector="#submit", screenshot_dir=tmp_path,
                        job_label="Demo Co — Data Engineer", now=when)
    assert result.status == "submitted"
    assert page.clicked is True
    assert result.screenshot and Path(result.screenshot).exists()
    assert result.submitted_at == when.isoformat()


# --- submit-button lookup: type selector, then visible-text fallback ----------

def test_submit_prefers_the_type_selector_when_it_matches(tmp_path):
    page = _FakePage(type_matches=1, text_matches=1)
    run_submit(page, PLAN, APPROVED, submit_flag=True,
               submit_selector="#submit", screenshot_dir=tmp_path, job_label="J")
    assert page.clicked_via == "type"
    assert page.text_queries == []          # fallback never consulted


def test_submit_falls_back_to_button_text_for_react_forms(tmp_path):
    # Ashby-style: no button[type=submit]; a plain button reads "Submit Application".
    page = _FakePage(type_matches=0, text_matches=1)
    result = run_submit(page, PLAN, APPROVED, submit_flag=True,
                        submit_selector="button[type='submit']",
                        screenshot_dir=tmp_path, job_label="J")
    assert result.status == "submitted"
    assert page.clicked_via == "text"
    role, name = page.text_queries[0]
    assert role == "button"
    # the pattern accepts "Submit" and "Submit Application", case-insensitively,
    # but not e.g. "Submit feedback"
    assert name.match("Submit Application") and name.match("SUBMIT") \
        and name.match("submit")
    assert not name.match("Submit feedback")


def test_text_fallback_never_runs_on_dry_run_or_skip(tmp_path):
    page = _FakePage(type_matches=0, text_matches=1)
    run_submit(page, PLAN, APPROVED, submit_flag=False,     # dry run
               submit_selector="#s", screenshot_dir=tmp_path, job_label="J")
    run_submit(page, PLAN, SKIPPED, submit_flag=True,       # not approved
               submit_selector="#s", screenshot_dir=tmp_path, job_label="J")
    assert page.clicked is False and page.text_queries == []


# --- logging ----------------------------------------------------------------

def test_log_result_appends_jsonl(tmp_path):
    log = tmp_path / "apply_log.jsonl"
    log_result(SubmitResult("dry_run", "preview", "Job A"), log)
    log_result(SubmitResult("submitted", "done", "Job B", submitted_at="t"), log)
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["status"] == "dry_run"
    assert json.loads(lines[1])["job"] == "Job B"
