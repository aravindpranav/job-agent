"""The source interface and shared normalization helpers.

Each ATS gets its own module implementing :class:`JobSource`. ``fetch()`` returns
lightweight :class:`Job` objects (cheap, from the board's list endpoint).
``enrich()`` is an optional per-job second call used only for keyword survivors —
SmartRecruiters needs it to pull the full description; the others don't override
it.
"""

from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from job_agent.geo import infer_country
from job_agent.models import Job

# Cap description length so scoring stays cheap and within context.
MAX_DESCRIPTION_CHARS = 2000

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")



class JobSource(ABC):
    """Fetches and normalizes one board into :class:`Job` objects."""

    #: ATS identifier, e.g. "greenhouse". Set by each subclass.
    ats: str = "base"

    def __init__(self, board: str) -> None:
        self.board = board

    @abstractmethod
    def fetch(self) -> list[Job]:
        """Return all postings on the board, normalized. Never raises for an
        expected fetch failure — return ``[]`` and let the caller log it."""

    def enrich(self, job: Job) -> Job:
        """Optionally fetch extra detail for a single job (default: no-op)."""
        return job


def strip_html(raw: str) -> str:
    """Turn HTML-ish source text into readable plain text.

    Intentionally simple (unescape entities, drop tags, collapse whitespace) —
    good enough for feeding a description to the scorer, not a full parser.
    """
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def truncate(text: str, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    """Trim overly long descriptions, marking that they were cut."""
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def guess_us_country(location: str | None) -> str | None:
    """Best-effort: return "US" when a free-text location clearly reads US, else
    None. Used only where the board has no structured country field.

    Delegates to :func:`job_agent.geo.infer_country` — a former loose regex here
    (``,\\s*[A-Z]{2}``) read "London, UK" / "Budapest, BU" as US and stamped
    that as the job's structured country, which BYPASSED the location stage's
    own (correct) inference. One brain for geography, not two.
    """
    return "US" if infer_country(location) == "US" else None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (with offset or trailing 'Z') to a tz-aware
    datetime. Returns None if the value is missing or unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def epoch_ms_to_dt(value: int | float | None) -> datetime | None:
    """Convert epoch milliseconds (Lever's ``createdAt``) to a UTC datetime."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
