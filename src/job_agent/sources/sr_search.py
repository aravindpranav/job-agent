"""SmartRecruiters CROSS-COMPANY posting search.

Unlike every other source (one company board per instance), this queries the
API behind SmartRecruiters' own public job-search site and returns postings
from MANY companies for one keyword. Probed live (scripts/probe_sources.py,
2026-07-08) — public, no auth.

Endpoint:
    https://jobs.smartrecruiters.com/sr-jobs/search?keyword={query}

Real shape (observed): ``{offset, limit, totalFound, content}``; each content
item has ``id``, ``name`` (title), ``company.name``, ``releasedDate`` (ISO 'Z'),
structured ``location`` (``country`` e.g. "us", ``remote``, ``hybrid``),
``shortLocation``, ``applyUrl``, and ``actions.details`` — the official
per-company postings-API URL for the full description. The detail posting id
DIFFERS from the search-result id, so ``enrich()`` must call the URL the search
response provided, remembered per job id on this instance.

Caveat (verified): ``limit`` / ``offset`` / ``page`` / ``country`` params are
ignored — one relevance-ordered page of ~100 results per keyword. Country
filtering happens downstream in the pipeline's location stage.
"""

from __future__ import annotations

from job_agent.http import SourceError, get_json
from job_agent.models import Job
from job_agent.sources.base import JobSource, parse_iso, strip_html, truncate

SEARCH_URL = "https://jobs.smartrecruiters.com/sr-jobs/search"


class SmartRecruitersSearchSource(JobSource):
    """``board`` is the keyword query (e.g. "machine learning engineer")."""

    ats = "sr-search"

    def __init__(self, board: str) -> None:
        super().__init__(board)
        self._detail_urls: dict[str, str] = {}   # job id -> actions.details URL

    def fetch(self) -> list[Job]:
        try:
            data = get_json(SEARCH_URL, params={"keyword": self.board})
        except SourceError:
            raise
        jobs: list[Job] = []
        for raw in data.get("content", []):
            details = (raw.get("actions") or {}).get("details")
            if details:
                self._detail_urls[str(raw["id"])] = details
            jobs.append(self._to_job(raw))
        return jobs

    def _to_job(self, raw: dict) -> Job:
        loc = raw.get("location") or {}
        return Job(
            id=str(raw["id"]),
            title=raw.get("name", "").strip(),
            company=(raw.get("company") or {}).get("name", "").strip() or "Unknown",
            location=raw.get("shortLocation") or "Unspecified",
            url=raw.get("applyUrl", ""),
            apply_url=raw.get("applyUrl"),
            source=self.ats,
            posted_at=parse_iso(raw.get("releasedDate")),
            remote=bool(loc.get("remote")),
            country=loc.get("country"),          # e.g. "us"
            description="",                       # filled by enrich()
        )

    def enrich(self, job: Job) -> Job:
        """Pull the full description from the detail URL the search gave us."""
        detail_url = self._detail_urls.get(job.id)
        if not detail_url:
            return job                            # no detail URL — keep it light
        try:
            detail = get_json(detail_url)
        except SourceError:
            return job
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        parts = [
            (sections.get(name) or {}).get("text", "")
            for name in ("jobDescription", "qualifications", "additionalInformation")
        ]
        description = truncate(strip_html(" ".join(p for p in parts if p)))
        return job.model_copy(update={
            "url": detail.get("postingUrl", job.url),
            "apply_url": detail.get("applyUrl", job.apply_url),
            "description": description,
        })
