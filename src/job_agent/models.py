"""Typed, immutable domain models.

Every ATS source normalizes its raw JSON into a :class:`Job`. The scorer wraps a
``Job`` into a :class:`ScoredJob`. Both are frozen (immutable) — code creates new
objects rather than mutating existing ones.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The ATS a job came from.
SourceName = Literal["greenhouse", "lever", "ashby", "smartrecruiters", "demo"]

# The scorer's verdict. "unscored" is a distinct state used when the LLM output
# could not be parsed even after one retry (see scoring.py).
Verdict = Literal["strong", "possible", "skip", "unscored"]


class Job(BaseModel):
    """A single job posting, normalized across all ATS sources."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Stable per-source id (source-scoped, not global).")
    title: str
    company: str
    location: str = Field(description="Human-readable location, e.g. 'Remote' or 'Austin, TX'.")
    url: str = Field(description="Public posting URL a human can open.")
    source: SourceName
    apply_url: str | None = None
    # Timezone-aware. None when the source exposes no post date and no cache hit
    # exists yet (see seen_cache.py for the fallback used by the 24h filter).
    posted_at: datetime | None = None
    remote: bool | None = None
    # Best-effort country marker for the location rule; None when unknown.
    country: str | None = None
    description: str = ""

    def dedup_key(self) -> str:
        """Canonical identity used to drop duplicates across boards/re-runs."""
        return "|".join([self.company.strip().lower(), self.title.strip().lower(),
                          self.location.strip().lower()])


class ScoredJob(BaseModel):
    """A :class:`Job` plus the LLM's fit assessment."""

    model_config = ConfigDict(frozen=True)

    job: Job
    # 0-100 fit score; None when the job could not be scored (verdict == "unscored").
    score: int | None = Field(default=None, ge=0, le=100)
    verdict: Verdict = "unscored"
    reasons: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()

    @property
    def sort_key(self) -> tuple[int, int]:
        """Rank strong > possible > skip > unscored, then by score (desc)."""
        verdict_rank = {"strong": 3, "possible": 2, "skip": 1, "unscored": 0}
        return (verdict_rank[self.verdict], self.score if self.score is not None else -1)
