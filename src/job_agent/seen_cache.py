"""A tiny on-disk cache of when we first saw each job.

Most boards expose a real post date, so the 24h filter uses that directly. But
some jobs (e.g. a Greenhouse posting with a null ``first_published``) have no
usable date. For those, we approximate "posted in the last 24h" with "first
observed in the last 24h": the first time we see a dateless job we record the
timestamp; on later runs we compare against it.

The cache is a small JSON file under the data dir (gitignored). It is the only
mutable-state component in the pipeline, so it's kept deliberately simple.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class SeenCache:
    """Maps ``"<source>:<id>"`` to the ISO timestamp we first observed it."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._first_seen: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._first_seen = json.loads(self.path.read_text())
            except (ValueError, OSError):
                # A corrupt cache should never break a run — start fresh.
                self._first_seen = {}

    @staticmethod
    def _key(source: str, job_id: str) -> str:
        return f"{source}:{job_id}"

    def observe(self, source: str, job_id: str, now: datetime) -> datetime:
        """Return when this job was first seen, recording ``now`` if it's new."""
        key = self._key(source, job_id)
        if key not in self._first_seen:
            self._first_seen[key] = now.isoformat()
        return datetime.fromisoformat(self._first_seen[key])

    def save(self) -> None:
        """Persist the cache, creating the data dir if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._first_seen, indent=2, sort_keys=True))
