"""Assisted-apply subpackage.

Slice 3 (this commit): the application **answer bank** — a validated, gitignored
store of the answers real ATS forms ask for (work authorization, salary, notice
period, EEO, prepared free-text answers). Contact details are *not* duplicated
here; they are merged in from ``career_facts.yaml`` so there is one source of
truth (see :func:`answer_bank.resolve_contact`).

Slice 4 (later): the browser-automation modules — ``form_reader``, ``filler``,
``review``, ``handoff``, ``submit`` — land in this package. They will read the
answer bank built here, fill fields, pause for logins/captchas/unknown fields,
show a full review, and submit only behind an explicit ``--submit`` flag *and*
per-application human approval.
"""

from __future__ import annotations

from job_agent.apply.answer_bank import (
    DECLINE_TO_STATE,
    AnswerBank,
    Contact,
    EeoAnswers,
    resolve_contact,
    load_answer_bank,
)
from job_agent.apply.fields import (
    FieldType,
    FillPlan,
    FormField,
    PlannedFill,
    Unfilled,
)
from job_agent.apply.filler import build_fill_plan
from job_agent.apply.review import Decision, ReviewOutcome, request_approval
from job_agent.apply.runner import ApplyConfig, run_apply
from job_agent.apply.submit import SubmitResult, submit_block_reason

__all__ = [
    # Slice 3 — answer bank
    "AnswerBank",
    "Contact",
    "EeoAnswers",
    "DECLINE_TO_STATE",
    "load_answer_bank",
    "resolve_contact",
    # Slice 4 — assisted apply
    "FieldType",
    "FormField",
    "FillPlan",
    "PlannedFill",
    "Unfilled",
    "build_fill_plan",
    "Decision",
    "ReviewOutcome",
    "request_approval",
    "SubmitResult",
    "submit_block_reason",
    "ApplyConfig",
    "run_apply",
]
