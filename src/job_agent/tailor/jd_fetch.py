"""Re-fetch a job's FULL description at tailor time.

Slice 1 stores a truncated (~2000 char) description for scoring. Tailoring wants
the complete JD, so this re-hits the source by (source, board, id) and returns the
untruncated text (capped to keep token cost sane).
"""

from __future__ import annotations

from job_agent.http import SourceError, get_json
from job_agent.sources.base import strip_html

MAX_JD_CHARS = 8000


def _cap(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_JD_CHARS else text[:MAX_JD_CHARS].rstrip() + " …"


def get_full_jd(source: str, board: str, job_id: str) -> str:
    """Return the full JD text for a job, or "" if it can't be re-fetched."""
    try:
        if source == "greenhouse":
            data = get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}",
                params={"questions": "false"},
            )
            return _cap(strip_html(data.get("content", "")))

        if source == "lever":
            data = get_json(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"})
            match = next((p for p in data if str(p.get("id")) == str(job_id)), None)
            return _cap(match.get("descriptionPlain", "")) if match else ""

        if source == "ashby":
            data = get_json(
                f"https://api.ashbyhq.com/posting-api/job-board/{board}",
                params={"includeCompensation": "true"},
            )
            match = next((j for j in data.get("jobs", []) if str(j.get("id")) == str(job_id)), None)
            return _cap(match.get("descriptionPlain", "")) if match else ""

        if source == "smartrecruiters":
            data = get_json(f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{job_id}")
            sections = (data.get("jobAd") or {}).get("sections") or {}
            parts = [(sections.get(k) or {}).get("text", "")
                     for k in ("jobDescription", "qualifications", "additionalInformation")]
            return _cap(strip_html(" ".join(p for p in parts if p)))
    except SourceError:
        return ""
    return ""
