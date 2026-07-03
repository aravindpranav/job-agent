# job-agent

An AI job-hunting agent. It discovers roles freshly posted on companies' public
Applicant Tracking System (ATS) boards, filters them to what you actually want,
and uses an LLM to score how well each one fits you — then prints a ranked table.

> **Status: Slices 1–4 of a larger build.** Slice 1 does discovery + scoring;
> Slice 2 tailors your résumé to a matched job as an ATS-safe PDF, gated by a
> no-drift honesty check; Slice 3 adds a validated answer bank; Slice 4 does
> browser-based assisted apply that fills the real form but submits only behind
> an explicit `--submit` flag *and* your per-application approval.

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

The CLI has three subcommands, `search`, `tailor`, and `apply`. A bare
invocation with no subcommand defaults to `search`.

### Demo mode — no API key, no network

```bash
pip install -e .
playwright install chromium            # one-time, only needed for `apply`
python -m job_agent search --demo    # discover + score (mock jobs)
python -m job_agent tailor --demo    # tailor a FAKE resume to a FAKE JD -> sample PDF
python -m job_agent apply  --demo    # fill + "submit" a LOCAL fake form, end to end
```

`search --demo` runs the whole discovery pipeline against bundled mock jobs
(same filters/ranking as a real run, offline stand-ins for the source + scorer).
`tailor --demo` tailors a committed fake resume to a fake job and writes an
ATS-safe sample PDF + DOCX. `apply --demo` opens a committed local HTML form over
`file://` (zero network, no real employer), fills it from a fake answer bank +
fake resume, prints the full review, pauses for a simulated approval, "submits"
to the local page, and saves a confirmation screenshot — the entire assisted-apply
flow with no real-world side effects.

```
Pipeline: fetched 6 → keyword 5 → 24h 4 → location 3 → seniority 3 → dedup 3 → experience 3
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
cp data/answer_bank.example.yaml data/answer_bank.yaml # fill your real apply answers
python -m job_agent search                            # fetch, filter, score, rank
python -m job_agent tailor --job <ID>                 # tailor your resume to a match
python -m job_agent apply  --job <ID>                 # assisted apply (dry-run by default)
python -m job_agent apply  --job <ID> --submit        # real submit (still needs your OK)
```

`search` fetches live jobs from the boards in `search_profile.yaml`, filters to
the last 24 hours, scores each survivor, prints them ranked, and saves the run to
`data/last_search.json`. `tailor --job <ID>` (an ID from that table) re-fetches
the full JD, tailors your base résumé to it, runs the no-drift gate, and writes
`data/output/<company>_<role>.pdf` (+ `.docx`) plus a NOTES block to review.
`apply --job <ID>` opens that job's application in a **visible** browser, fills
it from your answer bank + tailored PDF, and shows a full review — see below.

Useful search flags: `--profile PATH`, `--limit N`, `--max-age-hours N`
(freshness window, default 24), `--method {structured,tool}`.

## How it works

```
sources/ (Greenhouse, Lever, Ashby, SmartRecruiters)
   │  each fetch() -> list[Job]   (coded against real API shapes, not guesses)
   ▼
search.py   keyword ─▶ 24h freshness ─▶ location ─▶ seniority ─▶ dedup ─▶ experience
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
When a board leaves the country blank, it's inferred from the location text
(`geo.py`) so a clearly-foreign posting ("Bengaluru, India") is dropped here too.

**Seniority + experience filters (optional).** Two profile knobs keep
over-level roles out of results entirely — dropped before scoring, like the
location rule, not merely downranked:

- `max_seniority` (e.g. `senior`) drops titles ranked above it — Lead, Staff,
  Principal, Director, VP (`seniority.py`, title-only, runs before dedup).
- `experience_years` (e.g. `5`) drops a job whose JD *requires* clearly more —
  a stated minimum of `experience_years + 3` or higher, so "8+ years" goes for a
  5-year candidate while "6+"/reachable ranges stay (`experience.py`, runs after
  enrichment so every source's full JD is present).

Both are off when unset, so existing profiles are unaffected. The scorer then
only ranks roles that already fit your level and years.

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

## Assisted apply (Slice 4)

`apply` opens a job's real application form in a **visible** browser, fills it
from your answer bank + tailored PDF, shows you everything it will submit, and
submits **only** with both a `--submit` flag *and* your explicit approval. It is
built to be cautious by construction — the safety rules live in the code, not
just the docs:

- **Two independent locks on submit.** A real submission needs `--submit` on the
  command line **and** an in-session `approve` at the review gate. Missing either
  → dry-run or skipped, never sent (`submit.py:submit_block_reason`). Default is
  preview/dry-run. One approval submits exactly one application — there is no
  batch path.
- **Never guesses an answer.** Fields are filled only from your answer bank or
  résumé (`filler.py` is a pure `fields → FillPlan` function). An unmatched or
  ambiguous field is recorded as *unfilled with a reason* and surfaced in the
  review; a required one blocks approval until you `edit` it in or `skip`.
- **Never handles credentials or captchas.** It never creates accounts, types
  passwords, or solves captchas. On a login / account-creation / captcha it
  **pauses**, tells you what to do in the *same* browser window, waits for you to
  do it yourself, then re-checks the page is clear and resumes from where it
  paused — no reload, no lost state (`handoff.py`).
- **Full review before anything is sent.** The review prints every value *and its
  source* (e.g. `career_facts.email`, `answer_bank.salary_expectation`) plus every
  field left empty, then waits for `approve` / `edit <sel>=<val>` / `skip`
  (`review.py`). On a real submit it captures a confirmation screenshot and
  appends the outcome to `data/apply/apply_log.jsonl`.

Scope: Greenhouse / Lever / Ashby embedded forms are fully fillable; for Workday
/ iCIMS it fills what's public then pauses for you to log in — it never attempts
account creation. Playwright drives the browser (`playwright install chromium`
once). The pure logic (classification, mapping, gates, blocker detection) is
fully unit-tested with no browser; only the thin driver touches Playwright.

## Project layout

```
src/job_agent/
  models.py          Job, ScoredJob (frozen Pydantic v2 models)
  config.py          .env + search_profile.yaml loading & validation
  http.py            shared httpx client (timeout, retries, error mapping)
  sources/           one module per ATS + JobSource base
  search.py          fetch → keyword → 24h → location → seniority → dedup → experience
  geo.py             infer a country from free-text location (US-vs-foreign)
  seniority.py       title → seniority level (for the max_seniority filter)
  experience.py      required years-of-experience parsed from a JD
  scoring.py         LLM fit scoring (structured + tool-use paths)
  seen_cache.py      seen-ids cache for the 24h fallback
  demo_data.py       mock jobs + offline scorer for search --demo
  store.py           persist a search run for `tailor --job`
  cli.py             search / tailor / apply subcommands
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
                     from career_facts (Slice 3)
    fields.py        immutable FormField / FillPlan value types
    form_reader.py   read + classify a form's controls (pure classify + DOM scan)
    filler.py        pure answer-bank -> FillPlan mapping; apply plan to the page
    review.py        human review gate (approve / edit / skip); blocks on missing
    handoff.py       pause/resume for login / captcha / account (pure detection)
    submit.py        two-lock submit gate + screenshot + JSONL log
    runner.py        orchestrates one application end to end
    browser.py       lazy Playwright launch (visible for real runs)
    prompt_io.py     console-IO seam so the gates are testable offline
    demo/            committed local fake form + fake answers + fake resume
prompts/tailor_megaprompt.txt           the tailoring mega prompt
tests/               pytest + respx (sources, filters, scoring, tailoring, PDF,
                     answer bank, apply: mapping / review / handoff / submit)
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
- ✅ Slice 4 — assisted apply in a visible browser (Playwright): fills from the
  bank + tailored PDF, pauses on login/captcha/unknown fields, shows a full
  review, and submits only behind `--submit` + per-application approval. Runs
  end to end offline via `apply --demo` against a local fake form.

## License

MIT
