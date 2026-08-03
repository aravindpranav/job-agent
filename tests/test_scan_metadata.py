"""New-job tracking + scan metadata.

Properties pinned here:
  * every survivor gets a first-seen record (get-or-record, idempotent);
  * pre-recorded entries change NO freshness decision for dated jobs;
  * newness is an explicit persisted boolean (first_seen_this_scan), never a
    timestamp-equality check — it survives a save/load round-trip;
  * baseline rule: a scan starting from an empty seen cache stores
    new_count=None and badges nothing (badging every job would mislead).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from job_agent.config import LocationRule, SearchProfile, SourceRef
from job_agent.models import Job, ScoredJob
from job_agent.search import run
from job_agent.seen_cache import SeenCache
from job_agent.sources.base import JobSource
from job_agent.store import load_job_record, save_search

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def make_job(**kw) -> Job:
    base = dict(id="1", title="Machine Learning Engineer", company="Acme",
                location="Remote (US)", url="http://x", source="demo",
                remote=True, country="US", posted_at=NOW - timedelta(days=1))
    base.update(kw)
    return Job(**base)


def profile() -> SearchProfile:
    return SearchProfile(
        keywords=["machine learning"],
        location=LocationRule(remote_ok=True, allowed_countries=["US"]),
        sources=[SourceRef(ats="fake", board="b")],
        candidate_summary="x")


class FakeSource(JobSource):
    ats = "fake"

    def __init__(self, board, jobs):
        super().__init__(board)
        self._jobs = jobs

    def fetch(self):
        return self._jobs


def factory(jobs):
    return lambda ats, board: FakeSource(board, jobs)


# --- seen cache -----------------------------------------------------------------

def test_first_seen_records_once_and_reports_newness(tmp_path):
    c = SeenCache(tmp_path / "s.json")
    ts, newly = c.first_seen("fake", "1", NOW)
    assert newly is True and ts == NOW
    later = NOW + timedelta(hours=2)
    ts2, newly2 = c.first_seen("fake", "1", later)
    assert newly2 is False and ts2 == NOW      # existing entry untouched


def test_observe_behavior_is_preserved(tmp_path):
    c = SeenCache(tmp_path / "s.json")
    assert c.observe("fake", "9", NOW) == NOW              # records when absent
    assert c.observe("fake", "9", NOW + timedelta(days=2)) == NOW


# --- pipeline recording ----------------------------------------------------------

def test_run_records_every_survivor_and_flags_baseline(tmp_path):
    cache = SeenCache(tmp_path / "s.json")
    out = run(profile(), seen_cache=cache, now=NOW, fresh_window=timedelta(days=30),
              source_factory=factory([make_job(id="1")]))
    assert out.baseline_scan is True            # cache was empty when scan started
    assert out.first_seen["1"] == NOW.isoformat()
    assert out.new_job_ids == ("1",)
    # persisted: a fresh cache instance already knows the job
    again = SeenCache(tmp_path / "s.json")
    _, newly = again.first_seen("demo", "1", NOW + timedelta(days=1))
    assert newly is False


def test_second_scan_reports_only_genuinely_new_ids(tmp_path):
    cache_path = tmp_path / "s.json"
    run(profile(), seen_cache=SeenCache(cache_path), now=NOW,
        fresh_window=timedelta(days=30),
        source_factory=factory([make_job(id="1")]))
    out2 = run(profile(), seen_cache=SeenCache(cache_path),
               now=NOW + timedelta(hours=6), fresh_window=timedelta(days=30),
               source_factory=factory([make_job(id="1"),
                                       make_job(id="2", title="Machine Learning Engineer, Ads")]))
    assert out2.baseline_scan is False
    assert out2.new_job_ids == ("2",)
    assert out2.first_seen["1"] == NOW.isoformat()          # original stamp kept


def test_prerecorded_entries_change_no_freshness_decision(tmp_path):
    # A dated job past the window must STILL be dropped even though the cache
    # carries a fresh first-seen entry for it (posted_at short-circuits).
    cache = SeenCache(tmp_path / "s.json")
    cache.first_seen("demo", "old", NOW)
    out = run(profile(), seen_cache=cache, now=NOW, fresh_window=timedelta(days=30),
              source_factory=factory([
                  make_job(id="old", posted_at=NOW - timedelta(days=40)),
                  make_job(id="new"),
              ]))
    assert [j.id for j in out.jobs] == ["new"]


# --- persistence -----------------------------------------------------------------

def _scored(*jobs):
    return [ScoredJob(job=j, score=80, verdict="strong") for j in jobs]


def test_save_search_persists_metadata_and_badge_roundtrip(tmp_path):
    path = tmp_path / "last_search.json"
    save_search(_scored(make_job(id="1"), make_job(id="2")), ["b", "b"], path,
                first_seen={"1": "2026-07-28T10:00:00+00:00",
                            "2": NOW.isoformat()},
                new_job_ids=("2",), baseline=False, sources_queried=158)
    data = json.loads(path.read_text())
    assert data["meta"]["total"] == 2
    assert data["meta"]["new_count"] == 1
    assert data["meta"]["sources_queried"] == 158
    assert data["jobs"]["1"]["first_seen_this_scan"] is False
    assert data["jobs"]["2"]["first_seen_this_scan"] is True
    assert data["jobs"]["2"]["first_seen"] == NOW.isoformat()
    # the badge boolean survives the reader used everywhere else too
    assert load_job_record(path, "2")["first_seen_this_scan"] is True
    assert load_job_record(path, "1")["first_seen_this_scan"] is False


def test_baseline_scan_stores_null_new_count_and_no_badges(tmp_path):
    path = tmp_path / "last_search.json"
    save_search(_scored(make_job(id="1"), make_job(id="2")), ["b", "b"], path,
                first_seen={"1": NOW.isoformat(), "2": NOW.isoformat()},
                new_job_ids=("1", "2"), baseline=True, sources_queried=158)
    data = json.loads(path.read_text())
    assert data["meta"]["new_count"] is None
    assert all(r["first_seen_this_scan"] is False for r in data["jobs"].values())


def test_unavailable_sources_count_is_omitted_not_zero(tmp_path):
    path = tmp_path / "last_search.json"
    save_search(_scored(make_job(id="1")), ["b"], path,
                first_seen={"1": NOW.isoformat()}, new_job_ids=(), baseline=False)
    assert "sources_queried" not in json.loads(path.read_text())["meta"]
