"""Board-token discovery for Greenhouse / Lever / Ashby.

These ATSs expose NO public cross-company index (verified live 2026-07-20:
greenhouse boards-api without a token -> 404, Ashby posting-api without an org
-> 401, no usable sitemaps). The only permitted way to widen the board list is
to probe candidate tokens against the official public per-board APIs — exactly
what they exist to serve — politely: ~2 requests/second and a cache so re-runs
cost nothing.

Verified probe semantics (live, 2026-07-21):
    greenhouse  valid -> 200 {"jobs": [...]}     invalid -> 404 JSON
    ashby       valid -> 200 {"jobs": [...]}     invalid -> 404 plain text
    lever       valid -> 200 [ ...postings ]     invalid -> 404 JSON
429 / 5xx / network errors are TRANSIENT: reported, never cached, so a
rate-limit blip can't permanently mark a real board as missing.

Output is search_profile.yaml `sources:` syntax; this module never edits the
profile itself — the human merges.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx
from pydantic import BaseModel, ConfigDict

from job_agent.http import USER_AGENT

ATS_ORDER = ("greenhouse", "lever", "ashby")   # probed in prevalence order

_PROBE_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
}

REQUEST_INTERVAL = 0.5   # seconds between network probes (~2 req/s)
PROBE_TIMEOUT = 15.0

# Trailing corporate suffixes that are never part of a board token.
_SUFFIXES = {"inc", "llc", "ltd", "corp", "corporation", "company"}


class BoardHit(BaseModel):
    """One validated (candidate name -> ats/token) mapping."""

    model_config = ConfigDict(frozen=True)

    name: str
    ats: str
    token: str


class DiscoveryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    hits: tuple[BoardHit, ...]
    transient: tuple[str, ...]   # probes that errored (429/5xx/network) — retryable
    probed: int                  # network requests actually made this run


def token_variants(name: str) -> list[str]:
    """Candidate board tokens for a company name.

    Lowercase, '&' -> 'and', punctuation folded to word breaks, trailing
    corporate suffixes dropped; then the joined ("moderntreasury") and
    hyphenated ("modern-treasury") forms, deduped in order.
    """
    folded = "".join(c if c.isalnum() else " " for c in name.lower().replace("&", " and "))
    words = folded.split()
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words = words[:-1]
    if not words:
        return []
    variants = ["".join(words)]
    if len(words) > 1:
        variants.append("-".join(words))
    seen: set[str] = set()
    return [v for v in variants if not (v in seen or seen.add(v))]


def probe_url(ats: str, token: str) -> str:
    return _PROBE_URLS[ats].format(token=token)


def looks_valid(ats: str, status: int | None, body: object) -> bool | None:
    """True = valid board; False = definitively absent; None = transient/unknown."""
    if status is None:
        return None
    if status == 200:
        if ats == "lever":
            return isinstance(body, list)
        return isinstance(body, dict) and isinstance(body.get("jobs"), list)
    if 400 <= status < 500 and status != 429:
        return False
    return None


def default_fetcher(url: str) -> tuple[int | None, object]:
    """GET one probe URL. Returns (status, parsed-json-or-None); (None, None)
    on network failure so the caller treats it as transient, never negative."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        resp = httpx.get(url, headers=headers, timeout=PROBE_TIMEOUT)
    except httpx.HTTPError:
        return None, None
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body


class DiscoveryCache:
    """On-disk cache of definitive probe verdicts, keyed ``"{ats}:{token}"``.

    Positives and negatives are both cached (re-runs are free); transient
    outcomes are never written. Saved after every update so a long interrupted
    run loses nothing.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._verdicts: dict[str, bool] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
                self._verdicts = {k: bool(v["valid"]) for k, v in raw.items()}
            except (ValueError, TypeError, KeyError, OSError):
                self._verdicts = {}   # a corrupt cache never breaks a run

    def get(self, ats: str, token: str) -> bool | None:
        return self._verdicts.get(f"{ats}:{token}")

    def set(self, ats: str, token: str, valid: bool) -> None:
        self._verdicts[f"{ats}:{token}"] = valid
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {k: {"valid": v} for k, v in sorted(self._verdicts.items())}, indent=2))


def discover_boards(
    candidates: list[str],
    *,
    cache: DiscoveryCache,
    fetcher: Callable[[str], tuple[int | None, object]] = default_fetcher,
    sleep: Callable[[float], None] | None = None,
    on_hit: Callable[[BoardHit], None] | None = None,
) -> DiscoveryResult:
    """Probe every candidate name; return validated boards + transient errors.

    Per company: ATS in prevalence order, variants in derivation order, first
    validation wins and ends that company's probing (a company runs one ATS).
    Blank lines and ``#`` comments in the input are ignored.
    """
    if sleep is None:
        import time
        sleep = time.sleep

    hits: list[BoardHit] = []
    transient: list[str] = []
    probed = 0

    for raw in candidates:
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        found: BoardHit | None = None
        for ats in ATS_ORDER:
            for token in token_variants(name):
                verdict = cache.get(ats, token)
                if verdict is None:
                    sleep(REQUEST_INTERVAL)
                    status, body = fetcher(probe_url(ats, token))
                    probed += 1
                    verdict = looks_valid(ats, status, body)
                    if verdict is None:
                        transient.append(f"{ats}/{token}: HTTP {status} (retryable, not cached)")
                        continue
                    cache.set(ats, token, verdict)
                if verdict:
                    found = BoardHit(name=name, ats=ats, token=token)
                    break
            if found:
                break
        if found:
            hits.append(found)
            if on_hit:
                on_hit(found)

    return DiscoveryResult(hits=tuple(hits), transient=tuple(transient), probed=probed)


def format_yaml_block(hits: tuple[BoardHit, ...] | list[BoardHit]) -> str:
    """Render hits as search_profile.yaml `sources:` entries (same alignment)."""
    return "\n".join(
        f"  - {{ ats: {(h.ats + ','):<17}board: {h.token} }}" for h in hits)
