"""Persist a search run so `tailor --job <id>` can pick a job up later.

Written to ``data/last_search.json`` (gitignored). Each record is the normalized
Job plus its board token and score/verdict, keyed by job id.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from job_agent.models import ScoredJob


def save_search(scored: list[ScoredJob], boards: list[str], path: str | Path, *,
                first_seen: dict[str, str] | None = None,
                new_job_ids: tuple[str, ...] = (),
                baseline: bool = False,
                sources_queried: int | None = None) -> Path:
    """Persist scored jobs (parallel to ``boards``) keyed by id, plus scan
    metadata for the dashboard.

    ``first_seen_this_scan`` is an EXPLICIT persisted boolean per record (from
    the seen cache's newly-inserted signal) — never derived from timestamp
    equality. Baseline rule: when the seen cache was empty at scan start there
    is no earlier scan to be "new since", so no record is badged and
    ``new_count`` is stored as null, not zero. An unknown ``sources_queried``
    is omitted entirely (no made-up numbers).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_set = set() if baseline else {str(i) for i in new_job_ids}
    records: dict[str, dict] = {}
    for s, board in zip(scored, boards):
        job_id = str(s.job.id)
        rec = s.job.model_dump(mode="json")
        rec.update(board=board, score=s.score, verdict=s.verdict, reasons=list(s.reasons),
                   first_seen_this_scan=job_id in new_set)
        if first_seen and job_id in first_seen:
            rec["first_seen"] = first_seen[job_id]
        records[job_id] = rec
    meta: dict = {
        "total": len(records),
        "new_count": (None if baseline else
                      sum(1 for r in records.values() if r["first_seen_this_scan"])),
    }
    if sources_queried is not None:
        meta["sources_queried"] = sources_queried
    path.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "meta": meta, "jobs": records},
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
