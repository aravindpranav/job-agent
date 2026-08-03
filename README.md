# job-agent

An AI job-hunting agent. It discovers roles freshly posted on companies' public
Applicant Tracking System (ATS) boards, filters them to what you actually want,
and uses an LLM to score how well each one fits you — then prints a ranked table.

> **Status: Slices 1–4 shipped, plus a local dashboard and a Chrome extension.**
> Slice 1 does discovery + scoring; Slice 2 tailors your résumé to a matched job
> as an ATS-safe PDF, gated by a bidirectional no-drift honesty check; Slice 3
> adds a validated answer bank; Slice 4 does browser-based assisted apply that
> fills the real form but submits only behind an explicit `--submit` flag *and*
> your per-application approval. On top of those: a local web dashboard
> (search / tailor / track / apply), a Chrome MV3 extension that fills forms in
> your own logged-in browser, an application tracker, and a board-token
> discovery utility.

## Why this exists

Company career pages are backed by a handful of ATS vendors that expose **public,
no-auth JSON APIs**. Instead of scraping aggregators (which violates their terms),
`job-agent` reads these official endpoints directly, normalizes every board into
one shape, keeps only recently posted roles (default: the last 30 days) that match
your keywords and location, and spends an LLM call only on those survivors.

**Deliberate constraints:**

- **No scraping of LinkedIn / Indeed / Dice.** Discovery has two modes, both
  official public APIs: **per-company** ATS board endpoints (Greenhouse, Lever,
  Ashby, SmartRecruiters) enumerated from the board list in your profile, and
  **cross-company** query sources (SmartRecruiters search, Remotive, RemoteOK)
  that return jobs from many companies per keyword. Greenhouse/Lever/Ashby have
  no cross-company index, so the `discover` subcommand grows the board list by
  validating candidate company tokens against those same official APIs.
- **Application submission is never done via ATS APIs** (those submit endpoints
  need the employer's private key). Submission is browser-based and always stops
  at a human-approval gate.
- **Secrets and personal data are gitignored** (`.env`, `/data`, resume files).

## Quick start

The CLI has six subcommands — `search`, `tailor`, `apply`, `applications`,
`dashboard`, and `discover`. A bare invocation with no subcommand defaults to
`search`.

### Demo mode — no API key, no network

```bash
pip install -e .
playwright install chromium            # one-time, only needed for CLI `apply` and
                                       # the dashboard's controlled-window flow
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
Pipeline: fetched 6 → keyword 5 → recency(30d) 5 → location 4 → seniority 4 → dedup 4 → experience 4
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
python -m job_agent dashboard                         # local web UI + extension backend
python -m job_agent applications                      # the tracked log of every attempt
python -m job_agent discover                          # grow the board list (see below)
```

`search` fetches live jobs from the boards in `search_profile.yaml`, filters to
the recency window (default 30 days), scores each survivor, prints them ranked
— hiding jobs you already applied to (`--include-applied` shows them) — and
saves the run to `data/last_search.json`. `tailor --job <ID>` (an ID from that
table) re-fetches the full JD, tailors your base résumé to it, runs the no-drift
gate, and writes `data/output/<company>_<role>.pdf` (+ `.docx`) plus a NOTES
block to review. `apply --job <ID>` opens that job's application in a
**visible** browser, fills it from your answer bank + tailored PDF, and shows a
full review — see below.

Useful search flags: `--profile PATH`, `--limit N`, `--days N` (recency
window, default 30; `--max-age-hours N` overrides it for sub-day windows),
`--include-applied`, `--method {structured,tool}`.

## How it works

```
sources/ (Greenhouse, Lever, Ashby, SmartRecruiters
          + cross-company: sr-search, Remotive, RemoteOK)
   │  each fetch() -> list[Job]   (coded against real API shapes, not guesses)
   ▼
search.py   keyword ─▶ recency ─▶ location ─▶ seniority ─▶ dedup ─▶ experience
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

**Recency window (default 30 days, `--days`).** Uses each board's real post date (Greenhouse
`first_published`, Lever `createdAt`, Ashby `publishedAt`, SmartRecruiters
`releasedDate`). For the rare posting with no date, it falls back to a small
seen-ids cache under `/data` ("first observed within the window").

**Location rule.** Keep remote or in-country (US by default) roles; drop known
non-US roles even if remote; keep unknown-country roles for the scorer to weigh.
When a board leaves the country blank, it's inferred from the location text
(`geo.py`) so a clearly-foreign posting ("Bengaluru, India") is dropped here too.

**Seniority + experience filters (optional).** Two profile knobs keep
over-level roles out of results entirely — dropped before scoring, like the
location rule, not merely downranked:

- `max_seniority` (e.g. `senior`) drops titles ranked above it — Lead, Staff,
  Principal, Director, VP (`seniority.py`, title-only, runs before dedup).
- `experience_years` (e.g. `5`) drops a job whose JD *unambiguously requires*
  clearly more — a hard-cued minimum ("required", "must", "minimum", "at least")
  of `experience_years + 3` or higher. Soft phrasings never drop: "8+ years
  preferred (or equivalent)" and bare figures survive; "8 years required" goes
  for a 5-year candidate (`experience.py`, runs after enrichment so every
  source's full JD is present).

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

### Growing the board list (`discover`)

Greenhouse, Lever, and Ashby have **no** public cross-company index (verified
live: their board APIs 404/401 without a company token, and there is no public
token directory). `discover` widens coverage the only permitted way: it takes a
text file of candidate company names (`data/candidate_companies.txt`), derives
token guesses ("Modern Treasury" → `moderntreasury`, `modern-treasury`), probes
each against the official per-board APIs at ~2 requests/second, caches every
verdict so re-runs are free, and writes the validated entries in
`search_profile.yaml` format to `data/output/discovered_boards.yaml`. It never
edits your profile — you merge the entries you want by hand.

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
- **No-drift gate (`verify.py`) — bidirectional.** Before any PDF is written,
  the output is checked against the career facts in **both directions**: a
  fabricated or altered employer, an uncredentialed cert, or a metric number
  with no basis in the facts **fails the build loudly** — and so does an
  **omitted real employer** (every employer in the facts must appear; dropping
  a role misrepresents the career exactly like inventing one). A separate
  **scope-qualifier gate** rejects scale inflation ("multi-terabyte",
  "enterprise-scale", "firm-wide") unless the exact phrase appears in the facts
  — checked on the face *and* re-checked on the rendered artifact.
- **Professional, ATS-safe output.** A clean single-column `.docx` is the source
  of truth: large bold name with contact beneath, CAPS section headings under a
  thin rule, bold company names with **right-aligned dates**, role titles in
  *italics*, real `•` bullets, Calibri, no em-dashes. Skills stay pipe-free
  `Category: value` lines in the gated text; the renderer lays them out as a
  **two-column borderless table** in the `.docx` and PDF. The **PDF is produced
  from the `.docx` with LibreOffice** so the two match exactly (falls back to a
  bundled-font reportlab renderer if LibreOffice isn't installed). A format gate
  rejects brackets, pipes, em-dashes, company-blurb project descriptions,
  over-cap bullet counts, or missing certs; the output is then fitted to 3 pages
  and its text **extracted back out** and asserted selectable with sections in
  order. A PDF that fails extraction is a failed build.

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
- **Screening questions: grounded drafts, never unreviewed.** Unfilled questions
  are routed (`screening.py`): *factual* → answer bank/career facts only (blank +
  flagged if absent, no LLM); *consent/EEO* → always pauses; *free-text* ("why
  us?", "describe a project") → a Haiku draft grounded ONLY in your career facts +
  answer bank + this JD, run through a no-fabrication gate (unknown employers,
  unbanked metrics, "I've used their product" claims → regenerate once, else
  `[GATE-FLAGGED]`). Drafts appear at the review tagged `[AI-DRAFT]` /
  `[NEEDS-INPUT]` / `[GATE-FLAGGED]`, are editable inline, and can never be
  auto-approved. Approved answers are cached (gitignored) and re-reviewed on
  repeat questions. Factual yes/no questions the career facts settle explicitly
  come back tagged `[GROUNDED]` with the grounding fact shown ("Yes — JPMorgan:
  deployed ML models to production…") — veto-first, see the extension section.

Scope: Greenhouse / Lever / Ashby embedded forms are fully fillable; for Workday
/ iCIMS it fills what's public then pauses for you to log in — it never attempts
account creation. Playwright drives the browser (`playwright install chromium`
once). The pure logic (classification, mapping, gates, blocker detection) is
fully unit-tested with no browser; only the thin driver touches Playwright.

## Dashboard + Chrome extension

`python -m job_agent dashboard` serves a local web UI (**127.0.0.1 only**, not
configurable — it fronts personal data with no auth layer). It is also the
backend the Chrome extension talks to.

**The dashboard** shows the last search as a ranked table with each job's
tracked state joined in, and drives the same CLI code paths:

- **Search / Tailor / Resume** buttons run the real pipeline and show the output.
- **Application tracker** (`apply/tracker.py`, gitignored
  `data/applications.json`): per-job status (saved / applied / interviewing /
  offer / rejected), notes, and follow-up dates. Jobs with an in-flight
  application are hidden from search results by default, in both the CLI and
  the UI. A one-click **Mark applied / Undo** works whether or not autofill
  ever ran.
- **Apply** opens the job's stored apply URL in **your own Chrome** (real
  profile, extensions loaded — `open -a "Google Chrome"`, falling back to your
  default browser) and queues a fill task for the extension; the extension's
  fill status reports back into the panel. No automation-controlled window in
  this path. The previous Playwright-assisted flow — separate visible window,
  in-UI review gate, explicit submit — is still available as **"Open controlled
  window instead"**.

**The Chrome MV3 extension** (`extension/`) fills Greenhouse / Ashby / Lever
application forms in your own logged-in browser, from the same answer bank +
career facts, via the local backend only (CORS admits chrome-extension origins
exclusively; pin yours with `JOB_AGENT_EXTENSION_ID`). Same non-negotiables as
the CLI flow: **never submits, never touches consent/legal boxes, never evades
bot detection, never talks to any external server**. The popup groups results
into *filled / drafts to review / needs your answer / you must confirm*; AI
drafts and `[GROUNDED]` facts-backed yes/no answers (each shown with the fact
that grounds it) are inserted only by your explicit click. **Load instructions,
setup, and the manual test checklist: [extension/README.md](extension/README.md).**

## Project layout

```
src/job_agent/
  models.py          Job, ScoredJob (frozen Pydantic v2 models)
  config.py          .env + search_profile.yaml loading & validation
  http.py            shared httpx client (timeout, retries, error mapping)
  sources/           one module per ATS + JobSource base
                     (greenhouse, lever, ashby, smartrecruiters
                      + cross-company: sr_search, remotive, remoteok)
  search.py          fetch → keyword → recency → location → seniority → dedup → experience
  geo.py             infer a country from free-text location (US-vs-foreign)
  seniority.py       title → seniority level (for the max_seniority filter)
  experience.py      required years-of-experience parsed from a JD
  scoring.py         LLM fit scoring (structured + tool-use paths)
  seen_cache.py      seen-ids cache for the no-post-date fallback
  demo_data.py       mock jobs + offline scorer for search --demo
  store.py           persist a search run for `tailor --job`; resolve apply URLs
  discovery.py       board-token discovery (probe official ATS APIs, cached)
  cli.py             search / tailor / apply / applications / dashboard / discover
  tailor/
    extract.py       base resume (.docx) -> career_facts.yaml
    career_facts.py  frozen CareerFacts models + allow-lists
    tailor.py        mega prompt + facts + JD -> Sonnet -> resume + NOTES
    render_pdf.py    ATS-safe PDF + editable .docx (two-column skills layout)
    verify.py        bidirectional no-drift gate + scope gate + PDF text gate
    textnorm.py      normalization shared by rendering and the no-drift gate
    jd_fetch.py      re-fetch the full JD at tailor time
    demo/            committed FAKE facts / JD / stub response
  apply/
    answer_bank.py   frozen answer-bank models + load/validate; contact merged
                     from career_facts (Slice 3)
    fields.py        immutable FormField / FillPlan value types
    form_reader.py   read + classify a form's controls (pure classify + DOM scan)
    filler.py        pure answer-bank -> FillPlan mapping; apply plan to the page
    grounded.py      facts-grounded yes/no answers ([GROUNDED], veto-first)
    screening.py     question routing + honesty-gated essay drafts
    review.py        human review gate (approve / edit / skip); blocks on missing
    handoff.py       pause/resume for login / captcha / account (pure detection)
    submit.py        two-lock submit gate + screenshot + JSONL log
    tracker.py       application log: statuses, notes, applied-jobs markers
    runner.py        orchestrates one application end to end
    browser.py       lazy Playwright launch (visible for real runs)
    prompt_io.py     console-IO seam so the gates are testable offline
    demo_apply.py    the `apply --demo` offline flow
    demo/            committed local fake form + fake answers + fake resume
  dashboard/
    app.py           FastAPI app: jobs/track/tailor/apply routes (127.0.0.1)
    service.py       thin service layer over the CLI's own functions
    apply_session.py live assisted-apply session (the CLI review gate over HTTP)
    extension_api.py the extension's endpoints (fill-values, task hand-off)
    static/          the dashboard UI (single index.html)
extension/           Chrome MV3 extension (see extension/README.md)
prompts/tailor_megaprompt.txt           the tailoring mega prompt
tests/               pytest + respx (sources, filters, scoring, tailoring, PDF,
                     answer bank, apply: mapping / review / handoff / submit,
                     tracker, dashboard, extension API, grounded answers,
                     discovery)
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
- ✅ Slice 2 — résumé tailoring → ATS-safe PDF with a bidirectional no-drift
  honesty gate
- ✅ Slice 3 — application answer bank (`apply/answer_bank.py`): validated,
  gitignored PII store; work-auth required, EEO opt-in/declinable, contact merged
  from career facts. Template: `data/answer_bank.example.yaml`.
- ✅ Slice 4 — assisted apply in a visible browser (Playwright): fills from the
  bank + tailored PDF, pauses on login/captcha/unknown fields, shows a full
  review, and submits only behind `--submit` + per-application approval. Runs
  end to end offline via `apply --demo` against a local fake form.
- ✅ Local dashboard — search / tailor / track / apply from a web UI bound to
  127.0.0.1, with an application tracker and applied-jobs hiding.
- ✅ Chrome MV3 extension — fills forms in your own logged-in browser via the
  local backend; dashboard hand-off (open in Chrome, fill status reported back).
- ✅ Grounded yes/no answers — factual questions the career facts settle
  explicitly, tagged `[GROUNDED]` with the fact shown, veto-first.
- ✅ Board-token discovery (`discover`) — grow the Greenhouse/Lever/Ashby board
  list from candidate company names via the official APIs.

## License

MIT
