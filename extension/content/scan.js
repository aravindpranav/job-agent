/* Form scan — a direct port of the Python apply flow's _SCAN_JS
 * (src/job_agent/apply/form_reader.py). Emits the same raw descriptors
 * {tag, type, name, label, groupLabel, required, maxlength, options, selector}
 * that the backend's fields_from_scan() classifies, so both flows resolve
 * fields with identical logic.
 *
 * One deliberate divergence: the Python scan emits Playwright-only
 * ':nth-match(...)' selectors for anonymous ARIA widgets; those don't exist in
 * querySelector, so this scan stamps a data-ja-field attribute instead and
 * addresses the element as [data-ja-field="n"].
 */
"use strict";

function JA_scanForm(root) {
  const out = [];
  let stamp = 0;
  const controls = root.querySelectorAll("input, select, textarea");

  // Lever "cards" custom questions put the question text in a plain
  // <div class="application-label"> — NOT a <label> element and not
  // aria-linked, so the label ladder can't see it. Structural, proven
  // against the live capture: every cards control sits inside a
  // li.application-question whose .application-label holds the visible
  // question. Yields "" anywhere this shape doesn't exist.
  const questionDiv = (el) => {
    const li = el.closest("li.application-question");
    const q = li && li.querySelector(".application-label");
    return q ? q.innerText.trim() : "";
  };

  const labelFor = (el) => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return l.innerText.trim();
    }
    const wrap = el.closest("label");
    if (wrap) return wrap.innerText.trim();
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const t = lb.split(/\s+/)
        .map((i) => (document.getElementById(i) || {}).innerText || "")
        .join(" ").trim();
      if (t) return t;
    }
    const aria = el.getAttribute("aria-label");
    if (aria) return aria;
    // Structural BEFORE the heuristic walk: Lever's custom-question <li>s
    // share one <ul>, so the walk below can reach it and grab a SIBLING
    // card's option label ("Yes") as this field's question. Radio/checkbox
    // option labels are unaffected — closest("label") resolved them above.
    const q = questionDiv(el);
    if (q) return q;
    let anc = el.parentElement;
    for (let i = 0; i < 4 && anc; i++) {
      const l = anc.querySelector("label");
      if (l && l.innerText.trim()) return l.innerText.trim();
      anc = anc.parentElement;
    }
    // still "" when nothing exists — the field then pauses and surfaces as today
    return el.getAttribute("placeholder") || "";
  };

  const stampSelector = (el) => {
    stamp += 1;
    el.setAttribute("data-ja-field", String(stamp));
    return `[data-ja-field="${stamp}"]`;
  };
  const selectorFor = (el) => {
    if (el.id) return "#" + CSS.escape(el.id);
    if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
    return stampSelector(el);
  };

  // A control a human can't see is not part of the form we present: zero-size
  // or fully transparent inputs (react-select's hidden validation companion on
  // Greenhouse's newer boards) would otherwise duplicate their real widget and
  // get "filled" without anything visible changing.
  const invisible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width < 2 || r.height < 2 || getComputedStyle(el).opacity === "0";
  };

  controls.forEach((el) => {
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (["hidden", "submit", "button", "reset", "image"].includes(type)) return;
    if ((el.offsetParent === null || invisible(el)) && type !== "file") return; // skip hidden
    const options = el.tagName.toLowerCase() === "select"
      ? Array.from(el.options).map((o) => o.text.trim())
          .filter((t) => t && !/^select/i.test(t))
      : [];
    const fieldset = el.closest("fieldset");
    const legend = fieldset ? fieldset.querySelector("legend") : null;
    const role = (el.getAttribute("role") || "").toLowerCase();
    // radio/checkbox groups: the question is the legend — or, on Lever cards
    // (no fieldset at all), the question div. The per-option labels are
    // untouched: labelFor still resolves each control's own option text.
    const groupLabel = legend ? legend.innerText.trim()
      : (type === "radio" || type === "checkbox") ? questionDiv(el) : "";
    out.push({
      tag: role === "combobox" ? "combobox" : el.tagName.toLowerCase(),
      type,
      name: el.getAttribute("name") || el.id || "",
      label: labelFor(el),
      groupLabel,
      required: el.hasAttribute("required") || el.getAttribute("aria-required") === "true",
      maxlength: el.getAttribute("maxlength"),
      options,
      selector: selectorFor(el),
    });
  });

  // ARIA widgets (React selects) with no native control behind them.
  root.querySelectorAll('[role="combobox"], [role="listbox"]').forEach((el) => {
    if (["input", "select", "textarea"].includes(el.tagName.toLowerCase())) return;
    if (el.offsetParent === null) return;
    let opts = Array.from(el.querySelectorAll('[role="option"]'))
      .map((o) => o.innerText.trim()).filter(Boolean);
    const owns = el.getAttribute("aria-controls") || el.getAttribute("aria-owns");
    if (!opts.length && owns) {
      const pop = document.getElementById(owns);
      if (pop) {
        opts = Array.from(pop.querySelectorAll('[role="option"]'))
          .map((o) => o.innerText.trim()).filter(Boolean);
      }
    }
    out.push({
      tag: el.getAttribute("role").toLowerCase(),
      type: "",
      name: el.id || "",
      label: labelFor(el),
      groupLabel: "",
      required: el.getAttribute("aria-required") === "true",
      maxlength: null,
      options: opts,
      selector: el.id ? "#" + CSS.escape(el.id) : stampSelector(el),
    });
  });

  // Yes/No button pairs (Ashby toggles) — two sibling <button>s, no input.
  const pairSeen = new Set();
  root.querySelectorAll("button").forEach((btn) => {
    const parent = btn.parentElement;
    if (!parent || pairSeen.has(parent)) return;
    if (btn.offsetParent === null) return;
    const btns = Array.from(parent.children).filter((c) => c.tagName === "BUTTON");
    const texts = btns.map((b) => b.innerText.trim());
    const lower = texts.map((t) => t.toLowerCase());
    if (btns.length !== 2 || !lower.includes("yes") || !lower.includes("no")) return;
    pairSeen.add(parent);
    const entry = parent.closest("[data-field-path]");
    const selector = entry
      ? `[data-field-path="${entry.getAttribute("data-field-path")}"]`
      : parent.id ? "#" + CSS.escape(parent.id) : stampSelector(parent);
    out.push({
      tag: "buttonpair",
      type: "",
      name: parent.id || "",
      label: labelFor(parent),
      groupLabel: "",
      required: parent.getAttribute("aria-required") === "true",
      maxlength: null,
      options: texts,
      selector,
    });
  });

  return out;
}
