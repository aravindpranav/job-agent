"""Dashboard API: a local UI layer over the same functions the CLI calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_agent.apply.tracker import ApplicationRecord, record_attempt
from job_agent.dashboard.app import create_app
from job_agent.dashboard import service


# --- fixtures ----------------------------------------------------------------------

def _seed_applications(log: Path) -> None:
    record_attempt(log, ApplicationRecord(
        company="Plaid", title="ML Engineer", job_id="j1", source="ashby",
        date="2026-07-08T10:00:00+00:00", status="paused"))
    record_attempt(log, ApplicationRecord(
        company="Stripe", title="AI Engineer", job_id="j2", source="greenhouse",
        date="2026-07-09T09:00:00+00:00", status="submitted"))
    record_attempt(log, ApplicationRecord(
        company="Ramp", title="Applied AI", job_id="j3", source="ashby",
        date="2026-07-09T11:00:00+00:00", status="failed"))


def _seed_last_search(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generated_at": "2026-07-09T12:00:00+00:00",
        "jobs": {
            "j1": {"id": "j1", "title": "ML Engineer", "company": "Plaid",
                   "location": "SF", "url": "http://x", "source": "ashby",
                   "board": "plaid", "score": 88, "verdict": "strong",
                   "reasons": ["good fit"]},
            "j2": {"id": "j2", "title": "AI Engineer", "company": "Stripe",
                   "location": "Chicago", "url": "http://y", "source": "greenhouse",
                   "board": "stripe", "score": 55, "verdict": "possible",
                   "reasons": []},
        },
    }))


@pytest.fixture
def client(tmp_path):
    _seed_applications(tmp_path / "applications.json")
    _seed_last_search(tmp_path / "last_search.json")
    app = create_app(data_dir=tmp_path)
    return TestClient(app)


# --- section 1: application pipeline ------------------------------------------------

def test_applications_endpoint_returns_records_and_counts(client):
    data = client.get("/api/applications").json()
    assert data["counts"] == {"total": 3, "submitted": 1, "paused": 1, "failed": 1,
                              "needs_follow_up": 0}
    assert [r["company"] for r in data["records"]] == ["Ramp", "Stripe", "Plaid"]  # newest first
    assert data["records"][1]["status"] == "submitted"


def test_applications_endpoint_with_no_log_is_empty_not_an_error(tmp_path):
    app = create_app(data_dir=tmp_path / "empty")
    data = TestClient(app).get("/api/applications").json()
    assert data["counts"]["total"] == 0
    assert data["records"] == []


def test_applications_view_collapses_legacy_duplicate_rows(tmp_path):
    # a log written BEFORE the one-record-per-job fix: same job twice, and the
    # sr-search id-reissue case (same company+title, different ids)
    import json as _json
    log = tmp_path / "applications.json"
    log.write_text(_json.dumps([
        {"company": "Scale AI", "title": "ML Engineer", "job_id": "s1",
         "date": "2026-07-08T10:00:00+00:00", "status": "submitted",
         "attempt_id": "a1"},
        {"company": "Scale AI", "title": "ML Engineer", "job_id": "s1",
         "date": "2026-07-10T10:00:00+00:00", "status": "paused",
         "attempt_id": "a2"},
        {"company": "Ginas Tech Jobs", "title": "Senior ML Engineer",
         "job_id": "744000111", "date": "2026-07-09T10:00:00+00:00",
         "status": "submitted", "attempt_id": "a3"},
        {"company": "Ginas Tech Jobs", "title": "Senior ML Engineer",
         "job_id": "744000999", "date": "2026-07-11T10:00:00+00:00",
         "status": "paused", "attempt_id": "a4"},
    ]))
    data = service.applications_view(log)
    assert data["counts"]["total"] == 2                 # one row per job
    by_company = {r["company"]: r for r in data["records"]}
    assert by_company["Scale AI"]["status"] == "paused"           # latest wins
    assert by_company["Ginas Tech Jobs"]["status"] == "paused"


# --- section 2: search + browse ------------------------------------------------------

def test_jobs_endpoint_serves_the_last_search_ranked(client):
    data = client.get("/api/jobs").json()
    assert data["generated_at"].startswith("2026-07-09")
    assert [j["id"] for j in data["jobs"]] == ["j1", "j2"]        # score-ranked
    assert data["jobs"][0]["verdict"] == "strong"
    assert data["jobs"][0]["score"] == 88


def test_search_endpoint_runs_the_pipeline_and_returns_fresh_jobs(tmp_path):
    _seed_last_search(tmp_path / "last_search.json")

    calls = []

    def fake_searcher(days: int) -> dict:
        calls.append(days)
        return {"ok": True, "output": "Pipeline: fetched 10 → ...", "exit_code": 0}

    app = create_app(data_dir=tmp_path, searcher=fake_searcher)
    resp = TestClient(app).post("/api/search", json={"days": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert calls == [3]
    assert data["ok"] is True
    assert [j["id"] for j in data["jobs"]] == ["j1", "j2"]        # re-read from disk


# --- section 3: tailor + apply (human gate preserved) ---------------------------------

def test_tailor_endpoint_runs_for_a_job_and_reports_the_output(tmp_path):
    _seed_last_search(tmp_path / "last_search.json")

    def fake_tailorer(job_id: str) -> dict:
        return {"ok": True, "exit_code": 0, "output": "PDF: data/output/x.pdf",
                "pdf": "data/output/x.pdf"}

    app = create_app(data_dir=tmp_path, tailorer=fake_tailorer)
    data = TestClient(app).post("/api/tailor", json={"job_id": "j1"}).json()
    assert data["ok"] is True
    assert data["pdf"] == "data/output/x.pdf"


def test_apply_preview_endpoint_returns_the_plan_and_never_submits(tmp_path):
    _seed_last_search(tmp_path / "last_search.json")

    def fake_previewer(job_id: str) -> dict:
        assert job_id == "j1"
        return {
            "planned": [{"label": "Email", "value": "j@x.com",
                         "source": "career_facts.email", "tag": "[FROM ANSWER_BANK]",
                         "required": True}],
            "unfilled": [{"label": "I consent to the privacy policy",
                          "reason": "legal consent/acknowledgment — never auto-filled",
                          "tag": "[PAUSED: consent]", "required": True}],
            "missing_required": 1,
            "submit_command": "python -m job_agent apply --job j1 --submit",
        }

    app = create_app(data_dir=tmp_path, previewer=fake_previewer)
    data = TestClient(app).post("/api/apply/preview", json={"job_id": "j1"}).json()
    assert data["planned"][0]["tag"] == "[FROM ANSWER_BANK]"
    assert data["unfilled"][0]["tag"] == "[PAUSED: consent]"
    assert "job_agent apply --job j1 --submit" in data["submit_command"]


def test_unknown_job_id_is_a_404(tmp_path):
    _seed_last_search(tmp_path / "last_search.json")
    app = create_app(data_dir=tmp_path)
    assert TestClient(app).post("/api/tailor", json={"job_id": "nope"}).status_code == 404
    assert TestClient(app).post("/api/apply/preview",
                                json={"job_id": "nope"}).status_code == 404


def test_index_serves_the_single_page_ui(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    for section in ("Applications", "Search", "Apply"):
        assert section in resp.text


def test_run_search_cli_namespace_satisfies_cmd_search(tmp_path, monkeypatch):
    # Regression: run_search_cli builds its own Namespace; cmd_search grew an
    # `include_applied` read and crashed with AttributeError on this path.
    # Stub the pipeline + scorer so the command runs to its LAST line offline.
    from datetime import datetime, timezone

    import job_agent.cli as cli
    from job_agent.models import Job, ScoredJob
    from job_agent.search import SearchOutcome, StageCounts
    from job_agent.apply.tracker import upsert_job_state

    job = Job(id="s1", title="ML Engineer", company="Snowflake", location="US",
              url="http://x", source="ashby", posted_at=datetime.now(timezone.utc))
    monkeypatch.setattr(cli.search, "run", lambda *a, **k: SearchOutcome(
        jobs=[job], boards=["ashby/snowflake"], counts=StageCounts(fetched=1),
        per_source={"ashby/snowflake": 1}, warnings=[]))
    monkeypatch.setattr(cli, "score_jobs",
                        lambda *a, **k: [ScoredJob(job=job, score=80, verdict="strong")])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("JOB_AGENT_DATA_DIR", str(tmp_path))
    profile = tmp_path / "profile.yaml"
    profile.write_text("keywords: [ml]\nsources:\n  - {ats: ashby, board: snowflake}\n")
    # this job is already applied -> the filter path must run, not crash
    upsert_job_state(tmp_path / "applications.json", job_id="s1",
                     company="Snowflake", title="ML Engineer", status="applied")

    result = service.run_search_cli(profile, days=7)
    assert result["ok"] is True, result["output"]          # no AttributeError
    assert "Hid 1 job(s) you already applied to" in result["output"]
    assert "Snowflake" in result["output"]                 # named in the hid note


# --- feature: editable status / notes / follow-up -------------------------------------

def test_track_endpoint_upserts_status_and_notes_and_persists(tmp_path):
    _seed_last_search(tmp_path / "last_search.json")
    app = create_app(data_dir=tmp_path)
    c = TestClient(app)

    resp = c.post("/api/track", json={"job_id": "j1", "company": "Plaid",
                                      "title": "ML Engineer", "source": "ashby",
                                      "status": "interviewing",
                                      "notes": "recruiter: Sam",
                                      "follow_up": "2026-07-20"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "interviewing"

    # persisted to the tracker file, not just in memory
    from job_agent.apply.tracker import load_applications
    (rec,) = load_applications(tmp_path / "applications.json")
    assert (rec.status, rec.notes, rec.follow_up) == \
        ("interviewing", "recruiter: Sam", "2026-07-20")

    # a second update touches the same record
    c.post("/api/track", json={"job_id": "j1", "status": "offer"})
    (rec,) = load_applications(tmp_path / "applications.json")
    assert rec.status == "offer" and rec.notes == "recruiter: Sam"


def test_track_endpoint_rejects_unknown_status(tmp_path):
    app = create_app(data_dir=tmp_path)
    resp = TestClient(app).post("/api/track", json={"job_id": "j1", "status": "on-fire"})
    assert resp.status_code == 422


def test_applications_endpoint_flags_overdue_follow_ups(tmp_path):
    from job_agent.apply.tracker import upsert_job_state
    upsert_job_state(tmp_path / "applications.json", job_id="j1", company="Plaid",
                     follow_up="2020-01-01")                    # long past
    upsert_job_state(tmp_path / "applications.json", job_id="j2", company="Stripe",
                     follow_up="2099-01-01")                    # far future
    data = TestClient(create_app(data_dir=tmp_path)).get("/api/applications").json()
    by_id = {r["job_id"]: r for r in data["records"]}
    assert by_id["j1"]["needs_follow_up"] is True
    assert by_id["j2"]["needs_follow_up"] is False


def test_jobs_endpoint_marks_already_applied_by_id_and_by_company_title(tmp_path):
    from job_agent.apply.tracker import upsert_job_state
    _seed_last_search(tmp_path / "last_search.json")
    # j1 applied by exact id; j2's role applied under a DIFFERENT id
    upsert_job_state(tmp_path / "applications.json", job_id="j1", company="Plaid",
                     title="ML Engineer", status="submitted")
    upsert_job_state(tmp_path / "applications.json", job_id="other-run-id",
                     company="Stripe", title="AI Engineer", status="applied")
    jobs = TestClient(create_app(data_dir=tmp_path)).get("/api/jobs").json()["jobs"]
    by_id = {j["id"]: j for j in jobs}
    assert by_id["j1"]["already_applied"] is True          # id match
    assert by_id["j2"]["already_applied"] is True          # company+title re-match
    # a merely saved/paused job is NOT hidden material
    upsert_job_state(tmp_path / "applications.json", job_id="j1", status="saved")
    jobs = TestClient(create_app(data_dir=tmp_path)).get("/api/jobs").json()["jobs"]
    assert next(j for j in jobs if j["id"] == "j1")["already_applied"] is False


def test_jobs_endpoint_joins_tracked_state_by_job_id(tmp_path):
    from job_agent.apply.tracker import upsert_job_state
    _seed_last_search(tmp_path / "last_search.json")
    upsert_job_state(tmp_path / "applications.json", job_id="j1", company="Plaid",
                     status="applied", notes="asked about visa")
    data = TestClient(create_app(data_dir=tmp_path)).get("/api/jobs").json()
    j1 = next(j for j in data["jobs"] if j["id"] == "j1")
    j2 = next(j for j in data["jobs"] if j["id"] == "j2")
    assert j1["tracked"]["status"] == "applied"
    assert j1["tracked"]["notes"] == "asked about visa"
    assert j2["tracked"] is None                    # never touched


def test_setting_applied_on_a_never_tracked_job_creates_and_persists(tmp_path):
    # The manual-application case: the tool never applied to this job, there is
    # NO tracker record — picking "applied" in the dropdown must create one on
    # the spot and it must survive a page refresh (a fresh app over the file).
    _seed_last_search(tmp_path / "last_search.json")
    c = TestClient(create_app(data_dir=tmp_path))
    assert c.get("/api/applications").json()["counts"]["total"] == 0

    resp = c.post("/api/track", json={"job_id": "j2", "company": "Stripe",
                                      "title": "AI Engineer", "source": "greenhouse",
                                      "status": "applied"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"

    # "refresh": a brand-new app instance reading the same data dir
    c2 = TestClient(create_app(data_dir=tmp_path))
    data = c2.get("/api/applications").json()
    (rec,) = data["records"]
    assert (rec["job_id"], rec["status"], rec["company"]) == ("j2", "applied", "Stripe")
    jobs = c2.get("/api/jobs").json()["jobs"]
    j2 = next(j for j in jobs if j["id"] == "j2")
    assert j2["tracked"]["status"] == "applied"        # dropdown state after reload


def test_records_without_a_job_id_are_updatable_by_attempt_id(tmp_path):
    # Old/manual records may have job_id="" — their dropdown must still work,
    # keyed by attempt_id instead.
    from job_agent.apply.tracker import record_attempt, ApplicationRecord, load_applications
    log = tmp_path / "applications.json"
    attempt = record_attempt(log, ApplicationRecord(
        company="OldCo", title="DS", job_id="", source="",
        date="2026-07-01T00:00:00+00:00", status="paused"))
    c = TestClient(create_app(data_dir=tmp_path))
    resp = c.post("/api/track", json={"attempt_id": attempt, "status": "interviewing"})
    assert resp.status_code == 200
    (rec,) = load_applications(log)
    assert rec.status == "interviewing"
    assert len(load_applications(log)) == 1            # updated, not duplicated


def test_track_needs_a_job_id_or_attempt_id(tmp_path):
    c = TestClient(create_app(data_dir=tmp_path))
    assert c.post("/api/track", json={"status": "applied"}).status_code == 422


# --- feature: inline resume preview (served ONLY from the output dir) ------------------

def test_resume_endpoint_serves_the_tailored_pdf(tmp_path):
    _seed_last_search(tmp_path / "last_search.json")
    out = tmp_path / "output"
    out.mkdir(parents=True)
    # the same filename the tailor pipeline produces for this job
    (out / "Jordan_ML_Engineer_Plaid.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "career_facts.yaml").write_text(
        "name: Jordan Rivers\nemail: j@x.com\nphone: '1'\nrole: MLE\n"
        "employers:\n  - company: Acme\n    title: DE\n    duration: 2y\n"
        "skills_inventory: {}\neducation: []\n")
    app = create_app(data_dir=tmp_path)
    resp = TestClient(app).get("/api/resume/j1")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_resume_endpoint_404s_for_unknown_job_or_missing_pdf(tmp_path):
    _seed_last_search(tmp_path / "last_search.json")
    app = create_app(data_dir=tmp_path)
    c = TestClient(app)
    assert c.get("/api/resume/not-a-job").status_code == 404      # unknown id
    assert c.get("/api/resume/j1").status_code == 404             # no PDF yet
    assert c.get("/api/resume/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_resume_endpoint_never_escapes_the_output_dir(tmp_path):
    # Even a hostile record (title with path separators) must resolve inside
    # out_dir: the filename is slugged, and the resolved path is checked.
    _seed_last_search(tmp_path / "last_search.json")
    import json as _json
    data = _json.loads((tmp_path / "last_search.json").read_text())
    data["jobs"]["evil"] = {**data["jobs"]["j1"], "id": "evil",
                            "title": "../../secrets", "company": "../x"}
    (tmp_path / "last_search.json").write_text(_json.dumps(data))
    (tmp_path / "career_facts.yaml").write_text(
        "name: Jordan Rivers\nemail: j@x.com\nphone: '1'\nrole: MLE\n"
        "employers:\n  - company: Acme\n    title: DE\n    duration: 2y\n"
        "skills_inventory: {}\neducation: []\n")
    secret = tmp_path / "secrets.pdf"
    secret.write_bytes(b"%PDF top secret")
    resp = TestClient(create_app(data_dir=tmp_path)).get("/api/resume/evil")
    assert resp.status_code == 404
    assert b"top secret" not in resp.content


# --- the preview service itself: read-only over the page ------------------------------

class _ScanPage:
    """A fake page whose form scan yields one text field + one consent box.

    Records every method call — the preview must READ (goto/evaluate) and
    never fill, check, click, or submit anything.
    """

    def __init__(self):
        self.calls = []

    def goto(self, url, wait_until=None):
        self.calls.append(("goto", url))

    def inner_text(self, sel):
        return ""

    def evaluate(self, js):
        self.calls.append(("evaluate", "scan"))
        return [
            {"tag": "input", "type": "email", "name": "email", "label": "Email",
             "groupLabel": "", "required": True, "maxlength": None, "options": [],
             "selector": "#email"},
            {"tag": "input", "type": "checkbox", "name": "", "label":
             "I consent to the privacy policy", "groupLabel": "", "required": True,
             "maxlength": None, "options": [], "selector": "#consent"},
        ]


def test_preview_service_builds_a_tagged_plan_without_touching_the_page(tmp_path):
    from contextlib import contextmanager

    from job_agent.apply.answer_bank import AnswerBank, Contact

    page = _ScanPage()

    @contextmanager
    def browser(headless=True):
        yield page

    record = {"id": "j1", "title": "ML Engineer", "company": "Plaid",
              "apply_url": "http://apply.example/j1", "url": "http://apply.example/j1",
              "source": "ashby", "description": "we need ml"}
    bank = AnswerBank.model_validate({"authorized_us": True, "requires_sponsorship": False})
    contact = Contact(name="Jordan Rivers", email="j@x.com", phone="1")

    preview = service.build_apply_preview(record, bank, contact, resume_path=None,
                                          drafter=None, browser_factory=browser)
    by_label = {p["label"]: p for p in preview["planned"]}
    assert by_label["Email"]["value"] == "j@x.com"
    assert by_label["Email"]["tag"] == "[FROM ANSWER_BANK]"
    consent = next(u for u in preview["unfilled"] if "consent" in u["label"].lower())
    assert consent["tag"] == "[PAUSED: consent]"
    assert preview["missing_required"] >= 1
    assert "--submit" in preview["submit_command"]
    # READ-ONLY: the page saw navigation + the scan and nothing else.
    kinds = {k for k, *_ in page.calls}
    assert kinds == {"goto", "evaluate"}
