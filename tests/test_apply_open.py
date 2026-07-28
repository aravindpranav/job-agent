"""Apply-through-real-browser path: /api/apply/open + the extension task channel.

Safety properties pinned here:
  * /api/apply/open only OPENS a page and registers a task — no Playwright,
    no filling, no submission; the human gates everything in their own browser.
  * The URL opened is the record's stored apply_url VERBATIM (resolve_apply_url).
  * The fill-report channel carries counts only — no form values.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from job_agent.dashboard.app import create_app

FACTS_YAML = (
    "name: Jordan Rivers\nemail: j@x.com\nphone: '1'\nrole: MLE\n"
    "employers:\n  - company: Acme\n    title: DE\n    duration: 2y\n"
    "skills_inventory: {}\neducation: []\n"
)

JOB = {
    "id": "j1", "title": "ML Engineer", "company": "Acme",
    "location": "Remote", "source": "greenhouse", "board": "acme",
    "url": "https://acme.com/careers/ml",
    "apply_url": "https://boards.greenhouse.io/embed/job_app?for=acme&token=1",
    "description": "Build ML systems with Python.",
}


@pytest.fixture
def env(tmp_path):
    (tmp_path / "career_facts.yaml").write_text(FACTS_YAML)
    (tmp_path / "answer_bank.yaml").write_text("authorized_us: true\n")
    (tmp_path / "last_search.json").write_text(json.dumps(
        {"generated_at": "2026-07-21T00:00:00+00:00", "jobs": {"j1": JOB}}))
    opened = []
    app = create_app(data_dir=tmp_path,
                     url_opener=lambda url: opened.append(url) or "chrome")
    return TestClient(app), opened


def test_open_opens_the_stored_apply_url_verbatim(env):
    client, opened = env
    resp = client.post("/api/apply/open", json={"job_id": "j1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["opened_via"] == "chrome"
    assert data["apply_url"] == JOB["apply_url"]
    assert opened == [JOB["apply_url"]]          # verbatim, never rewritten


def test_open_registers_a_pending_task_for_the_extension(env):
    client, _ = env
    client.post("/api/apply/open", json={"job_id": "j1"})
    task = client.get("/api/extension/pending-task").json()["task"]
    assert task["job_id"] == "j1"
    assert task["company"] == "Acme"
    assert task["title"] == "ML Engineer"
    assert task["apply_url"] == JOB["apply_url"]
    assert "Python" in task["jd"]                # grounding for the drafter


def test_pending_task_is_empty_before_any_open(env):
    client, _ = env
    assert client.get("/api/extension/pending-task").json() == {"task": None}


def test_open_unknown_job_is_404_and_opens_nothing(env):
    client, opened = env
    assert client.post("/api/apply/open", json={"job_id": "nope"}).status_code == 404
    assert opened == []


def test_open_without_any_url_is_404(tmp_path):
    (tmp_path / "career_facts.yaml").write_text(FACTS_YAML)
    (tmp_path / "last_search.json").write_text(json.dumps(
        {"jobs": {"j2": {"id": "j2", "title": "T", "company": "C",
                         "location": "", "source": "x", "url": "",
                         "apply_url": None}}}))
    opened = []
    client = TestClient(create_app(
        data_dir=tmp_path, url_opener=lambda u: opened.append(u) or "chrome"))
    assert client.post("/api/apply/open", json={"job_id": "j2"}).status_code == 404
    assert opened == []


def test_fill_report_roundtrip_and_clears_the_pending_task(env):
    client, _ = env
    client.post("/api/apply/open", json={"job_id": "j1"})
    resp = client.post("/api/extension/fill-report", json={
        "job_id": "j1", "filled": 12, "skipped": 2, "missing_required": 1})
    assert resp.status_code == 200
    report = client.get("/api/apply/fill-report/j1").json()["report"]
    assert report["filled"] == 12
    assert report["skipped"] == 2
    assert report["missing_required"] == 1
    assert report["received_at"]
    # one-shot: the task is consumed once the extension reports on it
    assert client.get("/api/extension/pending-task").json() == {"task": None}


def test_fill_report_for_a_different_job_keeps_the_task(env):
    client, _ = env
    client.post("/api/apply/open", json={"job_id": "j1"})
    client.post("/api/extension/fill-report", json={
        "job_id": "other", "filled": 1, "skipped": 0, "missing_required": 0})
    assert client.get("/api/extension/pending-task").json()["task"]["job_id"] == "j1"


def test_fill_report_missing_is_null(env):
    client, _ = env
    assert client.get("/api/apply/fill-report/j1").json() == {"report": None}


# --- the Chrome opener ---------------------------------------------------------

def test_open_in_chrome_prefers_explicit_chrome():
    from job_agent.dashboard.service import open_in_chrome
    ran = []
    via = open_in_chrome("https://x.test/a",
                         _run=lambda cmd, **kw: ran.append(cmd),
                         _fallback=lambda url: pytest.fail("fallback used"))
    assert via == "chrome"
    assert ran[0][-1] == "https://x.test/a"
    assert "Google Chrome" in ran[0]


def test_open_in_chrome_falls_back_to_default_browser():
    from job_agent.dashboard.service import open_in_chrome

    def boom(cmd, **kw):
        raise OSError("no chrome")

    fell_back = []
    via = open_in_chrome("https://x.test/a", _run=boom, _fallback=fell_back.append)
    assert via == "default-browser"
    assert fell_back == ["https://x.test/a"]
