"""Ashby job-board source.

Endpoint (public, no auth):
    https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true

Real shape (observed): ``jobs`` list, each with ``id``, ``title``,
``publishedAt`` (ISO UTC — clean for the 24h filter), ``location``, ``isRemote``,
``address.postalAddress.addressCountry`` (e.g. "USA", "United States"),
``jobUrl``, ``applyUrl``, and ``descriptionPlain``.
"""

from __future__ import annotations

from job_agent.http import SourceError, get_json
from job_agent.models import Job
from job_agent.sources.base import JobSource, parse_iso, truncate

BASE = "https://api.ashbyhq.com/posting-api/job-board"


class AshbySource(JobSource):
    ats = "ashby"

    def fetch(self) -> list[Job]:
        url = f"{BASE}/{self.board}"
        try:
            data = get_json(url, params={"includeCompensation": "true"})
        except SourceError:
            raise

        jobs: list[Job] = []
        for raw in data.get("jobs", []):
            postal = (raw.get("address") or {}).get("postalAddress") or {}
            jobs.append(
                Job(
                    id=str(raw["id"]),
                    title=raw.get("title", "").strip(),
                    company=self.board.title(),
                    location=raw.get("location") or "Unspecified",
                    url=raw.get("jobUrl", ""),
                    apply_url=raw.get("applyUrl"),
                    source=self.ats,
                    posted_at=parse_iso(raw.get("publishedAt")),
                    remote=raw.get("isRemote"),
                    country=postal.get("addressCountry"),
                    description=truncate(raw.get("descriptionPlain", "")),
                )
            )
        return jobs
