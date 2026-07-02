"""ATS source implementations and a small factory."""

from __future__ import annotations

from job_agent.sources.ashby import AshbySource
from job_agent.sources.base import JobSource
from job_agent.sources.greenhouse import GreenhouseSource
from job_agent.sources.lever import LeverSource
from job_agent.sources.smartrecruiters import SmartRecruitersSource

_REGISTRY: dict[str, type[JobSource]] = {
    GreenhouseSource.ats: GreenhouseSource,
    LeverSource.ats: LeverSource,
    AshbySource.ats: AshbySource,
    SmartRecruitersSource.ats: SmartRecruitersSource,
}


def build_source(ats: str, board: str) -> JobSource:
    """Construct a source for ``ats``. Raises KeyError for unknown ATS names."""
    return _REGISTRY[ats](board)


__all__ = [
    "JobSource",
    "GreenhouseSource",
    "LeverSource",
    "AshbySource",
    "SmartRecruitersSource",
    "build_source",
]
