"""DOM-level tests for the extension's combobox fill (match.js + fill.js),
run against a local fixture page in headless Chromium.

The fixture reproduces the live Nextdoor School widget: the widget shows the
FIRST ALPHABETICAL PAGE of a large list when opened, and only swaps in
filtered results ~500ms after a typed query — the fill must type to search
and must not match against the stale pre-typing list.
"""

from __future__ import annotations

import pytest

playwright = pytest.importorskip("playwright.sync_api")

FILL_DIR = "/Users/aravindpranav/job-agent/extension/content"

FIXTURE = """
<div id="wrap">
  <label for="school">School</label>
  <div class="sel-control" id="school-control">
    <input id="school" role="combobox" type="text">
    <div id="school-menu"></div>
  </div>
</div>
<script>
  const FIRST_PAGE = ["Aalborg University", "Aalto University",
                      "Aarhus University", "Abertay University"];
  const ALL = FIRST_PAGE.concat(["University of North Texas",
                                 "University of Texas at Austin"]);
  const input = document.getElementById("school");
  const menu = document.getElementById("school-menu");
  const render = (items) => {
    menu.innerHTML = items.map(t => `<div role="option">${t}</div>`).join("");
    menu.querySelectorAll("[role=option]").forEach(o =>
      o.addEventListener("mousedown", () => {
        input.value = ""; menu.innerHTML = "";
        document.getElementById("school-control").insertAdjacentHTML(
          "beforeend", `<span class="chosen">${o.innerText}</span>`);
      }));
  };
  // opening shows the first alphabetical page (the stale-list trap)
  document.getElementById("school-control")
    .addEventListener("mousedown", () => { if (!input.value) render(FIRST_PAGE); });
  // a typed query returns filtered results only after an async delay
  input.addEventListener("input", () => {
    const q = input.value.toLowerCase();
    if (!q) { render(FIRST_PAGE); return; }
    setTimeout(() => render(ALL.filter(t => t.toLowerCase().includes(q))), 500);
  });
</script>
"""


def test_type_to_search_widget_resolves_after_typing():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(FIXTURE)
        for f in ("match", "fill"):
            page.add_script_tag(path=f"{FILL_DIR}/{f}.js")
        out = page.evaluate("""async () => {
          const r = await JA_fillCombobox(document.getElementById('school'),
                                          'University of North Texas');
          return {r, chosen: (document.querySelector('.chosen')||{}).innerText};
        }""")
        browser.close()
    assert out["r"]["ok"] is True, out
    assert out["chosen"] == "University of North Texas"
