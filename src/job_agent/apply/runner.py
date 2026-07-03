"""Orchestrate one assisted application, end to end.

The flow, in order (one job per invocation — never a batch):

    open visible browser → goto(apply_url)
      → detect login/captcha/account blockers → PAUSE for the human, resume
      → read + classify the form
      → build the fill plan (answer bank + resume), fill the visible form
      → REVIEW: show every value, wait for approve / edit / skip
      → SUBMIT only if approved AND --submit; screenshot + log

Every safety rule is enforced here structurally: submission is gated by
``run_submit`` (needs both locks), and any blocker or missing required field
pauses for the human rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from job_agent.apply.answer_bank import AnswerBank, Contact
from job_agent.apply.browser import open_browser
from job_agent.apply.fields import FillPlan
from job_agent.apply.filler import apply_plan, build_fill_plan
from job_agent.apply.form_reader import read_form
from job_agent.apply.handoff import detect_blockers, pause_and_resume
from job_agent.apply.prompt_io import PromptIO
from job_agent.apply.review import Decision, request_approval
from job_agent.apply.submit import SubmitResult, log_result, run_submit

_SUBMIT_SELECTOR = "button[type='submit'], input[type='submit'], button#submit"


@dataclass(frozen=True)
class ApplyConfig:
    apply_url: str
    bank: AnswerBank
    contact: Contact
    resume_path: Path | None = None
    submit_flag: bool = False
    headless: bool = False
    out_dir: Path = field(default_factory=lambda: Path("data/apply"))
    job_label: str = ""
    auto_approve: bool = False       # demo only
    submit_selector: str = _SUBMIT_SELECTOR


def _clear_blockers(page, io: PromptIO) -> bool:
    """Pause for each detected login/captcha/account blocker. False if aborted."""
    for blocker in detect_blockers(page.inner_text("body")):
        ok = pause_and_resume(
            blocker, io,
            resume_check=lambda: not detect_blockers(page.inner_text("body")),
        )
        if not ok:
            return False
    return True


def run_apply(cfg: ApplyConfig, io: PromptIO | None = None) -> SubmitResult:
    """Run the full assisted-apply flow for a single job."""
    io = io or PromptIO()
    out_dir = Path(cfg.out_dir)
    log_path = out_dir / "apply_log.jsonl"

    with open_browser(headless=cfg.headless) as page:
        io.write(f"→ Opening application: {cfg.apply_url}")
        page.goto(cfg.apply_url, wait_until="load")

        if not _clear_blockers(page, io):
            result = SubmitResult("skipped", "aborted at a login/captcha pause", cfg.job_label)
            log_result(result, log_path)
            return result

        fields = read_form(page)
        io.write(f"→ Read {len(fields)} form field(s).")
        plan = build_fill_plan(fields, cfg.bank, cfg.contact, cfg.resume_path)
        apply_plan(page, plan)

        outcome = request_approval(plan, io, auto_approve=cfg.auto_approve)
        if outcome.decision == Decision.APPROVE:
            apply_plan(page, outcome.plan)   # sync any edited values before submit

        result = run_submit(
            page, outcome.plan, outcome,
            submit_flag=cfg.submit_flag,
            submit_selector=cfg.submit_selector,
            screenshot_dir=out_dir,
            job_label=cfg.job_label,
        )

    log_result(result, log_path)
    return result
