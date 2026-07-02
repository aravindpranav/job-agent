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
