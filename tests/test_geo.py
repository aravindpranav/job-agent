"""Country inference from free-text location strings."""

from __future__ import annotations

import pytest

from job_agent.geo import infer_country


@pytest.mark.parametrize("location,expected", [
    # clearly foreign -> dropped by the location filter
    ("Bengaluru, India", "India"),
    ("Bengaluru", "India"),               # bare Indian metro, no country token
    ("Bengaluru, IN", "India"),           # 'IN' reads as India, not Indiana
    ("Hyderabad", "India"),
    ("London, UK", "United Kingdom"),
    ("London", "United Kingdom"),
    ("Toronto, Canada", "Canada"),
    ("Toronto", "Canada"),
    ("Dublin, Ireland", "Ireland"),
    ("Munich", "Germany"),
    ("Remote - India", "India"),
    ("Remote (UK)", "United Kingdom"),
    ("Tel Aviv, Israel", "Israel"),
    ("Singapore", "Singapore"),
    ("Seoul, South Korea", "South Korea"),   # the miss that let job 8574655002 through
    ("Seoul", "South Korea"),
    ("Seoul, Korea", "South Korea"),
    ("Taipei, Taiwan", "Taiwan"),
    ("Hong Kong", "Hong Kong"),
    ("Shanghai", "China"),
    ("Zurich", "Switzerland"),
    ("Stockholm, Sweden", "Sweden"),
    ("Prague", "Czechia"),
    ("São Paulo, Brazil", "Brazil"),
    ("Jakarta, Indonesia", "Indonesia"),
])
def test_clearly_foreign_locations(location, expected):
    assert infer_country(location) == expected


@pytest.mark.parametrize("location", [
    "Austin, TX",
    "New York, NY",
    "San Francisco, CA",
    "Remote, US",
    "Remote (US)",
    "United States",
    "Seattle, Washington",
    "Boston, MA",
    "Indianapolis, IN",       # 'India' must NOT be matched inside 'Indianapolis'
    "Columbus, OH",           # 'us' must NOT be matched inside 'Columbus'
    "Cambridge, MA",          # US Cambridge, not the UK one
    "Portland, OR",           # 'OR' the state, not the word 'or'
    "Vancouver, WA",          # US Vancouver — must not be misread as Canada
    "Dublin, OH",             # US Dublin — must not be misread as Ireland
    "Waterloo, IA",           # US Waterloo — must not be misread as Canada
    "Atlanta, Georgia",       # the US state, not the country Georgia
])
def test_us_locations_are_recognized_as_us(location):
    assert infer_country(location) == "US"


def test_us_wins_when_both_named():
    # A role open to the US and a foreign country is kept as US.
    assert infer_country("Remote - US or India") == "US"
    assert infer_country("United States / Canada") == "US"


@pytest.mark.parametrize("location", ["", "   ", "Remote", "Anywhere", "Hybrid"])
def test_ambiguous_or_empty_is_unknown(location):
    assert infer_country(location) is None


def test_none_is_unknown():
    assert infer_country(None) is None
