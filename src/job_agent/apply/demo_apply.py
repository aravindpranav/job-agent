"""`apply --demo`: run the whole assisted-apply flow against the local form.

Zero network, no real employer: the form is loaded over ``file://``, the answers
and resume are committed fakes, and "submission" is a client-side JS confirmation
on the local page. The review still pauses for a (simulated) approval, so the
end-to-end flow — read → fill → review → approve → submit → screenshot + log —
is exactly the real one, minus the real world.
"""

from __future__ import annotations

from pathlib import Path

from job_agent.apply.answer_bank import load_answer_bank, resolve_contact
from job_agent.apply.demo import DEMO_ANSWERS, DEMO_RESUME, FORM_HTML
from job_agent.apply.prompt_io import PromptIO
from job_agent.apply.runner import ApplyConfig, run_apply
from job_agent.apply.submit import SubmitResult
from job_agent.tailor.career_facts import load_career_facts

_DEMO_FACTS = (Path(__file__).resolve().parent.parent  # job_agent/
               / "tailor" / "demo" / "demo_career_facts.yaml")


def run_demo(io: PromptIO | None = None, *, out_dir: Path | None = None,
             headless: bool = True) -> SubmitResult:
    """Execute the demo application; returns the (real, local) SubmitResult."""
    facts = load_career_facts(_DEMO_FACTS)
    bank = load_answer_bank(DEMO_ANSWERS)
    contact = resolve_contact(facts, bank)

    cfg = ApplyConfig(
        apply_url=FORM_HTML.resolve().as_uri(),
        bank=bank,
        contact=contact,
        resume_path=DEMO_RESUME,
        submit_flag=True,          # safe: the local demo form submits client-side only
        headless=headless,
        out_dir=out_dir or Path("data/apply/demo"),
        job_label="Demo Co — Data Engineer (LOCAL DEMO)",
        auto_approve=True,         # simulated approval — no real employer involved
    )
    return run_apply(cfg, io=io)
