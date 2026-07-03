# job-agent

An AI job-hunting agent. It discovers roles freshly posted on companies' public
Applicant Tracking System (ATS) boards, filters them to what you actually want,
and uses an LLM to score how well each one fits you — then prints a ranked table.

> **Status: Slices 1–2 of a larger build.** Slice 1 does discovery + scoring;
> Slice 2 tailors your résumé to a matched job as an ATS-safe PDF, gated by a
> no-drift honesty check. An application-answer bank and browser-based
> application assist (with a human-approval gate) are later slices, left as stubs.

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

The CLI has two subcommands, `search` and `tailor`. A bare invocation with no
subcommand defaults to `search`.

### Demo mode — no API key, no network

```bash
pip install -e .
python -m job_agent search --demo    # discover + score (mock jobs)
python -m job_agent tailor --demo    # tailor a FAKE resume to a FAKE JD -> sample PDF
```

`search --demo` runs the whole discovery pipeline against bundled mock jobs
(same filters/ranking as a real run, offline stand-ins for the source + scorer).
`tailor --demo` tailors a committed fake resume to a fake job and writes an
ATS-safe sample PDF + DOCX — so anyone can see the tailoring end-to-end with no
API key and no real résumé.

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
python -m job_agent search                            # fetch, filter, score, rank
python -m job_agent tailor --job <ID>                 # tailor your resume to a match
```

`search` fetches live jobs from the boards in `search_profile.yaml`, filters to
the last 24 hours, scores each survivor, prints them ranked, and saves the run to
`data/last_search.json`. `tailor --job <ID>` (an ID from that table) re-fetches
the full JD, tailors your base résumé to it, runs the no-drift gate, and writes
`data/output/<company>_<role>.pdf` (+ `.docx`) plus a NOTES block to review.

Useful search flags: `--profile PATH`, `--limit N`, `--max-age-hours N`
(freshness window, default 24), `--method {structured,tool}`.

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

## Résumé tailoring (Slice 2)

`tailor` turns a matched job into an **ATS-safe PDF** tailored to that JD, plus a
NOTES block — and it is built around an **honesty gate**: it can rewrite emphasis
and wording, but it cannot fabricate.

- **Immutable career facts.** The base résumé (`.docx`) is parsed once into
  `data/career_facts.yaml` (gitignored) — the source of truth. Company names,
  titles, and durations are fixed; the tailoring model is constrained to them.
- **No invented metrics.** Only real numbers from the career facts are cited,
  woven into achievements. If a role has no real metric, a specific *qualitative*
  achievement is written instead — the résumé face never carries a `[METRIC …]`
  placeholder or bracket. Suggestions to add a real figure go in the NOTES block
  only, phrased as questions you can answer with a true number.
- **No invented certs/skills.** Only real certifications print; JD-valued certs you
  lack go to NOTES as "suggested to obtain". Gaps are flagged, never faked.
- **No-drift gate (`verify.py`).** Before any PDF is written, the output is checked
  against the career facts: altered/added employers, uncredentialed certs, and
  metric numbers with no basis in the facts **fail the build loudly**.
- **Professional, ATS-safe output.** A clean single-column `.docx` is the source
  of truth: large bold name with contact beneath, CAPS section headings under a
  thin rule, bold company names with **right-aligned dates**, role titles in
  *italics*, `Category: value` skills (no tables/pipes), real `•` bullets,
  Calibri, no em-dashes. The **PDF is produced from the `.docx` with LibreOffice**
  so the two match exactly (falls back to a bundled-font reportlab renderer if
  LibreOffice isn't installed). A format gate rejects brackets, pipes, em-dashes,
  company-blurb project descriptions, over-cap bullet counts, or missing certs;
  the output is then fitted to 2 pages and its text **extracted back out** and
  asserted selectable with sections in order. A PDF that fails extraction is a
  failed build.

Tailoring uses **`claude-sonnet-4-6`** for quality; scoring stays on Haiku.

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
  demo_data.py       mock jobs + offline scorer for search --demo
  store.py           persist a search run for `tailor --job`
  cli.py             search / tailor subcommands
  tailor/
    extract.py       base resume (.docx) -> career_facts.yaml
    career_facts.py  frozen CareerFacts models + allow-lists
    tailor.py        mega prompt + facts + JD -> Sonnet -> resume + NOTES
    render_pdf.py    ATS-safe PDF + editable .docx
    verify.py        no-drift gate + PDF text-extraction gate
    jd_fetch.py      re-fetch the full JD at tailor time
    demo/            committed FAKE facts / JD / stub response
  apply/
    answer_bank.py   frozen answer-bank models + load/validate; contact merged
                     from career_facts (Slice 3). Browser modules land here next.
  answers.py / apply.py                 Slice-4 browser-automation stubs
prompts/tailor_megaprompt.txt           the tailoring mega prompt
tests/               pytest + respx (sources, filters, scoring, tailoring, PDF)
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests are fully offline: source parsers run against saved fixtures via `respx`,
and the scorer is exercised with a fake client (valid output, retry-then-succeed,
and the unscored fallback).

## Roadmap

- ✅ Slice 1 — discovery + LLM fit scoring
- ✅ Slice 2 — résumé tailoring → ATS-safe PDF with a no-drift honesty gate
- ✅ Slice 3 — application answer bank (`apply/answer_bank.py`): validated,
  gitignored PII store; work-auth required, EEO opt-in/declinable, contact merged
  from career facts. Template: `data/answer_bank.example.yaml`.
- ⬜ Slice 4 — assisted apply in a visible browser (Playwright): fill from the
  bank, pause on login/captcha/unknown fields, full review, submit only behind
  `--submit` + per-application approval.

## License

MIT
