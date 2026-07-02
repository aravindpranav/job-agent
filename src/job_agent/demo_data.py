"""Mock jobs for `--demo` mode.

These let anyone clone the repo and run the whole pipeline with no API key and no
network. Timestamps are generated relative to "now" so the 24h filter keeps the
fresh ones and drops the stale one, demonstrating the filter without live calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from job_agent.models import Job


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
