"""Parsing the required years-of-experience out of a JD (hard vs soft)."""

from __future__ import annotations

import pytest

from job_agent.experience import required_years


@pytest.mark.parametrize("text,expected", [
    ("We require 8+ years of experience.", 8),
    ("Minimum of 10 years in ML.", 10),
    ("At least 7 years building data platforms.", 7),
    ("8 years of experience required.", 8),
    ("Requires 12 years of leadership.", 12),
    ("5-8 years of experience required.", 5),         # range -> lower bound
    ("3+ years Python and 8+ years overall required.", 8),   # max of hard matches
])
def test_hard_requirements_detected(text, expected):
    assert required_years(text) == expected


@pytest.mark.parametrize("text", [
    "8+ years preferred (or equivalent).",     # soft cue -> advisory, not a gate
    "10+ years preferred.",
    "Ideally 9 years of experience.",
    "8 years of experience is a plus.",
    "8+ years of experience.",                 # bare figure, no hard cue -> soft
    "5-8 years of experience.",
])
def test_soft_or_bare_figures_are_not_requirements(text):
    assert required_years(text) is None


def test_soft_cue_wins_over_hard_cue():
    # "required ... or equivalent" is not an unambiguous gate.
    assert required_years("8 years required, or equivalent experience.") is None


@pytest.mark.parametrize("text", [
    "",
    "Great team that has shipped over the past 5 years.",   # prose, not a requirement
    "You will grow a lot here.",
    "Founded 10 years ago.",
])
def test_no_requirement_returns_none(text):
    assert required_years(text) is None
