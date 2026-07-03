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

__all__ = [
    "AnswerBank",
    "Contact",
    "EeoAnswers",
    "DECLINE_TO_STATE",
    "load_answer_bank",
    "resolve_contact",
]
