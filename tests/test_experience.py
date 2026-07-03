"""Parsing the required years-of-experience out of a JD."""

from __future__ import annotations

import pytest

from job_agent.experience import required_years


@pytest.mark.parametrize("text,expected", [
    ("We require 8+ years of experience.", 8),
    ("Minimum of 10 years in ML.", 10),
    ("At least 7 years building data platforms.", 7),
    ("5-8 years of experience.", 5),                 # range -> lower bound
    ("8 years of experience required.", 8),
    ("Requires 12 years of leadership.", 12),
    ("3+ years Python and 8+ years overall.", 8),    # max of the requirements
])
def test_required_years_detected(text, expected):
    assert required_years(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "Great team that has shipped over the past 5 years.",   # prose, not a requirement
    "You will grow a lot here.",
    "Founded 10 years ago.",                                # not a 'years of experience' requirement
])
def test_no_requirement_returns_none(text):
    assert required_years(text) is None
