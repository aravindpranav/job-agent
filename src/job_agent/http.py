"""A small shared HTTP helper for the ATS sources.

All boards are public GET endpoints. This centralizes the timeout, a couple of
retries on transient failures, a descriptive User-Agent, and error mapping so
each source doesn't reinvent it. Failures raise :class:`SourceError`, which the
sources catch and turn into "this board contributed no jobs" rather than
crashing the whole run.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

USER_AGENT = "job-agent/0.1 (+https://github.com/; portfolio project)"
DEFAULT_TIMEOUT = 20.0
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.5
# Transient statuses worth a retry; 4xx (except 429) are not retried.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """A board could not be fetched (network, HTTP, or malformed JSON)."""


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    _sleep=time.sleep,
) -> Any:
    """GET ``url`` and return parsed JSON, retrying transient failures.

    Raises :class:`SourceError` on any unrecoverable problem.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:  # connection/timeout/etc.
            last_error = exc
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise SourceError(f"{url} returned non-JSON body") from exc
            if resp.status_code not in RETRYABLE_STATUS:
                raise SourceError(f"{url} -> HTTP {resp.status_code}")
            last_error = SourceError(f"{url} -> HTTP {resp.status_code}")

        if attempt < MAX_ATTEMPTS:
            _sleep(BACKOFF_SECONDS * attempt)

    raise SourceError(f"{url} failed after {MAX_ATTEMPTS} attempts: {last_error}")
