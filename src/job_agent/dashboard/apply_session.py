"""One live assisted-apply session: the CLI's review gate, served over HTTP.

An :class:`ApplySession` owns a VISIBLE browser (headless is never used here —
the human must be able to log in, solve captchas, and answer consent boxes in
the window) and mirrors the CLI runner's safety structure exactly:

* fields are filled with the same ``build_fill_plan`` → ``apply_plan`` path;
  a failed fill is demoted back to unfilled, never fatal;
* consent/legal/EEO fields are never auto-filled here — ``mark_done`` only
  records that the HUMAN completed one in the browser, the page is untouched;
* :meth:`submit` is the ONLY path to a real submission, refuses while any
  required field is unfilled (the CLI's approval rule), and routes through
  ``run_submit`` — the same two-lock gate the terminal flow uses;
* every outcome lands in the tracker (submitted / paused).

Playwright's sync API is bound to the thread that created it, while FastAPI
serves each request from a pool thread — so the session runs a dedicated
worker thread that owns the browser, and public methods marshal calls into it.
"""

from __future__ import annotations

import threading
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue

from job_agent.apply.browser import open_browser
from job_agent.apply.fields import FillPlan, PlannedFill
from job_agent.apply.filler import apply_plan, build_fill_plan
from job_agent.apply.form_reader import read_form
from job_agent.apply.review import Decision, ReviewOutcome
from job_agent.apply.runner import _SUBMIT_SELECTOR, _TRACK_STATUS
from job_agent.apply.screening import apply_drafts
from job_agent.apply.submit import log_result, run_submit
from job_agent.apply.tracker import ApplicationRecord, record_attempt, update_status
from job_agent.dashboard.service import serialize_plan
from job_agent.store import resolve_apply_url

_CALL_TIMEOUT_S = 600   # a single step may include slow page loads / an LLM draft

_UNREADABLE = (
    "Could not read an application form on this page (0 fields found). Some "
    "sources gate their form behind a captcha, login, or an extra “Apply” "
    "click — do that in the browser window that just opened, then press "
    "“Re-read form”."
)


class SubmitBlocked(RuntimeError):
    """Submission refused — the review gate is not satisfied."""


class SessionClosed(RuntimeError):
    """The session's browser is already torn down."""


class ApplySession:
    """A live, human-gated apply flow bound to one visible browser window."""

    def __init__(self, *, record: dict, bank, contact, resume_path: Path | None,
                 drafter, tracker_path: Path, out_dir: Path,
                 browser_factory=None, submit_selector: str = _SUBMIT_SELECTOR):
        self._record = record
        self._bank, self._contact = bank, contact
        self._resume_path, self._drafter = resume_path, drafter
        self._tracker_path, self._out_dir = Path(tracker_path), Path(out_dir)
        self._browser_factory = browser_factory or open_browser
        self._submit_selector = submit_selector

        self._page = None
        self._plan = FillPlan()
        self._form_readable = False
        self._message = ""
        self._warnings: list[str] = []
        self._attempt_id = ""
        self._closed = False

        self._commands: Queue = Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"apply-{record.get('id', '?')}")
        self._thread.start()

    # -- public API (each call runs inside the worker thread) -------------------

    def start(self) -> dict:
        return self._invoke(self._start)

    def rescan(self) -> dict:
        return self._invoke(self._rescan)

    def edit(self, selector: str, value: str, mark_done: bool = False) -> dict:
        return self._invoke(self._edit, selector, value, mark_done)

    def submit(self) -> dict:
        return self._invoke(self._submit)

    def cancel(self) -> dict:
        return self._invoke(self._cancel)

    # -- worker-thread plumbing --------------------------------------------------

    def _invoke(self, fn, *args):
        if self._closed:
            raise SessionClosed("this apply session is already closed")
        box: Queue = Queue()
        self._commands.put((fn, args, box))
        ok, value = box.get(timeout=_CALL_TIMEOUT_S)
        if not ok:
            raise value
        return value

    def _loop(self) -> None:
        with ExitStack() as stack:
            while True:
                fn, args, box = self._commands.get()
                try:
                    box.put((True, fn(stack, *args)))
                except Exception as exc:  # marshaled to the caller, loop survives
                    box.put((False, exc))
                if self._closed:
                    return  # ExitStack tears the browser down

    # -- steps (worker thread only) -----------------------------------------------

    def _start(self, stack: ExitStack) -> dict:
        self._attempt_id = record_attempt(self._tracker_path, ApplicationRecord(
            company=self._record.get("company", "Unknown"),
            title=self._record.get("title", ""),
            job_id=str(self._record.get("id", "")),
            date=datetime.now(timezone.utc).isoformat(),
            source=self._record.get("source", ""),
            status="paused", reason="in dashboard review"))
        # VISIBLE by definition: the human acts in this window (captcha, consent).
        self._page = stack.enter_context(self._browser_factory(headless=False))
        self._page.goto(resolve_apply_url(self._record), wait_until="load")
        self._read_and_fill()
        return self._state()

    def _rescan(self, stack: ExitStack) -> dict:
        """Re-read the CURRENT page — used after the human clears a captcha or
        clicks through to the real form in the visible window."""
        self._read_and_fill()
        return self._state()

    def _read_and_fill(self) -> None:
        self._warnings = []
        fields = read_form(self._page)
        if not fields:
            self._plan, self._form_readable = FillPlan(), False
            src = self._record.get("source", "")
            self._message = _UNREADABLE + (
                f" (source: {src} — SmartRecruiters is known to captcha-gate "
                f"its apply flow)" if src == "sr-search" else "")
            return
        plan = build_fill_plan(fields, self._bank, self._contact, self._resume_path)
        if self._drafter is not None:
            plan = apply_drafts(plan, self._drafter)
        for pf in apply_plan(self._page, plan):
            plan = plan.demote(pf.field.selector,
                               "could not be filled on the live page — "
                               "complete it in the browser")
            self._warnings.append(f"could not fill: {pf.field.describe()}")
        self._plan, self._form_readable, self._message = plan, True, ""

    def _edit(self, stack: ExitStack, selector: str, value: str,
              mark_done: bool) -> dict:
        source = ("manual (completed in the browser)" if mark_done
                  else "manual (edit)")
        self._plan = self._plan.with_value(selector, value, source)
        if not mark_done:
            fill = next(p for p in self._plan.planned
                        if p.field.selector == selector)
            for failed in apply_plan(self._page, FillPlan(planned=(fill,))):
                self._warnings.append(
                    f"edit did not land on the page: {failed.field.describe()} — "
                    f"set it in the browser window")
        return self._state()

    def _submit(self, stack: ExitStack) -> dict:
        missing = self._plan.missing_required()
        if missing:
            names = ", ".join(u.field.describe() for u in missing[:4])
            raise SubmitBlocked(
                f"{len(missing)} required field(s) still unfilled ({names}) — "
                f"edit them or mark them done first")
        outcome = ReviewOutcome(Decision.APPROVE, self._plan)
        result = run_submit(
            self._page, self._plan, outcome, submit_flag=True,
            submit_selector=self._submit_selector, screenshot_dir=self._out_dir,
            job_label=f"{self._record.get('company', '?')} — "
                      f"{self._record.get('title', '?')}")
        log_result(result, self._out_dir / "apply_log.jsonl")
        update_status(self._tracker_path, self._attempt_id,
                      _TRACK_STATUS.get(result.status, "paused"), result.reason)
        self._closed = True
        return {"result": {"status": result.status, "reason": result.reason,
                           "screenshot": result.screenshot}, **self._state()}

    def _cancel(self, stack: ExitStack) -> dict:
        if self._attempt_id:
            update_status(self._tracker_path, self._attempt_id, "paused",
                          "review cancelled in the dashboard — nothing submitted")
        self._closed = True
        return {"result": {"status": "cancelled"}}

    def _state(self) -> dict:
        return {
            "job": {"id": self._record.get("id"),
                    "title": self._record.get("title"),
                    "company": self._record.get("company")},
            "form_readable": self._form_readable,
            "message": self._message,
            "warnings": list(self._warnings),
            **serialize_plan(self._plan),
        }
