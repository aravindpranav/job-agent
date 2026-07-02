"""Lever board source.

Endpoint (public, no auth):
    https://api.lever.co/v0/postings/{board}?mode=json

Real shape (observed): a flat list of postings, each with ``id``, ``text``
(title), ``createdAt`` (epoch ms), ``categories.location`` /
``categories.allLocations``, ``workplaceType`` (remote / hybrid / on-site),
``country`` (e.g. "US"), ``hostedUrl``, ``applyUrl``, and ``descriptionPlain``.
"""

from __future__ import annotations

from job_agent.http import SourceError, get_json
from job_agent.models import Job
from job_agent.sources.base import JobSource, epoch_ms_to_dt, truncate

BASE = "https://api.lever.co/v0/postings"


class LeverSource(JobSource):
    ats = "lever"

    def fetch(self) -> list[Job]:
        url = f"{BASE}/{self.board}"
        try:
            data = get_json(url, params={"mode": "json"})
        except SourceError:
            raise

        jobs: list[Job] = []
        for raw in data:
            categories = raw.get("categories") or {}
            location = categories.get("location") or ", ".join(
                categories.get("allLocations", [])
            ) or "Unspecified"
            workplace = (raw.get("workplaceType") or "").lower()
            jobs.append(
                Job(
                    id=str(raw["id"]),
                    title=raw.get("text", "").strip(),
                    company=self.board.title(),
                    location=location,
                    url=raw.get("hostedUrl", ""),
                    apply_url=raw.get("applyUrl"),
                    source=self.ats,
                    posted_at=epoch_ms_to_dt(raw.get("createdAt")),
                    remote=workplace == "remote" if workplace else None,
                    country=raw.get("country"),
                    description=truncate(raw.get("descriptionPlain", "")),
                )
            )
        return jobs
