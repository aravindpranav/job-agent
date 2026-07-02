"""Resume tailoring — LATER SLICE (stub).

Planned: given a ScoredJob and the candidate's base resume, produce a tailored
resume PDF emphasizing the matched requirements.

Left as a stub in Slice 1 by design. The base resume and the "mega prompt" that
drives tailoring arrive in a later slice; the resume lives in a gitignored path.
"""

from __future__ import annotations

from job_agent.models import ScoredJob


def tailor_resume(job: ScoredJob, base_resume_path: str) -> str:
    """Return a path to a tailored resume PDF for ``job``.

    TODO(slice-2): render a tailored PDF from the base resume + job requirements.
    """
    raise NotImplementedError("Resume tailoring is a later slice (see tailor.py).")
