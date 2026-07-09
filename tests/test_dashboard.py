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
    assert data["counts"] == {"total": 3, "submitted": 1, "paused": 1, "failed": 1}
    assert [r["company"] for r in data["records"]] == ["Ramp", "Stripe", "Plaid"]  # newest first
    assert data["records"][1]["status"] == "submitted"


def test_applications_endpoint_with_no_log_is_empty_not_an_error(tmp_path):
    app = create_app(data_dir=tmp_path / "empty")
    data = TestClient(app).get("/api/applications").json()
    assert data["counts"]["total"] == 0
    assert data["records"] == []


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
