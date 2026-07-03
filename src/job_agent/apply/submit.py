"""The final submit gate — the only place a real application is sent.

Two independent locks, both required to click submit:

  1. ``--submit`` on the command line (``submit_flag``), and
  2. an explicit in-session approval decision from the review gate.

Either lock missing → no submission (dry-run or skipped). One approval covers
exactly one submission; there is no batch path. On a real submit, a confirmation
screenshot is captured and the outcome is appended to a JSONL log.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from job_agent.apply.fields import FillPlan
from job_agent.apply.review import Decision, ReviewOutcome


@dataclass(frozen=True)
class SubmitResult:
    status: str          # "submitted" | "dry_run" | "skipped"
    reason: str
    job_label: str = ""
    screenshot: str | None = None
    submitted_at: str | None = None

    @property
    def did_submit(self) -> bool:
        return self.status == "submitted"


def submit_block_reason(decision: Decision, submit_flag: bool) -> str | None:
    """Pure gate. Return why a submit is blocked, or None if it may proceed."""
    if decision != Decision.APPROVE:
        return "not approved (you chose to skip this application)"
    if not submit_flag:
        return "dry-run: real submission requires the --submit flag (nothing was sent)"
    return None


#: Visible submit-button text (Ashby and other React forms render submit as a
#: plain <button> with text, no type attribute).
_SUBMIT_TEXT = re.compile(r"^\s*submit(\s+application)?\s*$", re.IGNORECASE)


def find_submit(page, submit_selector: str):
    """Locate the submit control: type-based selector, then visible-text fallback.

    Ashby-style React forms have no ``button[type='submit']`` — fall back to a
    button whose accessible text is "Submit" / "Submit Application"
    (case-insensitive) when the selector matches nothing.
    """
    by_type = page.locator(submit_selector)
    if by_type.count() > 0:
        return by_type.first
    by_text = page.get_by_role("button", name=_SUBMIT_TEXT)
    if by_text.count() > 0:
        return by_text.first
    return by_type.first  # nothing matched — clicking raises Playwright's usual error


def log_result(result: SubmitResult, path: str | Path) -> Path:
    """Append one JSONL record of the outcome (audit trail of every run)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "job": result.job_label,
        "status": result.status,
        "reason": result.reason,
        "submitted_at": result.submitted_at,
        "screenshot": result.screenshot,
    }
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return path


def run_submit(page, plan: FillPlan, outcome: ReviewOutcome, *, submit_flag: bool,
               submit_selector: str, screenshot_dir: Path, job_label: str,
               now: datetime | None = None) -> SubmitResult:
    """Click submit only if both locks are satisfied; otherwise no-op.

    ``page`` may be None in a pure dry-run/skip path (no browser interaction is
    performed unless we actually submit).
    """
    screenshot_dir = Path(screenshot_dir)
    reason = submit_block_reason(outcome.decision, submit_flag)
    if reason is not None:
        status = "dry_run" if outcome.decision == Decision.APPROVE else "skipped"
        return SubmitResult(status=status, reason=reason, job_label=job_label)

    # Both locks satisfied — this is a real submission.
    stamp = (now or datetime.now(timezone.utc))
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    shot = screenshot_dir / f"confirmation_{stamp:%Y%m%d_%H%M%S}.png"
    find_submit(page, submit_selector).click()
    page.wait_for_timeout(1500)   # let the confirmation render
    page.screenshot(path=str(shot))
    return SubmitResult(
        status="submitted",
        reason="approved + --submit; submit clicked",
        job_label=job_label,
        screenshot=str(shot),
        submitted_at=stamp.isoformat(),
    )
