/* Content-script entry point: the popup asks, this orchestrates.
 *
 *   popup "FILL_FORM" → detect ATS → scan the form (scan.js, same scan the
 *   Python flow runs) → background worker resolves values from the local
 *   backend → fill.js enters them → grouped result back to the popup.
 *
 * NEVER submits: there is no code path here that clicks a submit control —
 * the human reviews the page and presses the real button themselves.
 */
"use strict";

/* AI-drafted and facts-grounded answers are NEVER entered by this script:
 * they go to the popup for the human to read, veto/edit, and explicitly
 * insert. */
var JA_DRAFT_TAGS = ["[AI-DRAFT]", "[NEEDS-INPUT]", "[GATE-FLAGGED]", "[GROUNDED]"];  // var: re-injection safe

/* Page context for the backend's screening drafter: the company slug is the
 * first path segment on all three boards, the JD is the page's own text. */
function JA_pageContext() {
  return {
    company: decodeURIComponent(location.pathname.split("/").filter(Boolean)[0] || ""),
    jd: (document.body.innerText || "").slice(0, 25000),
  };
}

async function JA_run(context) {
  const ats = JA_ATS.detect();
  if (!ats) {
    return { error: "This page isn't a supported job board (Greenhouse, Ashby, Lever)." };
  }
  const root = ats.formRoot();
  const raw = JA_scanForm(root);
  if (!raw.length) {
    return { error: "No application form found on this page — open the job's " +
                    "application page (with the actual form) and try again." };
  }

  // Dashboard-queued context (real company name + full JD) grounds the
  // drafter better than page scraping; fall back to the page per field.
  const page = JA_pageContext();
  const plan = await chrome.runtime.sendMessage(
    { type: "RESOLVE_FIELDS", fields: raw,
      company: (context && context.company) || page.company,
      jd: (context && context.jd) || page.jd });
  if (!plan || plan.error) {
    return { error: (plan && plan.error) || "The local job-agent backend didn't respond." };
  }

  const drafts = (plan.planned || []).filter((p) => JA_DRAFT_TAGS.includes(p.tag));
  const toFill = (plan.planned || []).filter((p) => !JA_DRAFT_TAGS.includes(p.tag));
  const { filled, failed } = await JA_fillPlan(root, toFill);
  const paused = (plan.unfilled || []).filter((u) => u.tag.startsWith("[PAUSED"));
  const missing = (plan.unfilled || []).filter((u) => !u.tag.startsWith("[PAUSED"));

  const fileSelectors = new Set(raw.filter((r) => r.type === "file")
                                   .map((r) => r.selector));
  const mark = (e) => ({
    ...e, isFile: e.kind === "file" || fileSelectors.has(e.selector) });

  return {
    ats: ats.id,
    filled,                                   // entered on the page, ready to review
    drafts,                                   // review/edit in the popup, insert by hand
    needsYou: [...missing, ...failed].map(mark),
    confirm: paused,                          // consent & credentials — human only
    missingRequired: plan.missing_required || 0,
    resume: plan.resume || null,              // which file to attach (human click)
    drafting: plan.drafting || null,          // whether/why essay drafting ran
  };
}

/* Scroll to a field and flash it — for "show me the resume field". Browsers
 * only open a file chooser on a real user click, so this points, never picks. */
function JA_reveal(selector) {
  const el = document.querySelector(selector);
  if (!el) return { error: "field not found on the page anymore" };
  const target = el.offsetParent === null ? (el.closest("label") || el.parentElement || el) : el;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  const prev = target.style.outline;
  target.style.outline = "3px solid #6366F1";
  setTimeout(() => { target.style.outline = prev; }, 2500);
  try { el.focus(); } catch (e) { /* non-focusable container */ }
  return { ok: true };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || !msg.type) return undefined;
  if (msg.type === "FILL_FORM") {
    JA_run(msg.context || null)
      .then(sendResponse)
      .catch((e) => sendResponse({ error: String(e && e.message || e) }));
    return true;   // async response
  }
  if (msg.type === "FILL_ONE") {   // a human-reviewed value from the popup
    const ats = JA_ATS.detect();
    JA_fillOne(ats ? ats.formRoot() : document,
               { selector: msg.selector, value: msg.value })
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, reason: String(e && e.message || e) }));
    return true;
  }
  if (msg.type === "REVEAL_FIELD") {
    sendResponse(JA_reveal(msg.selector));
    return undefined;
  }
  return undefined;
});
