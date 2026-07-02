"""Greenhouse board source.

Endpoint (public, no auth):
    https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true

Real shape (observed): each job has ``id``, ``title``, ``company_name``,
``location.name``, ``absolute_url``, ``content`` (HTML), ``first_published`` and
``updated_at`` (both tz-aware ISO). ``first_published`` was populated on every job
observed, so it's the primary post date; the seen-ids cache is the fallback if a
future posting has it null. Greenhouse has no structured country, so country is
guessed loosely from the location string.
"""

from __future__ import annotations

from job_agent.http import SourceError, get_json
from job_agent.models import Job
from job_agent.sources.base import (
    JobSource,
    guess_us_country,
    parse_iso,
    strip_html,
    truncate,
)

BASE = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseSource(JobSource):
    ats = "greenhouse"

    def fetch(self) -> list[Job]:
        url = f"{BASE}/{self.board}/jobs"
        try:
            data = get_json(url, params={"content": "true"})
        except SourceError:
            raise  # caller (search.run) logs and continues

        jobs: list[Job] = []
        for raw in data.get("jobs", []):
            location = (raw.get("location") or {}).get("name", "") or "Unspecified"
            jobs.append(
                Job(
                    id=str(raw["id"]),
                    title=raw.get("title", "").strip(),
                    company=raw.get("company_name") or self.board.title(),
                    location=location,
                    url=raw.get("absolute_url", ""),
                    apply_url=raw.get("absolute_url"),
                    source=self.ats,
                    posted_at=parse_iso(raw.get("first_published") or raw.get("updated_at")),
                    remote="remote" in location.lower(),
                    country=guess_us_country(location),
                    description=truncate(strip_html(raw.get("content", ""))),
                )
            )
        return jobs
