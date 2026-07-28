# Job Agent Autofill — Chrome extension

Fills job application forms on **Greenhouse, Ashby, and Lever** from your local
job-agent profile (answer bank + career facts), JobRight-style — but running
entirely on your machine, in your own logged-in browser.

**What it never does (non-negotiable, same rules as the CLI apply flow):**

- **Never submits.** There is no code path that clicks a submit control — you
  review the page and press the form's own button yourself.
- **Never fills consent/legal boxes.** The backend structurally refuses to
  return a value for them (`[PAUSED: consent]`); they show under "You must
  confirm".
- **Never evades bot detection.** It works because it runs as you, in your
  browser. No fingerprint spoofing, no captcha solving.
- **Never talks to any external server.** The only network target is
  `http://127.0.0.1:<port>` — your own backend. Profile data stays local.

## How it works

```
job page (Greenhouse/Ashby/Lever)
  └─ content script: scans the form            (content/scan.js — the same
     ↓ raw field descriptors + page context     scan the Python flow runs)
  background service worker
  └─ POST http://127.0.0.1:8642/api/extension/fill-values
     ↓ resolved values (existing answer-bank / career-facts / EEO logic)
     ↓ + AI drafts for essay questions (existing screening drafter)
     ↓ + which tailored PDF to attach (a hint — never an upload)
  content script fills the visible fields      (content/fill.js)
  popup shows: filled / drafts to review / needs your answer / you must confirm
  YOU review the page and click the form's own submit button
```

The backend endpoint reuses `build_fill_plan` and the screening drafter — the
extension adds **no new answer logic** and stores **no profile data** of its own.

### The resume field (why it can't be automatic)

Browsers do not let extensions put a file into an `<input type="file">` — a
file chooser only opens from a real user click, by design, so no page or
extension can silently attach files from your disk. So the extension:

- shows the resume field under **Needs your answer** with a **"Show me the
  field"** button that scrolls to it and highlights it;
- names the file to pick — the backend recommends your tailored PDF from
  `data/output/` (the one matching the company when it can tell, otherwise the
  most recently tailored one) with its full path.

You click the field, pick that file, done.

### Essay drafts (never entered unreviewed)

With `ANTHROPIC_API_KEY` configured in the backend's `.env`, free-text
questions ("describe a project…") are drafted by the **existing** screening
drafter — grounded in your career facts and the page text, with the same
honesty rules as the CLI: "why do you want to work at X" comes back marked
**needs your input** rather than a fabricated reason, and drafts that fail the
fact-check gate come back flagged. Drafts appear ONLY in the popup, in an
editable box — nothing reaches the form until you've read it and clicked
**"Put it in the form"** (or Copy). Without an API key, essay questions simply
show under "Needs your answer".

## Setup

1. **Start the backend** (it binds 127.0.0.1 only):

   ```bash
   python -m job_agent dashboard          # default port 8642
   ```

   You need `data/career_facts.yaml` and `data/answer_bank.yaml`, same as the
   CLI apply flow.

2. **Load the extension** (Chrome / Edge / Brave):

   1. Open `chrome://extensions`
   2. Turn on **Developer mode** (top right)
   3. Click **Load unpacked** and pick this `extension/` directory
   4. Pin "Job Agent Autofill" to the toolbar

3. **(Recommended) Pin the extension ID.** After loading, copy the extension's
   ID from `chrome://extensions` and start the backend with it, so ONLY your
   copy of the extension may call the API:

   ```bash
   JOB_AGENT_EXTENSION_ID=<your 32-char id> python -m job_agent dashboard
   ```

   Without it, any `chrome-extension://` origin is admitted (web pages never
   are, either way).

4. If your backend runs on a non-default port, set it in the popup's footer.

## Using it

1. Open a job's **application page** (the one with the actual form) on
   `boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.ashbyhq.com`, or
   `jobs.lever.co`.
2. Click the toolbar icon → **Fill this form**.
3. Read the three groups in the popup:
   - **Auto-filled for you** — entered on the page; check each looks right.
   - **Needs your answer** — no saved answer, or a control that needs a human
     (file uploads, options that don't match your banked wording).
   - **You must confirm** — consent/legal boxes; the extension never touches
     them.
4. Finish the leftovers on the page and click the form's **own** submit button.

## Manual test checklist (extension JS has no automated harness)

Backend running, extension loaded:

- [ ] **Ping**: popup header shows "backend connected" (green). Stop the
      backend, reopen the popup — "backend not running" (red).
- [ ] **Greenhouse**: open any live posting, e.g.
      `https://job-boards.greenhouse.io/gitlab/jobs/<id>` → Fill this form →
      name/email/phone/LinkedIn fill visibly; Yes/No dropdowns (sponsorship
      etc.) show the banked answer; consent-worded questions stay on
      "Select..." and appear under "You must confirm".
- [ ] **Ashby**: `https://jobs.ashbyhq.com/<company>/<job>/application` →
      text fields fill; Yes/No toggle questions appear (filled or under
      "needs your answer" — never mis-clicked).
- [ ] **Lever**: `https://jobs.lever.co/<company>/<id>/apply` → same checks.
- [ ] **Dropdown aliases**: with "USA" in the answer bank, a Country dropdown
      whose options say "United States" (or "United States +1") still fills.
      A question whose options genuinely don't match (e.g. it wants a state)
      pauses and the reason lists the options it saw — never a guess.
- [ ] **Type-to-search dropdowns** (e.g. School): the widget is opened, the
      saved value (and its alias spellings) typed as search queries, and the
      matching option clicked; commitment is verified before "filled" is
      claimed. A widget that doesn't take the click reports "clicked … but the
      form didn't take it".
- [ ] **Why no draft**: with drafting off (no key), the popup's "Drafts to
      review" group says exactly why ("no ANTHROPIC_API_KEY loaded in the
      backend…") instead of silently showing nothing.
- [ ] **Resume guidance**: on a form with a resume field, the popup shows
      "Attach your resume" with your tailored PDF's name/path; "Show me the
      field" scrolls to and highlights the input; clicking it yourself opens
      the file chooser. The extension never attaches the file itself.
- [ ] **Essay drafts** (backend has `ANTHROPIC_API_KEY`): on a form with a
      free-text question, the popup shows "Drafts to review" with an editable
      draft; the form field on the page is EMPTY until you click "Put it in
      the form"; the inserted text is exactly what's in the box (edits
      included). A "why this company?" question comes back marked as needing
      your own words, not a fabricated answer.
- [ ] **No key, no drafts**: stop the backend, remove the key, restart — the
      same essay question appears under "Needs your answer" instead.
- [ ] **Never submits**: after filling, the page's submit button is untouched
      and no navigation happened.
- [ ] **Wrong page**: click Fill on the job DESCRIPTION page (no form) → clear
      "no application form found" message, nothing filled.
- [ ] **Unsupported site**: on any other site the popup says the page isn't a
      supported job board.
- [ ] **Stale tab recovery**: open a job page, then reload the extension at
      `chrome://extensions` WITHOUT reloading the tab — "Fill this form" must
      still work (the popup re-injects its content script and retries; no
      manual tab reload needed).
- [ ] **CORS**: from a normal website's devtools console,
      `fetch("http://127.0.0.1:8642/api/extension/ping")` is blocked by CORS;
      from the extension's service-worker console it succeeds.

Backend behavior (automated): `python -m pytest tests/test_extension_api.py`
covers value resolution, consent/credential pausing, radio grouping, payload
validation, and the CORS policy.

## Adding a 4th ATS

1. Add its application-page host to `host_permissions` AND the content-script
   `matches` in `manifest.json`.
2. Append one module to `content/ats.js` with the board's hostname(s) and a
   `formRoot()` that returns the application-form container.
3. If its widgets need special handling, extend `content/fill.js` — everything
   else (scan, resolution, safety rules) is shared.

## Files

```
manifest.json        MV3 manifest — minimal permissions
background.js        service worker: the ONLY code that calls the backend
content/ats.js       ATS registry (greenhouse / ashby / lever)
content/scan.js      form scan — port of the Python form_reader scan
content/fill.js      fills values; refuses consent, files, near-miss options
content/main.js      orchestration + popup messaging
popup/               the review UI (plain-language groups)
```
