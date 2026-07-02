"""SmartRecruiters company-postings source.

List endpoint (public, no auth):
    https://api.smartrecruiters.com/v1/companies/{company}/postings

Real shape (observed): paged ``content`` list with ``id``, ``name`` (title),
``releasedDate`` (ISO 'Z'), and a structured ``location`` (``country``,
``remote``, ``hybrid``, ``fullLocation``). The list carries **no** public URL or
description — those come from the per-posting detail endpoint:
    https://api.smartrecruiters.com/v1/companies/{company}/postings/{id}
which returns ``postingUrl``, ``applyUrl``, and ``jobAd.sections``.

To avoid a detail call for every posting, ``fetch()`` returns lightweight jobs
and ``enrich()`` pulls the description only for keyword survivors.
"""

from __future__ import annotations

from job_agent.http import SourceError, get_json
from job_agent.models import Job
from job_agent.sources.base import JobSource, parse_iso, strip_html, truncate

BASE = "https://api.smartrecruiters.com/v1/companies"
PAGE_LIMIT = 100
MAX_PAGES = 10  # safety cap; logged if we hit it (see search.run)


class SmartRecruitersSource(JobSource):
    ats = "smartrecruiters"

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        offset = 0
        for _ in range(MAX_PAGES):
            try:
                data = get_json(
                    f"{BASE}/{self.board}/postings",
                    params={"limit": PAGE_LIMIT, "offset": offset},
                )
            except SourceError:
                raise
            content = data.get("content", [])
            if not content:
                break
            jobs.extend(self._to_job(raw) for raw in content)
            offset += len(content)
            if offset >= data.get("totalFound", offset):
                break
        return jobs

    def _to_job(self, raw: dict) -> Job:
        loc = raw.get("location") or {}
        return Job(
            id=str(raw["id"]),
            title=raw.get("name", "").strip(),
            company=self.board,
            location=loc.get("fullLocation") or "Unspecified",
            # Public URL pattern; enrich() replaces it with the authoritative one.
            url=f"https://jobs.smartrecruiters.com/{self.board}/{raw['id']}",
            apply_url=None,
            source=self.ats,
            posted_at=parse_iso(raw.get("releasedDate")),
            remote=bool(loc.get("remote")),
            country=loc.get("country"),  # e.g. "us"
            description="",  # filled by enrich()
        )

    def enrich(self, job: Job) -> Job:
        """Fetch the detail endpoint for one job to add its full description."""
        try:
            detail = get_json(f"{BASE}/{self.board}/postings/{job.id}")
        except SourceError:
            return job  # keep the lightweight job; description stays empty
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        parts = [
            (sections.get(name) or {}).get("text", "")
            for name in ("jobDescription", "qualifications", "additionalInformation")
        ]
        description = truncate(strip_html(" ".join(p for p in parts if p)))
        return job.model_copy(
            update={
                "url": detail.get("postingUrl", job.url),
                "apply_url": detail.get("applyUrl"),
                "description": description,
            }
        )
