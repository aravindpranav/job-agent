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
import re
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


def _find_job(records: list[ApplicationRecord], job_id: str,
              company: str, title: str) -> int | None:
    """Index of the record tracking this JOB: exact id first, then the
    (company, title) match-key — sr-search re-issues ids across runs, so the
    same posting must be recognized under a new id."""
    if job_id:
        idx = next((i for i in range(len(records) - 1, -1, -1)
                    if records[i].job_id == str(job_id)), None)
        if idx is not None:
            return idx
    if company and title:
        key = (_match_key(company), _match_key(title))
        return next((i for i in range(len(records) - 1, -1, -1)
                     if (_match_key(records[i].company),
                         _match_key(records[i].title)) == key), None)
    return None


def record_attempt(path: str | Path, record: ApplicationRecord) -> str:
    """Track one attempt — ONE record per job, never a duplicate row.

    A new attempt on an already-tracked job (matched by id, else by
    company+title) REPLACES that record: the new attempt's status/date/id win,
    the user's notes and follow-up survive. Returns the unique attempt id.
    """
    path = Path(path)
    records = load_applications(path)
    attempt_id = record.attempt_id or uuid.uuid4().hex
    stamped = record.model_copy(update={"attempt_id": attempt_id})
    idx = _find_job(records, record.job_id, record.company, record.title)
    if idx is None:
        _write(path, records + [stamped])
    else:
        merged = stamped.model_copy(update={"notes": records[idx].notes,
                                            "follow_up": records[idx].follow_up})
        _write(path, records[:idx] + [merged] + records[idx + 1:])
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


#: A job with one of these statuses has an application in flight — search views
#: hide it (re-applying would be a duplicate). "rejected"/"saved"/"paused" stay
#: visible: nothing was successfully applied, or the human may want to retry.
APPLIED_STATUSES = frozenset({"applied", "submitted", "interviewing", "offer"})


def _match_key(text: str) -> str:
    """Loose identity for cross-run matching: lowercase, alnum words only."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def applied_markers(path: str | Path) -> dict:
    """The identity sets of every in-flight application, for exclusion checks.

    ``ids`` match exactly; ``pairs`` are (company, title) match-keys so the same
    role is recognized across search runs even when the source re-issues ids
    (sr-search does).
    """
    ids: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for r in load_applications(path):
        if r.status in APPLIED_STATUSES:
            if r.job_id:
                ids.add(str(r.job_id))
            if r.company and r.title:
                pairs.add((_match_key(r.company), _match_key(r.title)))
    return {"ids": ids, "pairs": pairs}


def is_already_applied(markers: dict, *, job_id: str, company: str, title: str) -> bool:
    """True when this job already has an in-flight application (id or re-match)."""
    if str(job_id) in markers["ids"]:
        return True
    return bool(company and title
                and (_match_key(company), _match_key(title)) in markers["pairs"])


def upsert_job_state(path: str | Path, *, job_id: str = "", attempt_id: str = "",
                     company: str = "", title: str = "", source: str = "",
                     status: Status | None = None, notes: str | None = None,
                     follow_up: str | None = None,
                     now: datetime | None = None) -> ApplicationRecord:
    """Set user-managed state for one job, creating its record if none exists.

    Targets the MOST RECENT record with this ``job_id`` — or, for records that
    carry no job id (old/manual entries), the record with ``attempt_id``. A job
    never tracked before gets a fresh record (default status "saved"). Only the
    fields actually passed are changed — None means "leave as is". Immutable
    style: the file is rewritten with a new records list.
    """
    if not job_id and not attempt_id:
        raise ValueError("upsert_job_state needs a job_id or an attempt_id")
    path = Path(path)
    records = load_applications(path)
    if attempt_id and not job_id:
        idx = next((i for i, r in enumerate(records)
                    if r.attempt_id == attempt_id), None)
        if idx is None:
            raise KeyError(f"no record with attempt_id {attempt_id!r}")
    else:
        # id first, then company+title — the same job under a re-issued id
        # must update its existing row, never grow a duplicate
        idx = _find_job(records, job_id, company, title)
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
