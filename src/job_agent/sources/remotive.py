"""Remotive public API — cross-company REMOTE jobs by keyword.

Probed live (scripts/probe_sources.py, 2026-07-08) — official public API, no
auth. Terms (stated in the response's ``0-legal-notice``): access is granted to
share their jobs onward; submitting them to third-party job boards is
forbidden — this tool only feeds a personal score/tailor/apply pipeline.

Endpoint:
    https://remotive.com/api/remote-jobs?search={query}

Real shape (observed): ``{job-count, jobs}``; each job has ``id``, ``title``,
``company_name``, ``candidate_required_location`` (free text: "Worldwide",
"USA", regions), ``publication_date`` (ISO, no offset — treated as UTC),
``url``, and an HTML ``description``. Everything is remote by definition;
country is only set when the required location clearly reads US, so the
pipeline's location stage keeps unknown-region remotes gated by ``remote_ok``.
"""

from __future__ import annotations

from job_agent.http import SourceError, get_json
from job_agent.models import Job
from job_agent.sources.base import JobSource, guess_us_country, parse_iso, strip_html, truncate

API_URL = "https://remotive.com/api/remote-jobs"


class RemotiveSource(JobSource):
    """``board`` is the keyword query (e.g. "machine learning")."""

    ats = "remotive"

    def fetch(self) -> list[Job]:
        try:
            data = get_json(API_URL, params={"search": self.board})
        except SourceError:
            raise
        jobs: list[Job] = []
        for raw in data.get("jobs", []):
            location = raw.get("candidate_required_location") or "Remote"
            jobs.append(Job(
                id=str(raw["id"]),
                title=raw.get("title", "").strip(),
                company=raw.get("company_name", "").strip() or "Unknown",
                location=location,
                url=raw.get("url", ""),
                apply_url=raw.get("url"),
                source=self.ats,
                posted_at=parse_iso(raw.get("publication_date")),
                remote=True,
                country=guess_us_country(location),
                description=truncate(strip_html(raw.get("description", ""))),
            ))
        return jobs
