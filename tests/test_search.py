"""Pipeline filters and orchestration (deterministic, offline)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from job_agent.config import LocationRule, SearchProfile, SourceRef
from job_agent.models import Job
from job_agent.seen_cache import SeenCache
from job_agent.search import (
    is_fresh,
    matches_keywords,
    passes_experience,
    passes_location,
    passes_seniority,
    run,
)
from job_agent.sources.base import JobSource

NOW = datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)


def make_job(**kw) -> Job:
    base = dict(id="1", title="t", company="c", location="l", url="http://x", source="demo")
    base.update(kw)
    return Job(**base)


def profile(**overrides) -> SearchProfile:
    data = dict(
        keywords=["data engineer", "machine learning"],
        location=LocationRule(remote_ok=True, allowed_countries=["US"]),
        sources=[SourceRef(ats="fake", board="b")],
        candidate_summary="x",
    )
    data.update(overrides)
    return SearchProfile(**data)


class FakeSource(JobSource):
    ats = "fake"

    def __init__(self, board, jobs=None, raises=False):
        super().__init__(board)
        self._jobs = jobs or []
        self._raises = raises

    def fetch(self):
        if self._raises:
            raise RuntimeError("boom")
        return self._jobs


# --- unit-level filters -----------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Senior Data Engineer", True),
    ("Machine Learning Scientist", True),
    ("Frontend Engineer", False),
])
def test_matches_keywords(title, expected):
    assert matches_keywords(title, ["data engineer", "machine learning"]) is expected


@pytest.mark.parametrize("remote,country,expected", [
    (True, "US", True),
    (False, "US", True),
    (True, "GB", False),      # remote does not override a known non-US country
    (False, "GB", False),
    (True, None, True),       # unknown + remote -> keep
    (False, None, True),      # unknown + onsite -> keep for scorer
])
def test_passes_location(remote, country, expected):
    job = make_job(remote=remote, country=country)
    assert passes_location(job, profile()) is expected


@pytest.mark.parametrize("location,remote,expected", [
    # Clearly-foreign location text drops the job even with no structured
    # country and even when it's marked remote.
    ("Bengaluru, India", False, False),
    ("Bengaluru", False, False),
    ("Bengaluru, India", True, False),      # foreign + remote still drops
    ("London, UK", False, False),
    ("Remote - India", True, False),
    # US locations (and unknowns) are kept.
    ("Austin, TX", False, True),
    ("Remote (US)", True, True),
    ("Indianapolis, IN", False, True),      # not misread as India
    ("Remote", True, True),                 # unknown + remote -> keep
    ("Anywhere", False, True),              # unknown + onsite -> keep for scorer
])
def test_passes_location_infers_country_from_text(location, remote, expected):
    job = make_job(location=location, remote=remote, country=None)
    assert passes_location(job, profile()) is expected


def test_is_fresh_uses_posted_at():
    assert is_fresh(make_job(posted_at=NOW - timedelta(hours=3)), NOW, SeenCache("/tmp/_a.json"))
    assert not is_fresh(make_job(posted_at=NOW - timedelta(hours=30)), NOW, SeenCache("/tmp/_b.json"))


def test_is_fresh_falls_back_to_seen_cache(tmp_path):
    cache = SeenCache(tmp_path / "seen.json")
    job = make_job(id="42", posted_at=None, source="greenhouse")
    # First time we see a dateless job it's treated as fresh...
    assert is_fresh(job, NOW, cache) is True
    # ...but if we first saw it two days ago, it's stale.
    cache._first_seen["greenhouse:42"] = (NOW - timedelta(days=2)).isoformat()
    assert is_fresh(job, NOW, cache) is False


# --- seniority filter -------------------------------------------------------

@pytest.mark.parametrize("title,kept", [
    ("Data Engineer", True),
    ("Senior Data Engineer", True),
    ("Sr. Machine Learning Engineer", True),
    ("Lead Data Scientist", False),
    ("Staff ML Engineer", False),
    ("Principal AI Engineer", False),
    ("Director of Data", False),
    ("VP of Engineering", False),
])
def test_passes_seniority_ceiling_senior(title, kept):
    prof = profile(max_seniority="senior")
    assert passes_seniority(make_job(title=title), prof) is kept


def test_passes_seniority_off_when_unset():
    # No ceiling -> even a VP passes this stage.
    assert passes_seniority(make_job(title="VP of Engineering"), profile()) is True


# --- experience filter ------------------------------------------------------

@pytest.mark.parametrize("desc,kept", [
    ("8+ years of experience required.", False),      # 8 >= 5+3 -> drop
    ("Minimum of 10 years in ML.", False),
    ("6+ years of experience.", True),                # reachable -> keep
    ("7+ years of experience.", True),
    ("5-8 years of experience.", True),               # lower bound 5 -> keep
    ("Join our team!", True),                         # no stated minimum -> keep
])
def test_passes_experience_gap(desc, kept):
    prof = profile(experience_years=5)
    assert passes_experience(make_job(description=desc), prof) is kept


def test_passes_experience_off_when_unset():
    assert passes_experience(make_job(description="12+ years required"), profile()) is True


# --- full pipeline ----------------------------------------------------------

def test_run_pipeline_counts_and_dedup(tmp_path):
    jobs = [
        make_job(id="1", title="Data Engineer", company="Acme", location="Remote",
                 remote=True, country="US", posted_at=NOW - timedelta(hours=1)),
        make_job(id="2", title="Frontend Engineer", company="Acme", location="Remote",
                 remote=True, country="US", posted_at=NOW - timedelta(hours=1)),          # keyword drop
        make_job(id="3", title="Machine Learning Engineer", company="Gamma", location="London",
                 remote=False, country="GB", posted_at=NOW - timedelta(hours=1)),          # location drop
        make_job(id="4", title="Data Engineer", company="Delta", location="Remote",
                 remote=True, country="US", posted_at=NOW - timedelta(hours=40)),          # freshness drop
        make_job(id="5", title="Machine Learning Engineer", company="Beta", location="Remote",
                 remote=True, country="US", posted_at=NOW - timedelta(hours=1)),
        make_job(id="6", title="Machine Learning Engineer", company="Beta", location="Remote",
                 remote=True, country="US", posted_at=NOW - timedelta(hours=1)),           # dup of 5
    ]
    factory = lambda ats, board: FakeSource(board, jobs)
    out = run(profile(), seen_cache=SeenCache(tmp_path / "s.json"), now=NOW, source_factory=factory)

    assert out.counts.fetched == 6
    assert out.counts.after_keyword == 5
    assert out.counts.after_fresh == 4
    assert out.counts.after_location == 3
    assert out.counts.after_dedup == 2
    assert {j.title for j in out.jobs} == {"Data Engineer", "Machine Learning Engineer"}


def test_run_pipeline_drops_over_level_and_over_experience(tmp_path):
    fresh = NOW - timedelta(hours=1)
    jobs = [
        make_job(id="1", title="Senior Data Engineer", company="Acme", location="Remote",
                 remote=True, country="US", posted_at=fresh, description="3+ years."),
        make_job(id="2", title="Staff Data Engineer", company="Acme", location="Remote",
                 remote=True, country="US", posted_at=fresh, description="3+ years."),   # seniority drop
        make_job(id="3", title="Senior Machine Learning Engineer", company="Beta", location="Remote",
                 remote=True, country="US", posted_at=fresh,
                 description="We need 10+ years of experience."),                        # experience drop
        make_job(id="4", title="Machine Learning Engineer", company="Gamma", location="Remote",
                 remote=True, country="US", posted_at=fresh, description="5+ years."),
    ]
    factory = lambda ats, board: FakeSource(board, jobs)
    prof = profile(max_seniority="senior", experience_years=5)
    out = run(prof, seen_cache=SeenCache(tmp_path / "s.json"), now=NOW, source_factory=factory)

    assert out.counts.after_location == 4
    assert out.counts.after_seniority == 3     # Staff dropped (title, before dedup)
    assert out.counts.after_dedup == 3
    assert out.counts.after_experience == 2    # "10+ years" dropped (after enrich)
    assert {j.title for j in out.jobs} == {"Senior Data Engineer", "Machine Learning Engineer"}


def test_run_source_failure_is_a_warning_not_a_crash(tmp_path):
    factory = lambda ats, board: FakeSource(board, raises=True)
    out = run(profile(), seen_cache=SeenCache(tmp_path / "s.json"), now=NOW, source_factory=factory)
    assert out.jobs == []
    assert out.warnings and "fetch failed" in out.warnings[0]
