"""The Chrome extension's backend: POST detected fields, get resolved values.

Safety properties pinned here (the same rules as the CLI apply flow — the
endpoint reuses ``build_fill_plan``, so they hold by construction):
  * consent/legal/EEO-consent fields NEVER receive a value — they come back in
    ``unfilled`` tagged ``[PAUSED: consent]``;
  * credential fields never receive a value;
  * the endpoint only RESOLVES values — it cannot fill or submit anything;
  * CORS: only chrome-extension:// origins are ever allowed, never the web.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_agent.dashboard.app import create_app

FACTS_YAML = (
    "name: Jordan Rivers\nemail: j@x.com\nphone: '1'\nrole: MLE\n"
    "employers:\n  - company: Acme\n    title: DE\n    duration: 2y\n"
    "skills_inventory: {}\neducation: []\n"
)
BANK_YAML = "authorized_us: true\nrequires_sponsorship: false\n"


def _control(**kw) -> dict:
    base = {"tag": "input", "type": "text", "name": "", "label": "",
            "groupLabel": "", "required": False, "maxlength": None,
            "options": [], "selector": ""}
    return {**base, **kw}


RAW_FIELDS = [
    _control(type="email", name="email", label="Email", required=True,
             selector="#email"),
    _control(type="checkbox", label="I consent to the privacy policy",
             required=True, selector="#consent"),
    _control(type="password", name="pw", label="Create a password",
             selector="#pw"),
    _control(type="radio", name="authorized", label="Yes", required=True,
             groupLabel="Are you authorized to work in the United States?",
             selector="#auth-yes"),
    _control(type="radio", name="authorized", label="No", required=True,
             groupLabel="Are you authorized to work in the United States?",
             selector="#auth-no"),
]


@pytest.fixture
def client(tmp_path):
    (tmp_path / "career_facts.yaml").write_text(FACTS_YAML)
    (tmp_path / "answer_bank.yaml").write_text(BANK_YAML)
    return TestClient(create_app(data_dir=tmp_path))


def _post(client, fields):
    return client.post("/api/extension/fill-values", json={"fields": fields})


# --- resolution: existing answer-bank/facts logic, no new answer logic ---------------

def test_resolves_contact_fields_from_the_existing_logic(client):
    data = _post(client, RAW_FIELDS).json()
    by_label = {p["label"]: p for p in data["planned"]}
    assert by_label["Email"]["value"] == "j@x.com"
    assert by_label["Email"]["tag"] == "[FROM ANSWER_BANK]"
    assert by_label["Email"]["selector"] == "#email"


def test_consent_fields_pause_and_never_carry_a_value(client):
    data = _post(client, RAW_FIELDS).json()
    consent = next(u for u in data["unfilled"] if "consent" in u["label"].lower())
    assert consent["tag"] == "[PAUSED: consent]"
    assert "value" not in consent            # structurally impossible to fill
    assert consent["selector"] == "#consent"


def test_credential_fields_pause_and_never_carry_a_value(client):
    data = _post(client, RAW_FIELDS).json()
    cred = next(u for u in data["unfilled"] if "password" in u["label"].lower())
    assert cred["tag"] == "[PAUSED: credential]"
    assert "value" not in cred


def test_same_name_radios_group_into_one_question(client):
    data = _post(client, RAW_FIELDS).json()
    auth = [e for e in data["planned"] + data["unfilled"]
            if "authorized" in (e["label"] + e["selector"]).lower()]
    assert len(auth) == 1                    # 2 radios -> 1 question
    assert auth[0]["selector"] == 'input[name="authorized"]'
    # the banked yes/no answer resolves through the existing matcher
    assert auth[0].get("value") == "Yes"


def test_missing_required_counts_only_required_unfilled(client):
    data = _post(client, RAW_FIELDS).json()
    assert data["missing_required"] == 1     # consent (required); password is optional


# --- validation at the boundary --------------------------------------------------------

def test_rejects_a_payload_without_fields(client):
    assert client.post("/api/extension/fill-values", json={}).status_code == 422


def test_rejects_an_oversized_field_list(client):
    resp = _post(client, [_control(selector=f"#f{i}") for i in range(301)])
    assert resp.status_code == 422


def test_unaddressable_controls_are_skipped_not_guessed(client):
    data = _post(client, [_control(label="Mystery", selector="")]).json()
    assert data["planned"] == [] and data["unfilled"] == []


def test_missing_answer_bank_is_a_clear_error(tmp_path):
    (tmp_path / "career_facts.yaml").write_text(FACTS_YAML)
    client = TestClient(create_app(data_dir=tmp_path))
    resp = _post(client, RAW_FIELDS)
    assert resp.status_code == 400
    assert "answer_bank" in resp.json()["detail"]


# --- resume hint: which file the human should attach (never auto-uploaded) -----------

def test_file_fields_pause_and_the_response_recommends_the_tailored_pdf(tmp_path):
    (tmp_path / "career_facts.yaml").write_text(FACTS_YAML)
    (tmp_path / "answer_bank.yaml").write_text(BANK_YAML)
    out = tmp_path / "output"
    out.mkdir()
    (out / "Jordan_ML_Engineer_Plaid.pdf").write_bytes(b"%PDF old")
    import os
    os.utime(out / "Jordan_ML_Engineer_Plaid.pdf", (1, 1))
    (out / "Jordan_AI_Engineer_Stripe.pdf").write_bytes(b"%PDF new")
    client = TestClient(create_app(data_dir=tmp_path))

    fields = RAW_FIELDS + [_control(type="file", label="Resume/CV",
                                    required=True, selector="#resume")]
    data = _post(client, fields).json()
    resume_field = next(u for u in data["unfilled"] if u["selector"] == "#resume")
    assert "value" not in resume_field           # never auto-uploaded
    assert data["resume"]["name"] == "Jordan_AI_Engineer_Stripe.pdf"  # newest

    # naming the company prefers its tailored resume over the newest one
    data = client.post("/api/extension/fill-values",
                       json={"fields": fields, "company": "Plaid"}).json()
    assert data["resume"]["name"] == "Jordan_ML_Engineer_Plaid.pdf"


def test_resume_hint_is_null_when_nothing_is_tailored_yet(client):
    data = _post(client, RAW_FIELDS).json()
    assert data["resume"] is None


# --- essay drafting: the EXISTING screening drafter, never silently auto-filled -------

ESSAY = _control(tag="textarea", type="", name="project",
                 label="Describe a recent project you are proud of",
                 required=True, selector="#project")


def test_free_text_drafts_with_the_existing_screening_drafter(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from job_agent.apply import screening
    monkeypatch.setattr(screening, "make_llm_generate",
                        lambda settings: lambda prompt: "I shipped an ML feature store at Acme.")
    (tmp_path / "career_facts.yaml").write_text(FACTS_YAML)
    (tmp_path / "answer_bank.yaml").write_text(BANK_YAML)
    client = TestClient(create_app(data_dir=tmp_path))

    data = client.post("/api/extension/fill-values",
                       json={"fields": [ESSAY], "company": "Acme",
                             "jd": "We need someone who ships ML."}).json()
    draft = next(p for p in data["planned"] if p["selector"] == "#project")
    assert draft["tag"] in ("[AI-DRAFT]", "[NEEDS-INPUT]", "[GATE-FLAGGED]")
    assert draft["value"]                        # the draft text, for review


def test_no_api_key_means_no_drafting_and_the_question_pauses(client, monkeypatch):
    # empty (not unset): load_dotenv must not re-read a real key from .env
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    data = client.post("/api/extension/fill-values",
                       json={"fields": [ESSAY], "company": "Acme",
                             "jd": "We need ML."}).json()
    paused = next(u for u in data["unfilled"] if u["selector"] == "#project")
    assert "value" not in paused


def test_consent_still_pauses_even_with_a_drafter_available(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from job_agent.apply import screening
    monkeypatch.setattr(screening, "make_llm_generate",
                        lambda settings: lambda prompt: "should never appear")
    (tmp_path / "career_facts.yaml").write_text(FACTS_YAML)
    (tmp_path / "answer_bank.yaml").write_text(BANK_YAML)
    client = TestClient(create_app(data_dir=tmp_path))
    data = client.post("/api/extension/fill-values",
                       json={"fields": RAW_FIELDS, "company": "Acme", "jd": "x"}).json()
    consent = next(u for u in data["unfilled"] if "consent" in u["label"].lower())
    assert consent["tag"] == "[PAUSED: consent]"
    assert "value" not in consent


# --- the popup must know WHY there is no draft ----------------------------------------

def test_response_reports_drafting_off_and_why_without_a_key(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")   # empty beats .env re-load
    data = client.post("/api/extension/fill-values",
                       json={"fields": [ESSAY], "company": "Acme", "jd": "x"}).json()
    assert data["drafting"]["enabled"] is False
    assert "anthropic_api_key" in data["drafting"]["reason"].lower()


def test_response_reports_drafting_on_with_a_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from job_agent.apply import screening
    monkeypatch.setattr(screening, "make_llm_generate",
                        lambda settings: lambda prompt: "drafted text")
    (tmp_path / "career_facts.yaml").write_text(FACTS_YAML)
    (tmp_path / "answer_bank.yaml").write_text(BANK_YAML)
    client = TestClient(create_app(data_dir=tmp_path))
    data = client.post("/api/extension/fill-values",
                       json={"fields": [ESSAY], "company": "Acme", "jd": "x"}).json()
    assert data["drafting"]["enabled"] is True


# --- file fields: extension wording, never the CLI's ----------------------------------

def test_file_fields_carry_kind_and_plain_wording_not_cli_flags(client):
    fields = [_control(type="file", label="Resume/CV", required=True,
                       selector="#resume")]
    data = _post(client, fields).json()
    entry = next(u for u in data["unfilled"] if u["selector"] == "#resume")
    assert entry["kind"] == "file"
    assert "--resume" not in entry["reason"]      # no CLI flags in the popup
    assert "attach" in entry["reason"].lower()


# --- option alias matching (extension/content/match.js, run under node) ---------------

def test_option_alias_matching_usa_to_united_states():
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    script = """
      const {JA_matchOption} = require(process.argv[1]);
      const t = (value, options, expect) => {
        const r = JA_matchOption(value, options);
        const got = r.index === -1 ? null : options[r.index];
        if (got !== expect) {
          console.error(`FAIL: ${value} -> ${got} (wanted ${expect}) [${r.reason||""}]`);
          process.exit(1);
        }
      };
      t("USA", ["Canada", "United States", "Mexico"], "United States");
      t("United States", ["USA", "Canada"], "USA");
      t("US", ["United States of America", "Uzbekistan"], "United States of America");
      t("Bachelor's", ["Master's Degree", "Bachelor's Degree", "PhD"], "Bachelor's Degree");
      t("No", ["Yes", "No"], "No");                       // exact still first
      t("USA", ["North USA Region", "South USA Region"], null);  // ambiguous -> pause
      t("Narnia", ["United States", "Canada"], null);     // unknown -> pause
      // short aliases must match whole words only — the live Nextdoor bug:
      // "us" substring-matched Australia/Austria/Belarus and killed the fill
      t("USA", ["Australia +61", "United States +1", "Belarus +375", "Austria +43"],
        "United States +1");
      t("USA", ["Massachusetts", "Texas - Houston Metro"], null);  // wrong question -> pause
      // compound saved values: the degree part must find the degree option
      t("Masters, Computer Science",
        ["Associate's Degree", "Bachelor's Degree", "Master's Degree", "Doctorate"],
        "Master's Degree");

      // option-shape sanity: a mismatched saved answer must never be attempted
      const {JA_shapeMismatch} = require(process.argv[1]);
      const s = (value, options, expectMismatch) => {
        const got = JA_shapeMismatch(value, options);
        if (Boolean(got) !== expectMismatch) {
          console.error(`FAIL shape: ${value} vs [${options}] -> ${got}`);
          process.exit(1);
        }
      };
      s("Generative AI Engineer", ["Yes", "No"], true);       // title vs yes/no pair
      s("No", ["Yes", "No"], false);                          // yes/no value is fine
      s("No", ["Yes", "No", "Decline To Self Identify"], false);
      s("USA", ["Massachusetts", "Massachusetts - Boston Metro",
                "Texas - Houston Metro", "California - SF Bay Area"], true);
      s("USA", ["Canada", "United States", "Mexico"], false); // country list is fine
      console.log("OK");
    """
    proc = subprocess.run(
        [node, "-e", script,
         "/Users/aravindpranav/job-agent/extension/content/match.js"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout


# --- manifest URL coverage: posting, /apply, and query-string variants ----------------

def _chrome_pattern_matches(pattern: str, url: str) -> bool:
    """Chrome match-pattern semantics: scheme://host/path, '*' wildcards in
    host prefix and path; the URL's query string is ignored for matching."""
    from urllib.parse import urlsplit
    scheme, rest = pattern.split("://", 1)
    host, _, path_pat = rest.partition("/")
    u = urlsplit(url)
    if scheme != "*" and u.scheme != scheme:
        return False
    if host.startswith("*."):
        if not (u.hostname == host[2:] or u.hostname.endswith("." + host[2:])):
            return False
    elif u.hostname != host:
        return False
    import fnmatch
    return fnmatch.fnmatch(u.path or "/", "/" + path_pat)


def test_content_script_patterns_cover_all_application_page_shapes():
    import json
    manifest = json.loads(Path("extension/manifest.json").read_text())
    patterns = manifest["content_scripts"][0]["matches"]
    must_match = [
        # the reported Lever /apply page, query string and all
        "https://jobs.lever.co/employ/65d6239e-f9fc-41ee-bed2-55a427c191de/apply"
        "?lever-source=Job%20postings%20feed&tid=x",
        "https://jobs.lever.co/employ/65d6239e-f9fc-41ee-bed2-55a427c191de",
        "https://jobs.eu.lever.co/some-co/1234abcd/apply",
        "https://boards.greenhouse.io/gitlab/jobs/8503792002",
        "https://boards.greenhouse.io/embed/job_app?for=nextdoor&token=6005888",
        "https://job-boards.greenhouse.io/gitlab/jobs/8503792002",
        "https://jobs.ashbyhq.com/company/1111-2222/application",
    ]
    for url in must_match:
        assert any(_chrome_pattern_matches(p, url) for p in patterns), url
    # and never ordinary websites
    for url in ("https://example.com/jobs.lever.co/x", "https://lever.co/a/apply"):
        assert not any(_chrome_pattern_matches(p, url) for p in patterns), url


def test_host_permissions_cover_every_content_script_host():
    import json
    manifest = json.loads(Path("extension/manifest.json").read_text())
    hosts = {m.split("://", 1)[1].split("/")[0]
             for m in manifest["content_scripts"][0]["matches"]}
    perm_hosts = {h.split("://", 1)[1].split("/")[0]
                  for h in manifest["host_permissions"]}
    assert hosts <= perm_hosts     # every injected host is also permissioned


# --- ping: lets the popup tell "backend down" from "no form found" --------------------

def test_ping_reports_the_backend_is_up(client):
    data = client.get("/api/extension/ping").json()
    assert data["ok"] is True


# --- CORS: extension origins only, never the web ---------------------------------------

EXT_ORIGIN = "chrome-extension://" + "a" * 32


def test_cors_allows_a_chrome_extension_origin(client):
    resp = _post_with_origin(client, EXT_ORIGIN)
    assert resp.headers.get("access-control-allow-origin") == EXT_ORIGIN


def test_cors_never_allows_web_origins(client):
    for origin in ("https://evil.example", "http://127.0.0.1:8000",
                   "moz-extension://" + "a" * 32):
        resp = _post_with_origin(client, origin)
        assert "access-control-allow-origin" not in resp.headers, origin


def test_cors_can_be_pinned_to_one_extension_id(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_EXTENSION_ID", "b" * 32)
    (tmp_path / "career_facts.yaml").write_text(FACTS_YAML)
    (tmp_path / "answer_bank.yaml").write_text(BANK_YAML)
    client = TestClient(create_app(data_dir=tmp_path))
    allowed = _post_with_origin(client, "chrome-extension://" + "b" * 32)
    assert allowed.headers.get("access-control-allow-origin") == \
        "chrome-extension://" + "b" * 32
    other = _post_with_origin(client, EXT_ORIGIN)   # a different extension
    assert "access-control-allow-origin" not in other.headers


def _post_with_origin(client, origin: str):
    return client.post("/api/extension/fill-values",
                       json={"fields": RAW_FIELDS}, headers={"Origin": origin})
