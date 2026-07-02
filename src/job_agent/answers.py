"""Application answer bank — LATER SLICE (stub).

Planned: collect and reuse answers to common application questions (work
authorization, salary expectations, "why this company", etc.), reading the real
per-application questions from the ATS (e.g. Greenhouse
``/jobs/{id}?questions=true``, which we've confirmed is available).

Left as a stub in Slice 1. Answer data is personal and will live in a gitignored
location.
"""

from __future__ import annotations

from job_agent.models import Job


def collect_answers(job: Job) -> dict[str, str]:
    """Return answers to ``job``'s application questions.

    TODO(slice-3): fetch the job's real questions and fill from the answer bank.
    """
    raise NotImplementedError("Answer collection is a later slice (see answers.py).")
