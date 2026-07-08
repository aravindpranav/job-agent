"""RemoteOK public API — cross-company REMOTE jobs by tag.

Probed live (scripts/probe_sources.py, 2026-07-08) — official public API, no
auth. Terms (stated in the response's leading ``legal`` element): link back to
RemoteOK and credit it as the source when republishing — this tool surfaces
RemoteOK's own URLs to one human and republishes nothing.

Endpoint:
    https://remoteok.com/api?tag={tag}          (tag is a slug, e.g. "machine-learning")

Real shape (observed): a JSON LIST whose first element is a legal/metadata
object (no ``id``); each following job has ``id``, ``position`` (title),
``company``, ``location`` (free text, often empty or "Remote - United States"),
``date`` (ISO with offset), ``url``, ``apply_url``, and an HTML ``description``.
"""

from __future__ import annotations

from job_agent.http import SourceError, get_json
from job_agent.models import Job
from job_agent.sources.base import JobSource, guess_us_country, parse_iso, strip_html, truncate

API_URL = "https://remoteok.com/api"


class RemoteOKSource(JobSource):
    """``board`` is the tag slug (e.g. "machine-learning")."""

    ats = "remoteok"

    def fetch(self) -> list[Job]:
        try:
            data = get_json(API_URL, params={"tag": self.board})
        except SourceError:
            raise
        if not isinstance(data, list):
            return []
        jobs: list[Job] = []
        for raw in data:
            if not isinstance(raw, dict) or "id" not in raw:
                continue                          # the leading legal element
            location = (raw.get("location") or "").strip(" ,") or "Remote"
            jobs.append(Job(
                id=str(raw["id"]),
                title=raw.get("position", "").strip(),
                company=raw.get("company", "").strip() or "Unknown",
                location=location,
                url=raw.get("url", ""),
                apply_url=raw.get("apply_url") or raw.get("url"),
                source=self.ats,
                posted_at=parse_iso(raw.get("date")),
                remote=True,
                country=guess_us_country(location),
                description=truncate(strip_html(raw.get("description", ""))),
            ))
        return jobs
