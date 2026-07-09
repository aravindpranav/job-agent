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
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

#: Run statuses (written by run_apply): submitted — really sent; paused —
#: awaiting the human (dry-run, skipped, mid-run); failed — the run crashed.
#: User statuses (set from the dashboard): saved → applied → interviewing →
#: offer / rejected.
Status = Literal["submitted", "paused", "failed",
                 "saved", "applied", "interviewing", "offer", "rejected"]


class ApplicationRecord(BaseModel):
    """One tracked job/application as written to the log."""

    model_config = ConfigDict(frozen=True)

    company: str
    title: str = ""
    job_id: str = ""
    date: str                    # ISO-8601 UTC timestamp of the attempt
    source: str = ""             # ATS the job came from (greenhouse/ashby/...)
    status: Status
    reason: str = ""             # human-readable outcome context
    attempt_id: str = ""         # unique per attempt; assigned by record_attempt
    # user-managed state (edited from the dashboard, never by run_apply)
    notes: str = ""              # recruiter name, why applied, ...
    follow_up: str = ""          # ISO date to follow up by ("" = none)


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


def upsert_job_state(path: str | Path, *, job_id: str, company: str = "",
                     title: str = "", source: str = "", status: Status | None = None,
                     notes: str | None = None, follow_up: str | None = None,
                     now: datetime | None = None) -> ApplicationRecord:
    """Set user-managed state for one job, creating its record if none exists.

    Targets the MOST RECENT record with this ``job_id``; a job never tracked
    before gets a fresh record (default status "saved"). Only the fields
    actually passed are changed — None means "leave as is". Immutable style:
    the file is rewritten with a new records list.
    """
    if not job_id:
        raise ValueError("upsert_job_state needs a non-empty job_id")
    path = Path(path)
    records = load_applications(path)
    idx = next((i for i in range(len(records) - 1, -1, -1)
                if records[i].job_id == job_id), None)
    if idx is None:
        record = ApplicationRecord(
            company=company or "Unknown", title=title, job_id=job_id,
            date=(now or datetime.now(timezone.utc)).isoformat(), source=source,
            status=status or "saved", notes=notes or "", follow_up=follow_up or "",
            attempt_id=uuid.uuid4().hex)
        _write(path, records + [record])
        return record
    updates = {k: v for k, v in
               (("status", status), ("notes", notes), ("follow_up", follow_up))
               if v is not None}
    record = records[idx].model_copy(update=updates)
    _write(path, records[:idx] + [record] + records[idx + 1:])
    return record
