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
from job_agent.experience import required_years
from job_agent.geo import infer_country
from job_agent.models import Job
from job_agent.seen_cache import SeenCache
from job_agent.seniority import LEVEL_NAMES, seniority_rank
from job_agent.sources import JobSource, build_source

FRESH_WINDOW = timedelta(hours=24)

# A job is dropped on experience only when its JD requires at least this many
# more years than the candidate has (so an "8+ years" role is dropped for a
# 5-year candidate, but "6+"/"7+" and reachable ranges are kept).
EXPERIENCE_GAP = 3

# Country strings (any case) we treat as the United States.
_US_ALIASES = {"US", "USA", "U.S.", "U.S.A.", "UNITED STATES", "UNITED STATES OF AMERICA"}


class StageCounts(BaseModel):
    """How many jobs survived each pipeline stage."""

    model_config = ConfigDict(frozen=True)

    fetched: int = 0
    after_keyword: int = 0
    after_fresh: int = 0
    after_location: int = 0
    after_seniority: int = 0
    after_dedup: int = 0
    after_experience: int = 0


class SearchOutcome(BaseModel):
    """Result of a discovery run: the survivors plus visibility into pruning."""

    model_config = ConfigDict(frozen=True)

    jobs: list[Job]
    boards: list[str]                    # board token per job (parallel to jobs)
    counts: StageCounts
    per_source: dict[str, int]           # board label -> jobs fetched
    warnings: list[str] = []             # source failures, etc. (never fatal)
    first_seen: dict[str, str] = {}      # survivor job id -> first-observed ISO time
    new_job_ids: tuple[str, ...] = ()    # ids whose first-seen was recorded THIS scan
    baseline_scan: bool = False          # the seen cache was empty when the scan began


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
    * No structured country → infer one from the location text (``geo``). A
      *clearly* foreign string ("Bengaluru, India") is dropped here, before
      scoring, exactly as if the country were structured.
    * Still unknown → keep it: if it's remote, gate on ``remote_ok``; otherwise
      leave it for the scorer to weigh.
    """
    rule = profile.location
    country = job.country or infer_country(job.location)
    allowed = _country_allowed(country, rule.allowed_countries)
    if allowed is not None:
        return allowed
    if job.remote:
        return rule.remote_ok
    return True


def passes_seniority(job: Job, profile: SearchProfile) -> bool:
    """Drop titles above the profile's ceiling. ``max_seniority`` None = off.

    With ``max_seniority: senior`` this drops Lead / Staff / Principal / Director
    / VP and keeps Senior/Sr. and below — title-only, so it runs cheaply before
    dedup and enrichment.
    """
    if profile.max_seniority is None:
        return True
    return seniority_rank(job.title) <= LEVEL_NAMES[profile.max_seniority]


def passes_experience(job: Job, profile: SearchProfile) -> bool:
    """Drop jobs whose JD requires clearly more years than the candidate has.

    ``experience_years`` None = off. A JD with no stated minimum is kept (left to
    the scorer). Runs after enrichment so every source's full JD is available.
    """
    if profile.experience_years is None:
        return True
    required = required_years(job.description or "")
    if required is None:
        return True
    return required < profile.experience_years + EXPERIENCE_GAP


def is_fresh(job: Job, now: datetime, cache: SeenCache,
             window: timedelta = FRESH_WINDOW) -> bool:
    """Posted within the freshness window. Jobs without a post date fall back to
    'first observed within the window' via the seen-ids cache."""
    reference = job.posted_at or cache.observe(job.source, job.id, now)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (now - reference) <= window


def run(
    profile: SearchProfile,
    *,
    seen_cache: SeenCache,
    now: datetime | None = None,
    fresh_window: timedelta = FRESH_WINDOW,
    source_factory=build_source,
) -> SearchOutcome:
    """Execute the pipeline for a profile and return survivors + stage counts."""
    now = now or datetime.now(timezone.utc)
    # Captured BEFORE anything is recorded: an empty cache means there is no
    # earlier scan to be "new since" — the baseline rule downstream.
    baseline_scan = len(seen_cache) == 0
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

    # Stage 2: freshness (default 24h).
    kept = [(j, lbl) for (j, lbl) in kept if is_fresh(j, now, seen_cache, fresh_window)]
    after_fresh = len(kept)
    seen_cache.save()

    # Stage 3: location rule.
    kept = [(j, lbl) for (j, lbl) in kept if passes_location(j, profile)]
    after_location = len(kept)

    # Stage 4: seniority ceiling (title-only, so it runs before enrichment).
    kept = [(j, lbl) for (j, lbl) in kept if passes_seniority(j, profile)]
    after_seniority = len(kept)

    # Stage 5: dedup (first occurrence wins).
    seen_keys: set[str] = set()
    deduped: list[tuple[Job, str]] = []
    for job, lbl in kept:
        key = job.dedup_key()
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append((job, lbl))
    after_dedup = len(deduped)

    # Enrich only the final survivors (e.g. SmartRecruiters detail fetch), so the
    # experience filter below sees every source's full JD.
    enriched_pairs: list[tuple[Job, str]] = []
    for job, lbl in deduped:
        board = lbl.split("/", 1)[1] if "/" in lbl else lbl
        try:
            enriched_pairs.append((sources[lbl].enrich(job), board))
        except Exception as exc:  # enrichment is best-effort
            warnings.append(f"{lbl}: enrich failed for {job.id} ({exc})")
            enriched_pairs.append((job, board))

    # Stage 6: experience gap (needs the full JD, hence after enrichment).
    enriched_pairs = [(j, b) for (j, b) in enriched_pairs if passes_experience(j, profile)]
    after_experience = len(enriched_pairs)
    jobs = [j for j, _ in enriched_pairs]
    boards = [b for _, b in enriched_pairs]

    # Record when each SURVIVOR was first observed (get-or-record). Runs after
    # every filter stage, so no freshness decision in this run is affected —
    # and dated jobs never read these entries anyway (is_fresh short-circuits
    # on posted_at). Newness is the explicit boolean from first_seen(), never
    # a timestamp comparison.
    first_seen: dict[str, str] = {}
    new_ids: list[str] = []
    for job in jobs:
        ts, newly = seen_cache.first_seen(job.source, job.id, now)
        first_seen[str(job.id)] = ts.isoformat()
        if newly:
            new_ids.append(str(job.id))
    seen_cache.save()

    return SearchOutcome(
        jobs=jobs,
        boards=boards,
        counts=StageCounts(
            fetched=len(all_jobs),
            after_keyword=after_keyword,
            after_fresh=after_fresh,
            after_location=after_location,
            after_seniority=after_seniority,
            after_dedup=after_dedup,
            after_experience=after_experience,
        ),
        per_source=per_source,
        warnings=warnings,
        first_seen=first_seen,
        new_job_ids=tuple(new_ids),
        baseline_scan=baseline_scan,
    )
