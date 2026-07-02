"""Application assist — LATER SLICE (stub).

Planned: after a human approves, assist the application via browser automation.
Submission is deliberately NOT done through ATS APIs (those submit endpoints need
the employer's private API key). Reading jobs and questions is public; submitting
is not.

Left as a stub in Slice 1. Two invariants for when this is built:
  1. Every application stops at a human-approval gate BEFORE any submit.
  2. Automation drives the browser; it never posts to a private ATS submit API.
"""

from __future__ import annotations

from job_agent.models import ScoredJob


def approve_gate(job: ScoredJob) -> bool:
    """Block for explicit human approval before an application proceeds.

    TODO(slice-4): present the tailored resume + answers and require a yes/no.
    """
    raise NotImplementedError("The human-approval gate is a later slice (see apply.py).")


def assist_application(job: ScoredJob) -> None:
    """Drive the browser to fill the application after approval.

    TODO(slice-4): browser automation; must be called only after approve_gate().
    """
    raise NotImplementedError("Application assist is a later slice (see apply.py).")
