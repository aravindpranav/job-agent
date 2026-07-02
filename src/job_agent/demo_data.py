"""Mock jobs for `--demo` mode.

These let anyone clone the repo and run the whole pipeline with no API key and no
network. Timestamps are generated relative to "now" so the 24h filter keeps the
fresh ones and drops the stale one, demonstrating the filter without live calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from job_agent.config import LocationRule, SearchProfile, SourceRef
from job_agent.models import Job, ScoredJob
from job_agent.sources.base import JobSource


def demo_jobs(now: datetime | None = None) -> list[Job]:
    """A small, varied set of jobs covering the interesting cases."""
    now = now or datetime.now(timezone.utc)
    hours_ago = lambda h: now - timedelta(hours=h)  # noqa: E731

    return [
        Job(
            id="demo-1",
            title="Senior Data Engineer",
            company="Northwind Analytics",
            location="Remote (US)",
            url="https://example.com/jobs/demo-1",
            apply_url="https://example.com/jobs/demo-1/apply",
            source="demo",
            posted_at=hours_ago(3),
            remote=True,
            country="US",
            description=(
                "Build and own batch and streaming data pipelines on AWS "
                "(Glue, EMR, Redshift). Strong Python and SQL; dbt and Airflow "
                "experience preferred. Partner with ML teams to serve features."
            ),
        ),
        Job(
            id="demo-2",
            title="Machine Learning Engineer, LLM Platform",
            company="Cobalt AI",
            location="New York, NY (Hybrid)",
            url="https://example.com/jobs/demo-2",
            apply_url="https://example.com/jobs/demo-2/apply",
            source="demo",
            posted_at=hours_ago(12),
            remote=False,
            country="US",
            description=(
                "Design retrieval-augmented generation systems and fine-tuning "
                "pipelines. Python, PyTorch, and experience deploying LLMs to "
                "production. Familiarity with vector databases a plus."
            ),
        ),
        Job(
            id="demo-3",
            title="Data Scientist, Growth",
            company="Meridian Labs",
            location="Austin, TX",
            url="https://example.com/jobs/demo-3",
            apply_url="https://example.com/jobs/demo-3/apply",
            source="demo",
            posted_at=hours_ago(20),
            remote=False,
            country="US",
            description=(
                "Run experiments and build predictive models for user growth. "
                "SQL, Python, and A/B testing. Some data engineering expected to "
                "self-serve pipelines."
            ),
        ),
        Job(
            id="demo-4",
            title="Staff Frontend Engineer",
            company="Northwind Analytics",
            location="Remote (US)",
            url="https://example.com/jobs/demo-4",
            apply_url="https://example.com/jobs/demo-4/apply",
            source="demo",
            posted_at=hours_ago(6),
            remote=True,
            country="US",
            description=(
                "Own the React/TypeScript web app. Not a data role — included to "
                "show the keyword pre-filter removing off-target titles."
            ),
        ),
        Job(
            id="demo-5",
            title="Gen AI Engineer",
            company="Helios Robotics",
            location="London, UK",
            url="https://example.com/jobs/demo-5",
            apply_url="https://example.com/jobs/demo-5/apply",
            source="demo",
            posted_at=hours_ago(8),
            remote=False,
            country="GB",
            description=(
                "Great title match, wrong location — onsite in the UK. Included "
                "to show the location rule dropping non-US onsite roles."
            ),
        ),
        Job(
            id="demo-6",
            title="Senior Data Engineer",
            company="Stale Corp",
            location="Remote (US)",
            url="https://example.com/jobs/demo-6",
            apply_url="https://example.com/jobs/demo-6/apply",
            source="demo",
            posted_at=hours_ago(40),  # older than 24h — dropped by freshness
            remote=True,
            country="US",
            description="Matches keywords and location, but posted 40h ago.",
        ),
    ]


class DemoSource(JobSource):
    """A source that serves the bundled mock jobs, so `--demo` runs the *same*
    pipeline as a real run — just with a different source behind it."""

    ats = "demo"

    def __init__(self, board: str = "demo", now: datetime | None = None) -> None:
        super().__init__(board)
        self._now = now

    def fetch(self) -> list[Job]:
        return demo_jobs(self._now)


def demo_profile() -> SearchProfile:
    """A self-contained profile for demo mode (no YAML file needed)."""
    return SearchProfile(
        keywords=["data engineer", "data scientist", "machine learning",
                  "ml engineer", "ai engineer", "gen ai"],
        location=LocationRule(remote_ok=True, allowed_countries=["US"]),
        sources=[SourceRef(ats="demo", board="demo")],
        candidate_summary=(
            "Data / ML engineer targeting Data Engineer, Data Scientist, ML and "
            "Gen AI Engineer roles. Python, SQL, cloud data platforms, LLM "
            "pipelines. Open to remote (US) or onsite/hybrid in the USA."
        ),
    )


def score_demo(jobs: list[Job], profile: SearchProfile) -> list[ScoredJob]:
    """A deterministic, offline stand-in for the LLM scorer used in demo mode.

    Uses simple keyword/location heuristics so `--demo` produces a ranked list
    with plausible scores and no API call. The real scorer is scoring.py.
    """
    scored: list[ScoredJob] = []
    for job in jobs:
        text = f"{job.title} {job.description}".lower()
        hits = sum(1 for kw in profile.keywords if kw.lower() in text)
        title_hit = any(kw.lower() in job.title.lower() for kw in profile.keywords)
        us = job.remote or (job.country or "").upper() in {"US", "USA"}
        score = min(100, 40 + hits * 12 + (15 if title_hit else 0) + (10 if us else 0))
        verdict = "strong" if score >= 75 else "possible" if score >= 55 else "skip"
        reasons = []
        if title_hit:
            reasons.append("Title matches a target role")
        if us:
            reasons.append("Location fits (remote or US)")
        reasons.append(f"{hits} keyword(s) matched in the posting")
        missing = [] if job.description else ["No description available to assess depth"]
        scored.append(ScoredJob(job=job, score=score, verdict=verdict,
                                reasons=tuple(reasons), missing_requirements=tuple(missing)))
    return scored
