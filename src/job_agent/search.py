"""The discovery pipeline: fetch → keyword → freshness → location → dedup.

The keyword pre-filter runs *before* anything expensive so we never spend an LLM
call on the hundreds of unrelated roles a company board carries. Every stage's
survivor count is returned (not hidden) so the CLI can show exactly what was
pruned — no silent truncation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict

from job_agent.config import SearchProfile
from job_agent.models import Job
from job_agent.seen_cache import SeenCache
from job_agent.sources import JobSource, build_source

FRESH_WINDOW = timedelta(hours=24)

# Country strings (any case) we treat as the United States.
_US_ALIASES = {"US", "USA", "U.S.", "U.S.A.", "UNITED STATES", "UNITED STATES OF AMERICA"}


class StageCounts(BaseModel):
    """How many jobs survived each pipeline stage."""

    model_config = ConfigDict(frozen=True)

    fetched: int = 0
    after_keyword: int = 0
    after_fresh: int = 0
    after_location: int = 0
    after_dedup: int = 0


class SearchOutcome(BaseModel):
    """Result of a discovery run: the survivors plus visibility into pruning."""

    model_config = ConfigDict(frozen=True)

    jobs: list[Job]
    counts: StageCounts
    per_source: dict[str, int]           # board label -> jobs fetched
    warnings: list[str] = []             # source failures, etc. (never fatal)


def matches_keywords(title: str, keywords: list[str]) -> bool:
    """True if the title contains any keyword (case-insensitive substring)."""
    low = title.lower()
    return any(kw.lower() in low for kw in keywords)


def _country_allowed(country: str | None, allowed: list[str]) -> bool | None:
    """True/False if the job's country is known; None if unknown."""
    if country is None:
        return None
    c = country.strip().upper()
    allowed_up = {a.upper() for a in allowed}
    want_us = bool(allowed_up & {"US", "USA", "UNITED STATES"})
    if want_us and c in _US_ALIASES:
        return True
    return c in allowed_up


def passes_location(job: Job, profile: SearchProfile) -> bool:
    """Apply the location rule.

    * Known country → honor the allow-list, *even for remote roles* (a role that
      is "remote within the UK" is still a non-US role and is dropped).
    * Unknown country → keep it: if it's remote, gate on ``remote_ok``; otherwise
      leave it for the scorer to weigh.
    """
    rule = profile.location
    allowed = _country_allowed(job.country, rule.allowed_countries)
    if allowed is not None:
        return allowed
    if job.remote:
        return rule.remote_ok
    return True


def is_fresh(job: Job, now: datetime, cache: SeenCache) -> bool:
    """Posted within the freshness window. Jobs without a post date fall back to
    'first observed within the window' via the seen-ids cache."""
    reference = job.posted_at or cache.observe(job.source, job.id, now)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (now - reference) <= FRESH_WINDOW


def run(
    profile: SearchProfile,
    *,
    seen_cache: SeenCache,
    now: datetime | None = None,
    source_factory=build_source,
) -> SearchOutcome:
    """Execute the pipeline for a profile and return survivors + stage counts."""
    now = now or datetime.now(timezone.utc)
    warnings: list[str] = []
    per_source: dict[str, int] = {}
    all_jobs: list[tuple[Job, str]] = []  # (job, "ats/board" label)
    # Keep one source instance per board so we can enrich() survivors later.
    sources: dict[str, JobSource] = {}

    for ref in profile.sources:
        label = f"{ref.ats}/{ref.board}"
        try:
            source = source_factory(ref.ats, ref.board)
        except KeyError:
            warnings.append(f"{label}: unknown ATS, skipped")
            continue
        sources[label] = source
        try:
            fetched = source.fetch()
        except Exception as exc:  # SourceError or unexpected — one board must never break the run
            warnings.append(f"{label}: fetch failed ({type(exc).__name__}: {exc})")
            per_source[label] = 0
            continue
        per_source[label] = len(fetched)
        all_jobs.extend((job, label) for job in fetched)

    # Stage 1: keyword pre-filter (cheap, biggest reduction).
    kept = [(j, lbl) for (j, lbl) in all_jobs if matches_keywords(j.title, profile.keywords)]
    after_keyword = len(kept)

    # Stage 2: 24h freshness.
    kept = [(j, lbl) for (j, lbl) in kept if is_fresh(j, now, seen_cache)]
    after_fresh = len(kept)
    seen_cache.save()

    # Stage 3: location rule.
    kept = [(j, lbl) for (j, lbl) in kept if passes_location(j, profile)]
    after_location = len(kept)

    # Stage 4: dedup (first occurrence wins).
    seen_keys: set[str] = set()
    deduped: list[tuple[Job, str]] = []
    for job, lbl in kept:
        key = job.dedup_key()
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append((job, lbl))
    after_dedup = len(deduped)

    # Enrich only the final survivors (e.g. SmartRecruiters detail fetch).
    enriched: list[Job] = []
    for job, lbl in deduped:
        try:
            enriched.append(sources[lbl].enrich(job))
        except Exception as exc:  # enrichment is best-effort
            warnings.append(f"{lbl}: enrich failed for {job.id} ({exc})")
            enriched.append(job)

    return SearchOutcome(
        jobs=enriched,
        counts=StageCounts(
            fetched=len(all_jobs),
            after_keyword=after_keyword,
            after_fresh=after_fresh,
            after_location=after_location,
            after_dedup=after_dedup,
        ),
        per_source=per_source,
        warnings=warnings,
    )
