"""Application tracking: a gitignored JSON log of every apply attempt.

``data/applications.json`` (already covered by the ``/data/*`` gitignore — it
holds personal job history) records one entry per ``run_apply`` attempt:
company, role, job id, ISO date, source ATS, and a status that resolves to
``submitted`` / ``paused`` / ``failed`` when the run completes.

Everything is immutable in the codebase style: records are frozen models and
updates rewrite the file with a new list rather than mutating in place. A
missing or corrupt log reads as empty — tracking must never break an apply run.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

#: submitted — the form was really sent; paused — awaiting the human (dry-run,
#: skipped, or mid-run); failed — the run crashed before completing.
Status = Literal["submitted", "paused", "failed"]


class ApplicationRecord(BaseModel):
    """One apply attempt as written to the log."""

    model_config = ConfigDict(frozen=True)

    company: str
    title: str = ""
    job_id: str = ""
    date: str                    # ISO-8601 UTC timestamp of the attempt
    source: str = ""             # ATS the job came from (greenhouse/ashby/...)
    status: Status
    reason: str = ""             # human-readable outcome context
    attempt_id: str = ""         # unique per attempt; assigned by record_attempt


def load_applications(path: str | Path) -> list[ApplicationRecord]:
    """Read the log; a missing, unreadable, or corrupt file is an empty log."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
        return [ApplicationRecord.model_validate(r) for r in raw]
    except (OSError, json.JSONDecodeError, ValidationError):
        return []


def _write(path: Path, records: list[ApplicationRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([r.model_dump() for r in records], indent=2))


def record_attempt(path: str | Path, record: ApplicationRecord) -> str:
    """Append one attempt to the log; returns its unique attempt id."""
    path = Path(path)
    attempt_id = record.attempt_id or uuid.uuid4().hex
    stamped = record.model_copy(update={"attempt_id": attempt_id})
    _write(path, load_applications(path) + [stamped])
    return attempt_id


def update_status(path: str | Path, attempt_id: str, status: Status,
                  reason: str = "") -> None:
    """Set the final status of one attempt (new records list — no mutation)."""
    path = Path(path)
    records = [
        r.model_copy(update={"status": status, "reason": reason or r.reason})
        if r.attempt_id == attempt_id else r
        for r in load_applications(path)
    ]
    _write(path, records)
