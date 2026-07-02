# job-agent

An AI job-hunting agent. It discovers roles freshly posted on companies' public
Applicant Tracking System (ATS) boards, filters them to what you actually want,
and uses an LLM to score how well each one fits you — then prints a ranked table.

> **Status: Slice 1 of a larger build.** This slice does discovery + scoring.
> Resume tailoring, an application-answer bank, and browser-based application
> assist (with a human-approval gate) are later slices, left as clear stubs.

## Why this exists

Company career pages are backed by a handful of ATS vendors that expose **public,
no-auth JSON APIs**. Instead of scraping aggregators (which is against their
terms), `job-agent` reads these official endpoints directly, normalizes every
board into one shape, keeps only roles posted in the last 24 hours that match
your keywords and location, and spends an LLM call only on those survivors.

**Deliberate constraints:**

- **No scraping of LinkedIn / Indeed / Dice.** Discovery uses only public ATS
  APIs (Greenhouse, Lever, Ashby, SmartRecruiters). There is no "all companies"
  endpoint — discovery is per-company by design.
- **Application submission is never done via ATS APIs** (those need the
  employer's private key). Submission is a later, browser-based slice and always
  stops at a human-approval gate.
- **Secrets and personal data are gitignored** (`.env`, `/data`, resume files).

## Quick start

### Demo mode — no API key, no network

```bash
pip install -e .
python -m job_agent --demo
```

Runs end-to-end against bundled mock jobs so anyone can try it instantly.

### Real run

```bash
cp .env.example .env                       # add your ANTHROPIC_API_KEY
cp search_profile.example.yaml search_profile.yaml   # edit roles / companies / location
python -m job_agent
```

Fetches live jobs from the boards in `search_profile.yaml`, filters to the last
24 hours, scores them, and prints them ranked.

## Architecture

```
sources/ (Greenhouse, Lever, Ashby, SmartRecruiters)
   │  each fetch() -> list[Job]   (coded against real API shapes)
   ▼
search.py   keyword pre-filter ─▶ 24h freshness ─▶ location rule ─▶ dedup
   │            (prunes before spending any LLM call)
   ▼
scoring.py  Haiku fit score per surviving job  ─▶ ScoredJob {score, verdict, ...}
   │
   ▼
cli.py      ranked rich table
```

<!-- Finalized in commit 5: model choice, structured-output note, per-module tour, demo gif. -->

## License

MIT
