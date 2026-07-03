#!/usr/bin/env python
"""One-off utility: probe candidate companies' public ATS boards LIVE.

Not shipped code — a scripts/ helper. For each candidate company it tries the
public no-auth board endpoints across the four supported ATSes with a couple of
slug variants, and reports which board actually resolves (HTTP 200 + non-empty
job list) plus how many of its jobs match the profile keywords.

    python scripts/probe_boards.py            # probe + print the resolver table
    python scripts/probe_boards.py --append   # ...then show a diff and append
                                              # resolvers to search_profile.yaml
                                              # (asks for confirmation first)

Polite by design: 6s timeout, 0.3s delay between requests, one resolving ATS
per company (stops probing once found), errors skip rather than crash.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "search_profile.yaml"

TIMEOUT = 6.0
DELAY = 0.3

# Candidates: company -> slug variants to try, most likely first.
CANDIDATES: dict[str, list[str]] = {
    "databricks": ["databricks"],
    "stripe": ["stripe"],
    "anthropic": ["anthropic"],
    "openai": ["openai"],
    "palantir": ["palantir"],
    "ramp": ["ramp"],
    "scale": ["scaleai", "scale"],
    "cohere": ["cohere"],
    "huggingface": ["huggingface", "hugging-face"],
    "plaid": ["plaid"],
    "brex": ["brex"],
    "coinbase": ["coinbase"],
    "affirm": ["affirm"],
    "robinhood": ["robinhood"],
    "mercury": ["mercury"],
    "notion": ["notion"],
    "figma": ["figma"],
    "vercel": ["vercel"],
    "rippling": ["rippling"],
    "instacart": ["instacart"],
    "doordash": ["doordash", "doordashusa"],
    "reddit": ["reddit"],
    "pinterest": ["pinterest"],
    "cloudflare": ["cloudflare"],
    "discord": ["discord"],
    "gitlab": ["gitlab"],
    "perplexity": ["perplexity", "perplexityai", "perplexity-ai"],
    "airtable": ["airtable"],
    "gusto": ["gusto"],
    "chime": ["chime", "chimefinancial"],
    "snowflake": ["snowflake", "snowflakecomputing"],
    "nvidia": ["nvidia"],
    "samsara": ["samsara"],
    "benchling": ["benchling"],
    "dropbox": ["dropbox"],
}


def _get(url: str, params: dict | None = None):
    """GET politely; None on any failure (skip, never crash)."""
    time.sleep(DELAY)
    try:
        r = httpx.get(url, params=params, timeout=TIMEOUT, follow_redirects=True)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


# Each prober returns a list of job titles, or None if the board doesn't resolve.

def probe_greenhouse(slug: str) -> list[str] | None:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    jobs = (data or {}).get("jobs") or []
    return [j.get("title", "") for j in jobs] or None


def probe_lever(slug: str) -> list[str] | None:
    data = _get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    if not isinstance(data, list) or not data:
        return None
    return [j.get("text", "") for j in data]


def probe_ashby(slug: str) -> list[str] | None:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    jobs = (data or {}).get("jobs") or []
    return [j.get("title", "") for j in jobs] or None


def probe_smartrecruiters(slug: str) -> list[str] | None:
    data = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings")
    jobs = (data or {}).get("content") or []
    return [j.get("name", "") for j in jobs] or None


PROBERS = [
    ("greenhouse", probe_greenhouse),
    ("lever", probe_lever),
    ("ashby", probe_ashby),
    ("smartrecruiters", probe_smartrecruiters),
]


def load_profile() -> dict:
    return yaml.safe_load(PROFILE.read_text()) or {}


def keyword_hits(titles: list[str], keywords: list[str]) -> int:
    kws = [k.lower() for k in keywords]
    return sum(1 for t in titles if any(k in t.lower() for k in kws))


def probe_all(keywords: list[str]) -> list[dict]:
    """Probe every candidate; one row per company."""
    rows = []
    for company, slugs in CANDIDATES.items():
        found = None
        for slug in slugs:
            for ats, prober in PROBERS:
                titles = prober(slug)
                if titles:
                    found = {"company": company, "ats": ats, "slug": slug,
                             "jobs": len(titles), "kw": keyword_hits(titles, keywords)}
                    break
            if found:
                break
        rows.append(found or {"company": company, "ats": None, "slug": None,
                              "jobs": 0, "kw": 0})
        status = (f"{found['ats']:<16} slug={found['slug']:<18} jobs={found['jobs']:<5} "
                  f"kw-matches={found['kw']}" if found and found["ats"] else "— no board resolved")
        print(f"  {company:<14} {status}", flush=True)
    return rows


def append_to_profile(rows: list[dict]) -> None:
    """Show the diff of new sources, confirm, then append (dedup) to the profile."""
    profile = load_profile()
    existing = {(s["ats"], s["board"]) for s in profile.get("sources", [])}
    new = [{"ats": r["ats"], "board": r["slug"]} for r in rows
           if r["ats"] and (r["ats"], r["slug"]) not in existing]
    if not new:
        print("\nNothing to append — every resolver is already in the profile.")
        return
    print("\n--- diff: sources to APPEND to search_profile.yaml ---")
    for s in new:
        print(f"+  - {{ ats: {s['ats']}, board: {s['board']} }}")
    print("--- end diff ---")
    answer = input(f"Append these {len(new)} sources? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        print("Not written.")
        return
    # Append textually to keep the file's comments/formatting intact.
    lines = PROFILE.read_text().splitlines(keepends=True)
    additions = [f"  - {{ ats: {(s['ats'] + ','):<16} board: {s['board']} }}\n" for s in new]
    # find the last existing "  - { ats:" line and insert after it
    last = max(i for i, ln in enumerate(lines) if ln.lstrip().startswith("- { ats:"))
    lines[last + 1:last + 1] = additions
    PROFILE.write_text("".join(lines))
    print(f"Appended {len(new)} sources to {PROFILE}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--append", action="store_true",
                        help="after probing, append resolvers to search_profile.yaml "
                             "(shows a diff and asks first)")
    args = parser.parse_args()

    keywords = load_profile().get("keywords", [])
    print(f"Probing {len(CANDIDATES)} companies "
          f"(keyword check against: {', '.join(keywords)})\n", flush=True)
    rows = probe_all(keywords)

    hits = [r for r in rows if r["ats"]]
    misses = [r["company"] for r in rows if not r["ats"]]
    print(f"\nResolved {len(hits)}/{len(rows)} boards; misses: {', '.join(misses) or 'none'}")

    if args.append:
        append_to_profile(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
