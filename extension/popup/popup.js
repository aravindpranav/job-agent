/* Popup: one button, plain-language results. All rendering uses textContent —
 * field labels come from arbitrary web pages and are never treated as HTML. */
"use strict";

const $ = (s) => document.querySelector(s);
const DEFAULT_PORT = 8642;
let TAB_ID = null;      // the job-board tab this popup filled
let TASK = null;        // dashboard-queued fill task, if this tab matches it

const DRAFT_NOTES = {
  "[AI-DRAFT]": "AI draft from your saved facts — check it before it goes in.",
  "[NEEDS-INPUT]": "The assistant can't answer this honestly for you (e.g. " +
                   "“why this company”) — write it in your own words.",
  "[GATE-FLAGGED]": "The draft failed a fact-check — rewrite it before using.",
  "[GROUNDED]": "Answered from your saved career facts — veto it if it's wrong.",
};

function li(cls, label, detail, why) {
  const item = document.createElement("li");
  item.className = cls;
  const l = document.createElement("div");
  l.className = "lbl";
  l.textContent = label;
  item.appendChild(l);
  if (detail) {
    const v = document.createElement("div");
    v.className = "val";
    v.textContent = detail;
    item.appendChild(v);
  }
  if (why) {
    const w = document.createElement("div");
    w.className = "why";
    w.textContent = why;
    item.appendChild(w);
  }
  return item;
}

function renderList(ul, items, build) {
  ul.textContent = "";
  if (!items.length) {
    const none = document.createElement("div");
    none.className = "empty";
    none.textContent = "none";
    ul.appendChild(none);
    return;
  }
  items.forEach((it) => ul.appendChild(build(it)));
}

/* A file field: extensions cannot attach files (browser security), so this
 * points the human at the field — and for the resume field, names the
 * recommended tailored PDF to pick. */
function isResumeField(f) {
  return /resume|\bcv\b/i.test(`${f.label} ${f.selector}`);
}

function fileLi(f, resume) {
  const title = isResumeField(f)
    ? "Attach your resume — click the field on the page"
    : `Attach a file yourself — ${f.label || "see the page"}`;
  const item = li("file", title, "",
    "browsers don't let extensions attach files, so this one is yours");
  if (!isResumeField(f)) resume = null;   // recommend the PDF only for the resume
  if (resume) {
    const rec = document.createElement("div");
    rec.className = "rec";
    const b = document.createElement("b");
    b.textContent = resume.name;
    rec.append(resume.matched_company
      ? "Pick this file (tailored for this company): "
      : "No resume tailored for this company yet — this is your most recent one: ",
      b, document.createElement("br"));
    const path = document.createElement("span");
    path.className = "why";
    path.textContent = resume.path;
    rec.appendChild(path);
    item.appendChild(rec);
  } else if (isResumeField(f)) {
    const rec = document.createElement("div");
    rec.className = "rec why";
    rec.textContent = "No tailored resume found yet — run Tailor in the " +
      "dashboard first, or pick any resume from your files.";
    item.appendChild(rec);
  }
  const row = document.createElement("div");
  row.className = "drow";
  const btn = document.createElement("button");
  btn.className = "small go";
  btn.textContent = "Show me the field";
  btn.addEventListener("click", () =>
    chrome.tabs.sendMessage(TAB_ID, { type: "REVEAL_FIELD", selector: f.selector }));
  row.appendChild(btn);
  item.appendChild(row);
  return item;
}

/* One drafted answer: read → edit → explicitly insert. Never auto-entered. */
function draftLi(f) {
  const item = li("d", f.label || f.selector);
  const note = document.createElement("div");
  note.className = "dnote";
  note.textContent = DRAFT_NOTES[f.tag] || "check this before it goes in";
  item.appendChild(note);
  if (f.tag === "[GROUNDED]" && f.source) {
    // show the grounding fact so the veto decision is informed:
    // "Yes — JPMorgan Chase & Co.: Deployed ML models to production …"
    const why = document.createElement("div");
    why.className = "why";
    why.textContent = f.source.replace(/^grounded-fact:\s*/, "");
    item.appendChild(why);
  }
  const box = document.createElement("textarea");
  box.value = f.value || "";
  item.appendChild(box);
  const row = document.createElement("div");
  row.className = "drow";
  const insert = document.createElement("button");
  insert.className = "small go";
  insert.textContent = "Put it in the form";
  const copy = document.createElement("button");
  copy.className = "small";
  copy.textContent = "Copy";
  const state = document.createElement("span");
  state.className = "dstate";
  insert.addEventListener("click", async () => {
    const r = await chrome.tabs.sendMessage(TAB_ID,
      { type: "FILL_ONE", selector: f.selector, value: box.value })
      .catch(() => ({ ok: false, reason: "the page tab is gone — reload it" }));
    state.textContent = r && r.ok ? "✓ in the form" : (r && r.reason) || "could not insert";
    state.style.color = r && r.ok ? "" : "var(--red)";
  });
  copy.addEventListener("click", async () => {
    await navigator.clipboard.writeText(box.value);
    state.textContent = "copied";
  });
  row.append(insert, copy, state);
  item.appendChild(row);
  return item;
}

function renderResult(r) {
  $("#results").classList.remove("hidden");
  $("#n-filled").textContent = `(${r.filled.length})`;
  $("#n-drafts").textContent = `(${(r.drafts || []).length})`;
  $("#n-needs").textContent = `(${r.needsYou.length})`;
  $("#n-confirm").textContent = `(${r.confirm.length})`;
  renderList($("#list-filled"), r.filled,
    (f) => li("f", f.label || f.selector, f.value));
  // show the drafts group whenever drafting is OFF too — with the reason,
  // so "no draft" is never a mystery
  const draftingOff = r.drafting && r.drafting.enabled === false;
  $("#group-drafts").classList.toggle("hidden",
    !(r.drafts || []).length && !draftingOff);
  renderList($("#list-drafts"), r.drafts || [], draftLi);
  if (draftingOff) {
    const note = document.createElement("div");
    note.className = "empty";
    note.textContent = "Essay drafting is off: " + r.drafting.reason;
    $("#list-drafts").replaceChildren(note);
  }
  renderList($("#list-needs"), r.needsYou,
    (f) => f.isFile ? fileLi(f, r.resume)
                    : li("n", f.label || f.selector, "", f.reason || ""));
  renderList($("#list-confirm"), r.confirm,
    (f) => li("c", f.label || f.selector, "", "read and answer this on the page"));
  const s = $("#status");
  s.classList.remove("err");
  s.textContent = r.missingRequired > 0
    ? `${r.missingRequired} required question(s) still need you before the form is complete.`
    : "Everything required is filled — review the page, then submit it yourself.";
}

/* Dashboard hand-off: if the dashboard's Apply opened this page, its queued
 * task carries the real company name and full JD — better draft grounding
 * than page scraping. Read-only; the human's Fill click below is still the
 * only thing that acts. */
async function checkPendingTask() {
  const { task } = await chrome.runtime.sendMessage({ type: "PENDING_TASK" })
    .catch(() => ({ task: null }));
  if (!task || !task.apply_url) return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) return;
  try {
    if (new URL(tab.url).hostname !== new URL(task.apply_url).hostname) return;
  } catch (e) {
    return;
  }
  TASK = task;
  const banner = $("#task-banner");
  banner.textContent = `Queued from the dashboard: ${task.company} — ${task.title}. ` +
    "Click Fill and this page's status will report back there.";
  banner.classList.remove("hidden");
}

/* Counts only — no form values leave the page. */
function reportBack(r) {
  if (!TASK) return;
  chrome.runtime.sendMessage({
    type: "FILL_REPORT",
    report: {
      job_id: TASK.job_id,
      filled: (r.filled || []).length,
      skipped: (r.needsYou || []).length + (r.confirm || []).length,
      missing_required: r.missingRequired || 0,
    },
  }).catch(() => {});
}

async function fillForm() {
  const btn = $("#fill");
  const s = $("#status");
  btn.disabled = true;
  s.classList.remove("err");
  s.textContent = "Reading the form and fetching your answers…";
  const context = TASK ? { company: TASK.company, jd: TASK.jd } : null;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) throw new Error("No active tab.");
    TAB_ID = tab.id;
    let r = await chrome.tabs.sendMessage(tab.id, { type: "FILL_FORM", context })
      .catch(() => null);
    if (!r) {
      // No content script in this tab — normal when the tab was opened before
      // the extension was (re)loaded. Inject it now and retry, instead of
      // wrongly claiming the page is unsupported.
      const injected = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: chrome.runtime.getManifest().content_scripts[0].js,
      }).then(() => true).catch(() => false);   // fails only on unpermitted pages
      if (injected) {
        r = await chrome.tabs.sendMessage(tab.id, { type: "FILL_FORM", context })
          .catch(() => null);
      }
    }
    if (!r) {
      throw new Error("This page isn't a supported job board (Greenhouse, Ashby, " +
                      "Lever) — open the job's application page and try again.");
    }
    if (r.error) throw new Error(r.error);
    renderResult(r);
    reportBack(r);   // dashboard hand-off: counts only, fire-and-forget
  } catch (e) {
    s.classList.add("err");
    s.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function refreshBackendState() {
  const el = $("#backend-state");
  const { ok } = await chrome.runtime.sendMessage({ type: "PING_BACKEND" })
    .catch(() => ({ ok: false }));
  el.textContent = ok ? "backend connected" : "backend not running";
  el.classList.toggle("up", ok);
  el.classList.toggle("down", !ok);
}

async function initPort() {
  const { port } = await chrome.storage.sync.get({ port: DEFAULT_PORT });
  const input = $("#port");
  input.value = port;
  input.addEventListener("change", async () => {
    const v = Number(input.value) || DEFAULT_PORT;
    await chrome.storage.sync.set({ port: v });
    refreshBackendState();
  });
}

$("#fill").addEventListener("click", fillForm);
initPort().then(refreshBackendState);
checkPendingTask();
