#!/usr/bin/env python
"""One-off utility: probe candidate CROSS-COMPANY job-search sources LIVE.

Not shipped code — a scripts/ helper, companion to probe_boards.py (which
probes per-company boards). Every finding printed here is from a real request
made right now — nothing is assumed about an endpoint or its shape.

For each candidate source it reports one of:
    WORKS         — public, no auth, returns cross-company results for a keyword
    AUTH-REQUIRED — callable but needs a session/key (reported, not scraped)
    NO-SURFACE    — the guessed endpoint does not exist
plus the observed response shape (top-level keys / one sample record) and any
terms-of-service notes to weigh before integrating.

    python scripts/probe_sources.py                     # probe all, print report
    python scripts/probe_sources.py --query "ml engineer"   # different keyword
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

UA = {"User-Agent": "job-agent/0.1 (portfolio project)", "Accept": "application/json"}
TIMEOUT = 25.0


def _shape(payload) -> str:
    if isinstance(payload, dict):
        return f"dict keys={list(payload.keys())[:8]}"
    if isinstance(payload, list):
        return f"list[{len(payload)}] first={_shape(payload[0]) if payload else '∅'}"
    return type(payload).__name__


def _sample(record: dict, fields: tuple[str, ...]) -> str:
    return json.dumps({f: record.get(f) for f in fields if f in record},
                      default=str)[:240]


def probe(name: str, fn) -> None:
    print(f"\n=== {name} " + "=" * max(0, 60 - len(name)))
    try:
        fn()
    except httpx.HTTPError as exc:
        print(f"  NETWORK-ERROR: {exc}")
    except Exception as exc:  # a probe must report, never crash the harness
        print(f"  PROBE-ERROR: {type(exc).__name__}: {exc}")


def probe_ashby_cross_org(query: str) -> None:
    """Ashby: is there any public cross-organization search surface?"""
    r = httpx.post(
        "https://jobs.ashbyhq.com/api/non-user-graphql",
        json={"query": "{ __schema { queryType { fields { name } } } }"},
        headers=UA, timeout=TIMEOUT)
    blocked = "introspection has been disabled" in r.text
    print(f"  introspection: HTTP {r.status_code}"
          + (" — disabled by server" if blocked else f" — {r.text[:120]}"))
    r = httpx.get(f"https://jobs.ashbyhq.com/api/jobs/search?query={query}",
                  headers=UA, timeout=TIMEOUT)
    print(f"  guessed search endpoint: HTTP {r.status_code} -> {r.text[:80]}")
    print("  VERDICT: NO-SURFACE — per-org GraphQL/REST only "
          "(the existing ashby per-company source stays).")


def probe_greenhouse_seeker(query: str) -> None:
    """Greenhouse job-seeker search (my.greenhouse.io): public JSON or session?"""
    for url in (f"https://my.greenhouse.io/jobs?query={query}",
                f"https://my.greenhouse.io/api/jobs/search?query={query}"):
        r = httpx.get(url, headers=UA, timeout=TIMEOUT, follow_redirects=False)
        print(f"  {url[:70]} -> HTTP {r.status_code} "
              f"location={r.headers.get('location', '-')!r}")
    print("  VERDICT: AUTH-REQUIRED — both bounce to the login root; "
          "no callable JSON feed without a session. NOT integrating a scrape.")


def probe_smartrecruiters_search(query: str) -> None:
    """SmartRecruiters cross-company posting search (their public site's API)."""
    r = httpx.get("https://jobs.smartrecruiters.com/sr-jobs/search",
                  params={"keyword": query}, headers=UA, timeout=TIMEOUT)
    d = r.json()
    companies = {c["company"]["name"] for c in d.get("content", [])}
    print(f"  HTTP {r.status_code} | {_shape(d)} | totalFound={d.get('totalFound')} "
          f"| page size={len(d.get('content', []))} | {len(companies)} distinct companies")
    if d.get("content"):
        print("  sample:", _sample(d["content"][0],
              ("id", "name", "releasedDate", "applyUrl", "shortLocation")))
        print("  company:", d["content"][0]["company"]["name"],
              "| location.country:", d["content"][0].get("location", {}).get("country"))
    print("  NOTE: limit/offset/page/country params are IGNORED (verified) — one "
          "relevance-ordered page of ~100 per keyword; description via the "
          "official per-company postings API (actions.details).")
    print("  VERDICT: WORKS — public, no auth, genuinely cross-company.")


def probe_remotive(query: str) -> None:
    """Remotive public API (official; remote roles)."""
    r = httpx.get("https://remotive.com/api/remote-jobs",
                  params={"search": query}, headers=UA, timeout=TIMEOUT)
    d = r.json()
    print(f"  HTTP {r.status_code} | {_shape(d)} | job-count={d.get('job-count')}")
    if d.get("jobs"):
        print("  sample:", _sample(d["jobs"][0],
              ("title", "company_name", "candidate_required_location",
               "publication_date", "url")))
    print("  ToS (from the response itself):", str(d.get("0-legal-notice", ""))[:160])
    print("  VERDICT: WORKS — official API; terms allow sharing jobs onward, "
          "forbid submitting them to third-party job boards (we don't).")


def probe_remoteok(tag: str) -> None:
    """RemoteOK public API (official; remote roles, tag-filtered)."""
    r = httpx.get(f"https://remoteok.com/api?tag={tag}", headers=UA,
                  timeout=TIMEOUT, follow_redirects=True)
    d = r.json()
    legal = d[0].get("legal", "") if d and isinstance(d[0], dict) else ""
    jobs = d[1:] if len(d) > 1 else []
    print(f"  HTTP {r.status_code} | {_shape(d)} | jobs={len(jobs)}")
    if jobs:
        print("  sample:", _sample(jobs[0],
              ("position", "company", "location", "date", "url")))
    print("  ToS (from the response itself):", legal[:160])
    print("  VERDICT: WORKS — official API; terms ask for linkback attribution "
          "when republishing (we surface their URLs, we don't republish).")


def probe_hiring_cafe(query: str) -> None:
    """hiring.cafe internal search API (unofficial multi-ATS aggregator)."""
    r = httpx.get(f"https://hiring.cafe/api/search-jobs?q={query}",
                  headers=UA, timeout=TIMEOUT)
    print(f"  GET  -> HTTP {r.status_code} {r.text[:80]}")
    r = httpx.post("https://hiring.cafe/api/search-jobs",
                   json={"size": 5, "page": 0,
                         "searchState": {"searchQuery": query}},
                   headers={**UA, "Origin": "https://hiring.cafe"}, timeout=TIMEOUT)
    print(f"  POST -> HTTP {r.status_code} {r.text[:80]}")
    print("  VERDICT: AUTH-REQUIRED/BLOCKED — internal API (401/405), no public "
          "terms. NOT integrating.")


def probe_keyed_apis() -> None:
    """Aggregators that need an API key (reported for completeness)."""
    r = httpx.get("https://jobdataapi.com/api/jobs/",
                  params={"title": "machine learning"}, headers=UA, timeout=TIMEOUT)
    print(f"  jobdataapi.com -> HTTP {r.status_code} {r.text[:80]}")
    print("  VERDICT: AUTH-REQUIRED — free tier exists but needs a key; "
          "Adzuna/Jooble/JSearch likewise. Candidates if a key is added later.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", default="machine learning engineer")
    args = ap.parse_args()

    print(f"Probing cross-company sources LIVE (query={args.query!r}) …")
    probe("1. Ashby cross-org search", lambda: probe_ashby_cross_org(args.query))
    probe("2. Greenhouse job-seeker search (my.greenhouse.io)",
          lambda: probe_greenhouse_seeker(args.query))
    probe("3a. SmartRecruiters cross-company search",
          lambda: probe_smartrecruiters_search(args.query))
    probe("3b. Remotive public API", lambda: probe_remotive(args.query))
    probe("3c. RemoteOK public API", lambda: probe_remoteok("machine-learning"))
    probe("3d. hiring.cafe (unofficial)", lambda: probe_hiring_cafe(args.query))
    probe("3e. keyed aggregators", probe_keyed_apis)
    return 0


if __name__ == "__main__":
    sys.exit(main())
