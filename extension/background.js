/* Service worker: the only place that talks to the local backend.
 *
 * Content scripts run in the page's origin, where a fetch to
 * http://127.0.0.1 would be blocked as mixed content on these https job
 * boards — so they message the worker, which fetches with the extension's
 * own origin (covered by the 127.0.0.1 host permission and admitted by the
 * backend's chrome-extension-only CORS policy).
 *
 * Nothing here talks to any external server; the only fetch target is
 * 127.0.0.1. Profile data never leaves the machine.
 */
"use strict";

const DEFAULT_PORT = 8642;   // `python -m job_agent dashboard` default

async function backendUrl(path) {
  const { port } = await chrome.storage.sync.get({ port: DEFAULT_PORT });
  return `http://127.0.0.1:${port}${path}`;
}

async function resolveFields(fields, company, jd) {
  let resp;
  try {
    resp = await fetch(await backendUrl("/api/extension/fill-values"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields, company: company || "", jd: jd || "" }),
    });
  } catch (e) {
    return { error: "Could not reach the local job-agent backend — start it with " +
                    "`python -m job_agent dashboard` and try again." };
  }
  if (!resp.ok) {
    const detail = await resp.json().then((d) => d.detail).catch(() => resp.statusText);
    return { error: `Backend error: ${typeof detail === "string" ? detail : resp.statusText}` };
  }
  return resp.json();
}

async function ping() {
  try {
    const resp = await fetch(await backendUrl("/api/extension/ping"));
    return { ok: resp.ok };
  } catch (e) {
    return { ok: false };
  }
}

/* The dashboard's "Apply" queues a fill task (job id + company + JD) when it
 * opens the job page in this browser. Pulling it is read-only and optional —
 * filling still ONLY happens on the human's click in the popup. */
async function pendingTask() {
  try {
    const resp = await fetch(await backendUrl("/api/extension/pending-task"));
    if (!resp.ok) return { task: null };
    return resp.json();
  } catch (e) {
    return { task: null };
  }
}

/* Counts only — no form values ever leave the page through this. */
async function reportFill(report) {
  try {
    const resp = await fetch(await backendUrl("/api/extension/fill-report"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(report),
    });
    return { ok: resp.ok };
  } catch (e) {
    return { ok: false };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || !msg.type) return undefined;
  if (msg.type === "RESOLVE_FIELDS") {
    resolveFields(msg.fields, msg.company, msg.jd).then(sendResponse)
      .catch((e) => sendResponse({ error: String(e && e.message || e) }));
    return true;
  }
  if (msg.type === "PING_BACKEND") {
    ping().then(sendResponse).catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (msg.type === "PENDING_TASK") {
    pendingTask().then(sendResponse).catch(() => sendResponse({ task: null }));
    return true;
  }
  if (msg.type === "FILL_REPORT") {
    reportFill(msg.report).then(sendResponse).catch(() => sendResponse({ ok: false }));
    return true;
  }
  return undefined;
});
