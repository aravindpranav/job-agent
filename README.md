# job-agent

An AI job-hunting agent. It discovers roles freshly posted on companies' public
Applicant Tracking System (ATS) boards, filters them to what you actually want,
and uses an LLM to score how well each one fits you — then prints a ranked table.

> **Status: Slice 1 of a larger build.** This slice does discovery + scoring.
> Resume tailoring, an application-answer bank, and browser-based application
> assist (with a human-approval gate) are later slices, left as clear stubs.

## Why this exists

Company career pages are backed by a handful of ATS vendors that expose **public,
no-auth JSON APIs**. Instead of scraping aggregators (which violates their terms),
`job-agent` reads these official endpoints directly, normalizes every board into
one shape, keeps only roles posted in the last 24 hours that match your keywords
and location, and spends an LLM call only on those survivors.

**Deliberate constraints:**

- **No scraping of LinkedIn / Indeed / Dice.** Discovery uses only public ATS
  APIs (Greenhouse, Lever, Ashby, SmartRecruiters). There is no "all companies"
  endpoint — discovery is per-company by design.
- **Application submission is never done via ATS APIs** (those submit endpoints
  need the employer's private key). Submission is a later, browser-based slice and
  always stops at a human-approval gate.
- **Secrets and personal data are gitignored** (`.env`, `/data`, resume files).

## Quick start

### Demo mode — no API key, no network

```bash
pip install -e .
python -m job_agent --demo
```

Runs the whole pipeline against bundled mock jobs so anyone can try it instantly.
Demo mode uses the *same* filters and ranking as a real run — only the data
source and the scorer are swapped for offline stand-ins.

```
Pipeline: fetched 6 → keyword 5 → 24h 4 → location 3 → dedup 3
                        Ranked job matches
  #  Score  Verdict   Title                       Company            Location
  1   89    strong    Data Scientist, Growth      Meridian Labs      Austin, TX
  2   77    strong    Senior Data Engineer        Northwind Analytics Remote (US)
  3   77    strong    Machine Learning Engineer…  Cobalt AI          New York, NY
```

### Real run

```bash
cp .env.example .env                                  # add your ANTHROPIC_API_KEY
cp search_profile.example.yaml search_profile.yaml    # edit roles / companies / location
python -m job_agent
```

Fetches live jobs from the boards in `search_profile.yaml`, filters to the last
24 hours, scores each survivor with the LLM, and prints them ranked.

Useful flags: `--profile PATH`, `--limit N`, and `--method {structured,tool}`
(how the model is asked to return JSON — see *Scoring* below).

## How it works

```
sources/ (Greenhouse, Lever, Ashby, SmartRecruiters)
   │  each fetch() -> list[Job]   (coded against real API shapes, not guesses)
   ▼
search.py   keyword pre-filter ─▶ 24h freshness ─▶ location rule ─▶ dedup
   │            every stage's survivor count is reported (no silent truncation)
   ▼
scoring.py  LLM fit score per surviving job  ─▶ ScoredJob {score, verdict, …}
   │
   ▼
cli.py      ranked rich table
```

**Normalization.** Every board becomes one immutable `Job`
(`models.py`): `id, title, company, location, url, apply_url, source,
posted_at` (tz-aware), `remote`, `country`, `description`. Each source was
written against a real captured response — see `tests/fixtures/`.

**Keyword pre-filter first.** Company boards carry hundreds of unrelated roles.
Titles are matched against your keywords *before* anything expensive, so no LLM
call is ever spent on an off-target job.

**24h freshness.** Uses each board's real post date (Greenhouse
`first_published`, Lever `createdAt`, Ashby `publishedAt`, SmartRecruiters
`releasedDate`). For the rare posting with no date, it falls back to a small
seen-ids cache under `/data` ("first observed in the last 24h").

**Location rule.** Keep remote or in-country (US by default) roles; drop known
non-US roles even if remote; keep unknown-country roles for the scorer to weigh.

### Scoring

Scoring uses a low-cost Haiku-class model (`claude-haiku-4-5`, confirmed against
the Anthropic docs, overridable via `JOB_AGENT_MODEL`). The model returns strict
JSON — `{score 0-100, verdict strong|possible|skip, reasons[],
missing_requirements[]}` — via structured outputs (`output_config.format`).
Output is parsed defensively: on malformed JSON it retries once, and if it still
fails the job is kept but marked `unscored` rather than crashing the run.

A **tool-use** path that returns the same JSON as a forced tool call is also
implemented as a reliability fallback (`--method tool`).

## Project layout

```
src/job_agent/
  models.py          Job, ScoredJob (frozen Pydantic v2 models)
  config.py          .env + search_profile.yaml loading & validation
  http.py            shared httpx client (timeout, retries, error mapping)
  sources/           one module per ATS + JobSource base
  search.py          fetch → keyword → 24h → location → dedup
  scoring.py         LLM fit scoring (structured + tool-use paths)
  seen_cache.py      seen-ids cache for the 24h fallback
  demo_data.py       mock jobs + offline scorer for --demo
  cli.py             argument parsing + ranked table
  tailor.py / answers.py / apply.py   later-slice stubs
tests/               pytest + respx (source parsers, filters, scoring)
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests are fully offline: source parsers run against saved fixtures via `respx`,
and the scorer is exercised with a fake client (valid output, retry-then-succeed,
and the unscored fallback).

## Roadmap (later slices)

2. Resume tailoring → tailored PDF (`tailor.py`)
3. Application answer bank, reading real ATS questions (`answers.py`)
4. Human-approval gate, then browser-based application assist (`apply.py`)

## License

MIT
