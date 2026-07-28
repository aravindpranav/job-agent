/* ATS registry — one module per job board.
 *
 * Adding a 4th ATS = appending one entry here (plus its host in manifest.json):
 *   { id, hosts: [hostnames], formRoot: () => Element }
 * formRoot scopes the scan to the application form so page chrome (search
 * boxes, newsletter signups) is never read or filled. Fall back to the whole
 * document only when the board renders no recognizable container.
 */
"use strict";

var JA_ATS = (() => {
  const first = (...sels) => {
    for (const s of sels) {
      const el = document.querySelector(s);
      if (el) return el;
    }
    return document;
  };

  const MODULES = [
    {
      id: "greenhouse",
      hosts: ["boards.greenhouse.io", "job-boards.greenhouse.io"],
      formRoot: () => first("#application_form", "#application-form",
                            "form#application", "div#application",
                            "form[action*='greenhouse']"),
    },
    {
      id: "ashby",
      hosts: ["jobs.ashbyhq.com"],
      // Ashby is a React SPA; the application tab renders one <form>.
      formRoot: () => first("form"),
    },
    {
      id: "lever",
      hosts: ["jobs.lever.co", "jobs.eu.lever.co"],
      formRoot: () => first("#application-form", ".application-form",
                            "form[action*='apply']", "form"),
    },
  ];

  return {
    detect: () => MODULES.find((m) => m.hosts.includes(location.hostname)) || null,
  };
})();
