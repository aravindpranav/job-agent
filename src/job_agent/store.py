"""Persist a search run so `tailor --job <id>` can pick a job up later.

Written to ``data/last_search.json`` (gitignored). Each record is the normalized
Job plus its board token and score/verdict, keyed by job id.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from job_agent.models import ScoredJob


def save_search(scored: list[ScoredJob], boards: list[str], path: str | Path) -> Path:
    """Persist scored jobs (parallel to ``boards``) keyed by id."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records: dict[str, dict] = {}
    for s, board in zip(scored, boards):
        rec = s.job.model_dump(mode="json")
        rec.update(board=board, score=s.score, verdict=s.verdict, reasons=list(s.reasons))
        records[str(s.job.id)] = rec
    path.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "jobs": records},
        indent=2,
    ))
    return path


def load_job_record(path: str | Path, job_id: str) -> dict | None:
    """Return the stored record for ``job_id``, or None if absent."""
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data.get("jobs", {}).get(str(job_id))


def resolve_apply_url(record: dict) -> str | None:
    """The URL an apply flow must open for ``record``: its stored ``apply_url``,
    else its ``url``, VERBATIM — never a URL derived from the company, board, or
    a template. Query strings matter (SmartRecruiters' ``?oga=true`` routes to
    the per-job apply flow); any rewrite lands on the wrong page."""
    return record.get("apply_url") or record.get("url") or None
