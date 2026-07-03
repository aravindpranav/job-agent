"""The human-review gate: show everything, then wait for approve / edit / skip.

Nothing is ever submitted from here — this stage only produces a *decision*.
Approval is refused while any required field is unfilled (you can't approve an
incomplete application), so the only ways past a missing-required form are to
``edit`` in a value or ``skip`` the application entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from job_agent.apply.fields import FillPlan
from job_agent.apply.prompt_io import PromptIO


class Decision(str, Enum):
    APPROVE = "approve"
    SKIP = "skip"


@dataclass(frozen=True)
class ReviewOutcome:
    """The decision plus the (possibly edited) plan it applies to."""

    decision: Decision
    plan: FillPlan


def render_review(plan: FillPlan, heading: str = "APPLICATION REVIEW") -> str:
    """A readable summary of EVERY value to be submitted, plus every gap."""
    lines = [f"=== {heading} ===", ""]
    lines.append(f"Will submit {len(plan.planned)} field(s):")
    for pf in plan.planned:
        req = " *required" if pf.field.required else ""
        lines.append(f"  • {pf.field.describe():<40} = {pf.value}")
        lines.append(f"      ↳ source: {pf.source}{req}")
    if plan.unfilled:
        lines += ["", f"Left EMPTY ({len(plan.unfilled)} — never guessed):"]
        for u in plan.unfilled:
            mark = "‼ REQUIRED" if u.field.required else "optional"
            lines.append(f"  • [{mark}] {u.field.describe()}")
            lines.append(f"      ↳ {u.reason}")
    missing = plan.missing_required()
    lines += [""]
    if missing:
        lines.append(f"⚠ {len(missing)} REQUIRED field(s) unfilled — cannot approve until "
                     f"filled (edit) or the application is skipped.")
    else:
        lines.append("All required fields are filled.")
    return "\n".join(lines)


def _apply_edit(plan: FillPlan, command: str, io: PromptIO) -> FillPlan:
    """Parse and apply an ``edit <selector>=<value>`` command; report errors."""
    body = command[len("edit"):].strip()
    if "=" not in body:
        io.write("  edit needs the form: edit <selector>=<value>")
        return plan
    selector, value = (s.strip() for s in body.split("=", 1))
    try:
        new_plan = plan.with_value(selector, value)
        io.write(f"  set {selector} = {value}")
        return new_plan
    except KeyError as exc:
        io.write(f"  {exc}")
        return plan


def request_approval(plan: FillPlan, io: PromptIO, *, auto_approve: bool = False) -> ReviewOutcome:
    """Show the review and loop until the human approves (if allowed) or skips.

    ``auto_approve`` is for demo mode: it prints the review and a simulated
    approval, but still refuses if a required field is missing.
    """
    io.write(render_review(plan))

    if auto_approve:
        if plan.missing_required():
            io.write("[demo] cannot auto-approve: required fields missing.")
            return ReviewOutcome(Decision.SKIP, plan)
        io.write("[demo] simulated approval: approve")
        return ReviewOutcome(Decision.APPROVE, plan)

    while True:
        choice = io.read("\nApprove and continue? [approve / edit <sel>=<val> / skip]: ").strip().lower()
        if choice in ("skip", "s", "no", "n"):
            return ReviewOutcome(Decision.SKIP, plan)
        if choice.startswith("edit"):
            plan = _apply_edit(plan, choice, io)
            io.write(render_review(plan))
            continue
        if choice in ("approve", "a", "yes", "y"):
            if plan.missing_required():
                io.write("Cannot approve: required field(s) still empty. Use 'edit' or 'skip'.")
                continue
            return ReviewOutcome(Decision.APPROVE, plan)
        io.write("Please type 'approve', 'edit <selector>=<value>', or 'skip'.")
