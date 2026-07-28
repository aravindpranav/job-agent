/* Option matching for dropdowns/comboboxes — PURE, no DOM, no chrome APIs.
 *
 * The answer bank stores values the user's way ("USA", "Bachelor's"); forms
 * word their options their way ("United States", "Bachelor's Degree"). The
 * ladder, strictest first:
 *
 *   1. exact  — normalized text equality
 *   2. alias  — both sides normalize into the same known alias group
 *   3. contains — one unambiguous option contains the value (or its aliases)
 *
 * If several options survive the contains pass, that's AMBIGUOUS and the
 * answer is nothing: pausing for the human is always preferred over guessing.
 */
"use strict";

var JA_MATCH = (() => {
  // Each group lists spellings of the same answer. Lowercase; matching is on
  // normalized text (punctuation stripped) so "U.S.A." hits "usa".
  const ALIAS_GROUPS = [
    ["usa", "us", "united states", "united states of america", "america",
     "estados unidos"],
    ["uk", "united kingdom", "great britain", "britain", "england"],
    ["uae", "united arab emirates"],
    ["bachelors", "bachelor", "bachelors degree", "bachelor s", "bs", "ba",
     "b s", "b a", "bsc", "undergraduate degree", "bachelor s degree"],
    ["masters", "master", "masters degree", "master s", "ms", "ma", "m s",
     "m a", "msc", "graduate degree", "master s degree"],
    ["phd", "ph d", "doctorate", "doctoral", "doctor of philosophy",
     "doctoral degree"],
    ["associates", "associate", "associates degree", "associate s degree",
     "associate degree", "aa", "as"],
    ["high school", "high school diploma", "ged", "secondary school"],
    ["yes", "y", "true"],
    ["no", "n", "false"],
  ];

  const norm = (t) => String(t || "").toLowerCase()
    .replace(/[^a-z0-9]+/g, " ").trim();

  const groupOf = (t) => {
    const n = norm(t);
    const i = ALIAS_GROUPS.findIndex((g) => g.includes(n));
    return i === -1 ? null : i;
  };

  // whole-word containment on normalized text: "us" must match "United
  // States +1"'s tokens, never the inside of "Australia" or "Belarus"
  const containsWords = (haystack, needle) =>
    needle.length > 0 && ` ${haystack} `.includes(` ${needle} `);

  /* -> {index, reason}. index -1 means "no safe pick"; reason says why. */
  function JA_matchOption(value, optionTexts) {
    const want = norm(value);
    if (!want) return { index: -1, reason: "empty value" };
    const opts = optionTexts.map(norm);

    let idx = opts.findIndex((o) => o === want);                    // 1. exact
    if (idx !== -1) return { index: idx };

    const g = groupOf(value);                                       // 2. alias
    if (g !== null) {
      idx = opts.findIndex((o) => groupOf(o) === g);
      if (idx !== -1) return { index: idx };
      // an option may EMBED an alias ("United States of America (USA)")
      const hits = opts.map((o, i) =>
        ALIAS_GROUPS[g].some((a) => containsWords(o, a)) ? i : -1)
        .filter((i) => i !== -1);
      if (hits.length === 1) return { index: hits[0] };
      if (hits.length > 1) return { index: -1, reason: "several options match" };
    }

    const hits = opts.map((o, i) => (containsWords(o, want) ? i : -1))  // 3. contains
      .filter((i) => i !== -1);
    if (hits.length === 1) return { index: hits[0] };
    if (hits.length > 1) return { index: -1, reason: "several options match" };

    // 4. compound values: "Masters, Computer Science" against a plain degree
    //    list — try each comma/slash part through exact+alias (NOT contains;
    //    parts are too short to contains-match safely)
    for (const part of String(value).split(/[,;/]/).map(norm).filter(Boolean)) {
      const exact = opts.findIndex((o) => o === part);
      if (exact !== -1) return { index: exact };
      const pg = groupOf(part);
      if (pg !== null) {
        const byAlias = opts.map((o, i) => (groupOf(o) === pg ? i : -1))
          .filter((i) => i !== -1);
        if (byAlias.length === 1) return { index: byAlias[0] };
      }
    }
    return { index: -1, reason: "no option matches" };
  }

  /* Other spellings of this value (lowercase) — used as search queries for
   * type-to-search widgets when the raw value finds nothing. */
  function JA_aliasesFor(value) {
    const g = groupOf(value);
    return g === null ? [] : ALIAS_GROUPS[g].filter((a) => a !== norm(value));
  }

  // --- option-shape sanity: is the SAVED answer even the right KIND? -------
  // A resolver keyed on label words sometimes sends the wrong answer type
  // ("current title …?" carries "title" → gets the job title, but the widget
  // is a Yes/No pair). Attempting it can only mislead — detect and pause.

  const YES_G = ALIAS_GROUPS.findIndex((g) => g.includes("yes"));
  const NO_G = ALIAS_GROUPS.findIndex((g) => g.includes("no"));
  const COUNTRY_GS = ["usa", "uk", "uae"].map(
    (c) => ALIAS_GROUPS.findIndex((g) => g.includes(c)));

  const US_STATES = ["alabama", "alaska", "arizona", "arkansas", "california",
    "colorado", "connecticut", "delaware", "florida", "georgia", "hawaii",
    "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin",
    "wyoming", "district of columbia"];

  const neutralOption = (o) =>
    o.includes("decline") || o.includes("prefer not") || o.includes("don t wish");

  /* -> a human-readable mismatch description, or null when the value's kind
   * plausibly fits the options. Only CONFIDENT mismatches return non-null —
   * this must never block a legitimate attempt. */
  function JA_shapeMismatch(value, optionTexts) {
    const opts = optionTexts.map(norm).filter(Boolean);
    if (!opts.length) return null;
    const vg = groupOf(value);

    const substantive = opts.filter((o) => !neutralOption(o));
    const isYesNoPair = substantive.length >= 2 && substantive.length <= 3
      && substantive.every((o) => groupOf(o) === YES_G || groupOf(o) === NO_G)
      && substantive.some((o) => groupOf(o) === YES_G)
      && substantive.some((o) => groupOf(o) === NO_G);
    if (isYesNoPair && vg !== YES_G && vg !== NO_G) {
      return "this question wants a Yes/No answer";
    }

    if (COUNTRY_GS.includes(vg)) {
      const stateish = opts.filter((o) =>
        US_STATES.some((s) => o === s || o.startsWith(s + " "))).length;
      if (opts.length >= 3 && stateish >= opts.length / 2) {
        return "this question wants a state or region, not a country";
      }
    }
    return null;
  }

  return { JA_matchOption, JA_aliasesFor, JA_shapeMismatch };
})();

// content scripts read the globals; the node-run tests require() this file
var JA_matchOption = JA_MATCH.JA_matchOption;
var JA_aliasesFor = JA_MATCH.JA_aliasesFor;
var JA_shapeMismatch = JA_MATCH.JA_shapeMismatch;
if (typeof module !== "undefined" && module.exports) {
  module.exports = JA_MATCH;
}
