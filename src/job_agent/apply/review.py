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


def source_tag(source: str) -> str:
    """The review tag for a planned value's provenance."""
    s = source.lower()
    if s.startswith("ai-draft"):
        if "gate-flagged" in s:
            return "[GATE-FLAGGED]"
        if "needs-input" in s:
            return "[NEEDS-INPUT]"
        return "[AI-DRAFT]"
    if s.startswith(("answer_bank", "career_facts", "resume")):
        return "[FROM ANSWER_BANK]"
    return "[MANUAL]"


def _unfilled_tag(reason: str) -> str:
    low = reason.lower()
    if "consent" in low:
        return "[PAUSED: consent]"
    if "credential" in low:
        return "[PAUSED: credential]"
    return "[MISSING]"


def has_ai_drafts(plan: FillPlan) -> bool:
    return any(p.source.lower().startswith("ai-draft") for p in plan.planned)


def _row_selectors(plan: FillPlan) -> list[str]:
    """Selectors in display order — row N in the review is index N-1 here."""
    return ([p.field.selector for p in plan.planned]
            + [u.field.selector for u in plan.unfilled])


def render_review(plan: FillPlan, heading: str = "APPLICATION REVIEW") -> str:
    """A readable summary of EVERY value to be submitted, plus every gap.

    Rows are numbered; ``edit <row>=<value>`` targets a row by that number
    (``edit <selector>=<value>`` still works).
    """
    lines = [f"=== {heading} ===", ""]
    lines.append(f"Will submit {len(plan.planned)} field(s):")
    row = 0
    for pf in plan.planned:
        row += 1
        req = " *required" if pf.field.required else ""
        lines.append(f"  {row:>2}. {source_tag(pf.source):<18} {pf.field.describe():<40} = {pf.value}")
        lines.append(f"      ↳ source: {pf.source}{req}")
    if plan.unfilled:
        lines += ["", f"Left EMPTY ({len(plan.unfilled)} — never guessed):"]
        for u in plan.unfilled:
            row += 1
            mark = "‼ REQUIRED" if u.field.required else "optional"
            lines.append(f"  {row:>2}. {_unfilled_tag(u.reason):<20} [{mark}] {u.field.describe()}")
            lines.append(f"      ↳ {u.reason}")
    if has_ai_drafts(plan):
        lines += ["", "⚠ AI-DRAFTED answers above — read each one; edit with "
                      "'edit <selector>=<value>' before approving. You must be able "
                      "to stand behind every sentence."]
    missing = plan.missing_required()
    lines += [""]
    if missing:
        lines.append(f"⚠ {len(missing)} REQUIRED field(s) unfilled — cannot approve until "
                     f"filled (edit) or the application is skipped.")
    else:
        lines.append("All required fields are filled.")
    return "\n".join(lines)


def _apply_edit(plan: FillPlan, command: str, io: PromptIO) -> FillPlan:
    """Apply ``edit <row>=<value>`` or ``edit <selector>=<value>``; report errors.

    The value is stored verbatim (case preserved) — only the target is resolved.
    """
    body = command[len("edit"):].strip()
    if "=" not in body:
        io.write("  edit needs the form: edit <row|selector>=<value>")
        return plan
    target, value = (s.strip() for s in body.split("=", 1))
    if target.isdigit():                       # row number from the review listing
        rows = _row_selectors(plan)
        if not 1 <= int(target) <= len(rows):
            io.write(f"  no row {target} (rows are 1-{len(rows)})")
            return plan
        target = rows[int(target) - 1]
    try:
        new_plan = plan.with_value(target, value)
        io.write(f"  set {target} = {value}")
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
        if has_ai_drafts(plan):
            # An AI answer is NEVER submitted unreviewed — no auto path past it.
            io.write("cannot auto-approve: AI-drafted answers require human review.")
            return ReviewOutcome(Decision.SKIP, plan)
        io.write("[demo] simulated approval: approve")
        return ReviewOutcome(Decision.APPROVE, plan)

    while True:
        raw = io.read("\nApprove and continue? [approve / edit <row|sel>=<val> / skip]: ").strip()
        choice = raw.lower()
        if choice in ("skip", "s", "no", "n"):
            return ReviewOutcome(Decision.SKIP, plan)
        if choice.startswith("edit"):
            plan = _apply_edit(plan, raw, io)   # raw: edited values keep their case
            io.write(render_review(plan))
            continue
        if choice in ("approve", "a", "yes", "y"):
            if plan.missing_required():
                io.write("Cannot approve: required field(s) still empty. Use 'edit' or 'skip'.")
                continue
            return ReviewOutcome(Decision.APPROVE, plan)
        io.write("Please type 'approve', 'edit <selector>=<value>', or 'skip'.")
