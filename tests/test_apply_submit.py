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

class _FakePage:
    def __init__(self):
        self.clicked = False

    def locator(self, selector):
        page = self

        class _L:
            @property
            def first(self):
                return self

            def click(self_inner):
                page.clicked = True
        return _L()

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


# --- logging ----------------------------------------------------------------

def test_log_result_appends_jsonl(tmp_path):
    log = tmp_path / "apply_log.jsonl"
    log_result(SubmitResult("dry_run", "preview", "Job A"), log)
    log_result(SubmitResult("submitted", "done", "Job B", submitted_at="t"), log)
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["status"] == "dry_run"
    assert json.loads(lines[1])["job"] == "Job B"
