"""Title -> seniority level detection."""

from __future__ import annotations

import pytest

from job_agent.seniority import LEVEL_NAMES, seniority_rank


@pytest.mark.parametrize("title,expected", [
    ("Data Engineer", "mid"),
    ("Machine Learning Engineer", "mid"),
    ("Senior Data Engineer", "senior"),
    ("Sr. Data Scientist", "senior"),
    ("Lead Data Scientist", "lead"),
    ("Staff ML Engineer", "staff"),
    ("Senior Staff Engineer", "staff"),          # highest marker wins
    ("Principal AI Engineer", "principal"),
    ("Director of Data", "director"),
    ("Head of Machine Learning", "director"),
    ("VP of Engineering", "vp"),
    ("Junior Data Analyst", "junior"),
])
def test_seniority_rank(title, expected):
    assert seniority_rank(title) == LEVEL_NAMES[expected]


@pytest.mark.parametrize("title", [
    "Staffing Coordinator",       # 'staff' inside 'staffing' must NOT match
    "Leading Analytics Partner",  # 'lead' inside 'leading'
    "Leadership Data Analyst",    # 'lead' inside 'leadership'
    "Headcount Planning Analyst", # 'head' inside 'headcount'
])
def test_no_false_marker_hits(title):
    assert seniority_rank(title) == LEVEL_NAMES["mid"]


def test_untitled_defaults_to_mid():
    assert seniority_rank("") == LEVEL_NAMES["mid"]
    assert seniority_rank("Analytics Engineer") == LEVEL_NAMES["mid"]
