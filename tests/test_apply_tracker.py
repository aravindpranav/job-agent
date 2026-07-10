"""Application tracking: the gitignored data/applications.json log + CLI table."""

from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path

import pytest
from rich.console import Console

from job_agent.apply.answer_bank import AnswerBank, Contact
from job_agent.apply.prompt_io import ScriptedIO
from job_agent.apply.tracker import (
    ApplicationRecord,
    load_applications,
    record_attempt,
    update_status,
)

BANK = AnswerBank.model_validate({"authorized_us": True, "requires_sponsorship": False})
CONTACT = Contact(name="Jordan Rivers", email="j@example.com", phone="1")


def _record(**kw) -> ApplicationRecord:
    base = dict(company="Plaid", title="ML Engineer", job_id="j1",
                date="2026-07-08T12:00:00+00:00", source="ashby", status="paused")
    base.update(kw)
    return ApplicationRecord(**base)


# --- the log file -----------------------------------------------------------------

def test_record_attempt_appends_and_round_trips(tmp_path):
    log = tmp_path / "applications.json"
    record_attempt(log, _record())
    record_attempt(log, _record(company="Stripe", job_id="j2", status="submitted"))
    records = load_applications(log)
    assert [r.company for r in records] == ["Plaid", "Stripe"]
    assert records[1].status == "submitted"
    assert records[0].source == "ashby"


def test_update_status_touches_only_that_attempt(tmp_path):
    log = tmp_path / "applications.json"
    first = record_attempt(log, _record(job_id="j1"))
    second = record_attempt(log, _record(company="Stripe", job_id="j2"))
    update_status(log, second, "failed", "browser crashed")
    records = load_applications(log)
    assert records[0].status == "paused"            # untouched
    assert records[1].status == "failed"
    assert records[1].reason == "browser crashed"
    assert first != second                          # attempt ids are distinct


def test_missing_or_corrupt_log_reads_as_empty(tmp_path):
    assert load_applications(tmp_path / "nope.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_applications(bad) == []


def test_record_status_is_validated():
    with pytest.raises(ValueError):
        _record(status="on-fire")


# --- user-managed state: status pipeline, notes, follow-up --------------------------

def test_user_statuses_are_valid_alongside_run_statuses():
    for status in ("saved", "applied", "interviewing", "offer", "rejected"):
        assert _record(status=status).status == status


def test_upsert_creates_a_saved_record_for_a_new_job(tmp_path):
    from job_agent.apply.tracker import upsert_job_state
    log = tmp_path / "applications.json"
    rec = upsert_job_state(log, job_id="j9", company="Figma", title="DS",
                           source="greenhouse", notes="met recruiter Sam")
    assert rec.status == "saved"                    # default for a fresh save
    assert rec.notes == "met recruiter Sam"
    (loaded,) = load_applications(log)
    assert loaded.job_id == "j9" and loaded.date    # stamped


def test_upsert_updates_the_existing_record_not_a_duplicate(tmp_path):
    from job_agent.apply.tracker import upsert_job_state
    log = tmp_path / "applications.json"
    record_attempt(log, _record(job_id="j1", status="paused"))
    rec = upsert_job_state(log, job_id="j1", status="interviewing",
                           follow_up="2026-07-20")
    records = load_applications(log)
    assert len(records) == 1                        # updated in place, no dup
    assert records[0].status == "interviewing"
    assert records[0].follow_up == "2026-07-20"
    assert records[0].company == "Plaid"            # untouched fields preserved
    assert rec.status == "interviewing"


def test_upsert_leaves_unspecified_fields_alone(tmp_path):
    from job_agent.apply.tracker import upsert_job_state
    log = tmp_path / "applications.json"
    upsert_job_state(log, job_id="j1", company="Plaid", notes="note A")
    upsert_job_state(log, job_id="j1", status="applied")     # no notes given
    (rec,) = load_applications(log)
    assert rec.notes == "note A" and rec.status == "applied"


def test_upsert_requires_a_job_id(tmp_path):
    from job_agent.apply.tracker import upsert_job_state
    with pytest.raises(ValueError):
        upsert_job_state(tmp_path / "a.json", job_id="", status="saved")


# --- already-applied markers (feeds the search views' exclusion) ---------------------

def test_applied_markers_cover_the_in_flight_statuses_only(tmp_path):
    from job_agent.apply.tracker import applied_markers, is_already_applied
    log = tmp_path / "applications.json"
    record_attempt(log, _record(job_id="a1", status="applied", company="Plaid",
                                title="ML Engineer"))
    record_attempt(log, _record(job_id="a2", status="submitted", company="Snowflake",
                                title="Senior AI Engineer"))
    record_attempt(log, _record(job_id="a3", status="paused", company="Stripe",
                                title="AI Engineer"))
    record_attempt(log, _record(job_id="a4", status="saved", company="Figma",
                                title="DS"))
    markers = applied_markers(log)
    assert is_already_applied(markers, job_id="a1", company="", title="")
    assert is_already_applied(markers, job_id="a2", company="", title="")
    assert not is_already_applied(markers, job_id="a3", company="", title="")   # paused
    assert not is_already_applied(markers, job_id="a4", company="", title="")   # saved


def test_company_title_rematch_catches_a_different_job_id(tmp_path):
    # sr-search ids can differ across runs — the same role must still be caught.
    from job_agent.apply.tracker import applied_markers, is_already_applied
    log = tmp_path / "applications.json"
    record_attempt(log, _record(job_id="old-123", status="applied",
                                company="ServiceNow",
                                title="Machine Learning Engineer, Agentic Systems"))
    markers = applied_markers(log)
    assert is_already_applied(markers, job_id="new-999", company="ServiceNow",
                              title="Machine  Learning Engineer, Agentic Systems ")
    assert not is_already_applied(markers, job_id="new-999", company="ServiceNow",
                                  title="Staff Data Scientist")


def test_cli_search_splits_out_applied_jobs_and_flag_includes_them(tmp_path):
    from job_agent.apply.tracker import applied_markers
    from job_agent.cli import _build_parser, _split_applied
    from job_agent.models import Job, ScoredJob

    log = tmp_path / "applications.json"
    record_attempt(log, _record(job_id="snow-1", status="submitted",
                                company="Snowflake", title="Senior AI Engineer"))
    scored = [
        ScoredJob(job=Job(id="snow-1", title="Senior AI Engineer", company="Snowflake",
                          location="US", url="http://x", source="ashby"),
                  score=80, verdict="strong"),
        ScoredJob(job=Job(id="new-1", title="Data Scientist", company="Figma",
                          location="US", url="http://y", source="greenhouse"),
                  score=70, verdict="possible"),
    ]
    fresh, hidden = _split_applied(scored, applied_markers(log))
    assert [s.job.id for s in fresh] == ["new-1"]
    assert [s.job.id for s in hidden] == ["snow-1"]      # excluded from the table
    # the CLI flag exists and defaults to hiding
    assert _build_parser().parse_args(["search"]).include_applied is False
    assert _build_parser().parse_args(["search", "--include-applied"]).include_applied is True


# --- runner integration -------------------------------------------------------------

class _Page:
    """A page with no form and no blockers — the minimal happy path."""

    def goto(self, url, wait_until=None):
        pass

    def inner_text(self, selector):
        return ""

    def evaluate(self, js):
        return []


def _config(tmp_path, **kw):
    from job_agent.apply.runner import ApplyConfig
    base = dict(
        apply_url="http://example.test/apply", bank=BANK, contact=CONTACT,
        out_dir=tmp_path / "apply", auto_approve=True,
        job_label="Plaid — ML Engineer", company="Plaid",
        job_id="j-123", source="ashby", job_title="ML Engineer",
        applications_log=tmp_path / "applications.json",
    )
    base.update(kw)
    return ApplyConfig(**base)


@contextmanager
def _fake_browser(headless=False):
    yield _Page()


def test_apply_run_opens_the_configured_apply_url_verbatim(tmp_path, monkeypatch):
    """Regression pin (sr-search): the browser must open cfg.apply_url exactly —
    query string included — never a company/board-derived URL."""
    import job_agent.apply.runner as runner

    sr_apply_url = ("https://jobs.smartrecruiters.com/GinasTechJobs/"
                    "744000136873684-senior-machine-learning-engineer?oga=true")
    gotos: list[str] = []

    class _NavPage(_Page):
        def goto(self, url, wait_until=None):
            gotos.append(url)

    @contextmanager
    def browser(headless=False):
        yield _NavPage()

    monkeypatch.setattr(runner, "open_browser", browser)
    runner.run_apply(_config(tmp_path, apply_url=sr_apply_url, source="sr-search",
                             company="Ginas Tech Jobs"),
                     io=ScriptedIO(answers=[]).as_io())
    assert gotos == [sr_apply_url]


def test_apply_run_appends_a_tracked_record(tmp_path, monkeypatch):
    import job_agent.apply.runner as runner
    monkeypatch.setattr(runner, "open_browser", _fake_browser)
    result = runner.run_apply(_config(tmp_path), io=ScriptedIO(answers=[]).as_io())
    assert result.status == "dry_run"               # approved, but no --submit
    (rec,) = load_applications(tmp_path / "applications.json")
    assert (rec.company, rec.title, rec.job_id, rec.source) == \
        ("Plaid", "ML Engineer", "j-123", "ashby")
    assert rec.status == "paused"                   # dry-run: nothing was sent
    assert rec.date                                 # stamped


def test_submitted_run_is_tracked_as_submitted(tmp_path, monkeypatch):
    import job_agent.apply.runner as runner

    class _SubmitPage(_Page):
        def locator(self, sel):
            page = self

            class _Loc:
                def count(self):
                    return 1

                @property
                def first(self):
                    return self

                def click(self):
                    pass
            return _Loc()

        def wait_for_timeout(self, ms):
            pass

        def screenshot(self, path):
            Path(path).write_bytes(b"png")

    @contextmanager
    def browser(headless=False):
        yield _SubmitPage()
    monkeypatch.setattr(runner, "open_browser", browser)
    result = runner.run_apply(_config(tmp_path, submit_flag=True),
                              io=ScriptedIO(answers=[]).as_io())
    assert result.status == "submitted"
    (rec,) = load_applications(tmp_path / "applications.json")
    assert rec.status == "submitted"


def test_crashed_run_is_tracked_as_failed(tmp_path, monkeypatch):
    import job_agent.apply.runner as runner

    class _BoomPage(_Page):
        def evaluate(self, js):
            raise RuntimeError("browser died")

    @contextmanager
    def browser(headless=False):
        yield _BoomPage()
    monkeypatch.setattr(runner, "open_browser", browser)
    with pytest.raises(RuntimeError):
        runner.run_apply(_config(tmp_path), io=ScriptedIO(answers=[]).as_io())
    (rec,) = load_applications(tmp_path / "applications.json")
    assert rec.status == "failed"


# --- CLI: `job_agent applications` ---------------------------------------------------

def test_applications_command_renders_the_table_most_recent_first(tmp_path):
    from job_agent.cli import cmd_applications
    log = tmp_path / "applications.json"
    record_attempt(log, _record(company="Plaid", date="2026-07-07T10:00:00+00:00"))
    record_attempt(log, _record(company="Stripe", title="AI Engineer", job_id="j2",
                                source="greenhouse", status="submitted",
                                date="2026-07-08T09:00:00+00:00"))
    console = Console(record=True, width=120)
    assert cmd_applications(console, Namespace(log=str(log))) == 0
    out = console.export_text()
    assert "Plaid" in out and "Stripe" in out
    assert "submitted" in out and "paused" in out
    assert out.index("Stripe") < out.index("Plaid")     # most recent first


def test_applications_command_with_empty_log_says_so(tmp_path):
    from job_agent.cli import cmd_applications
    console = Console(record=True, width=120)
    assert cmd_applications(console, Namespace(log=str(tmp_path / "none.json"))) == 0
    assert "No applications" in console.export_text()
