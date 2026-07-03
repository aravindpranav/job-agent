"""Detect a job title's seniority level.

Used by the search seniority filter (``passes_seniority``) to drop titles above
a configured ceiling before scoring. Kept deliberately small and pure so it is
easy to unit-test and reuse.
"""

from __future__ import annotations

import re

# Canonical level name -> rank. Higher rank = more senior. "mid" (2) is the
# default when a title carries no seniority marker. These names are the accepted
# values for ``max_seniority`` in the search profile.
LEVEL_NAMES: dict[str, int] = {
    "entry": 1, "junior": 1, "associate": 1,
    "mid": 2,
    "senior": 3,
    "lead": 4,
    "staff": 5,
    "principal": 6,
    "director": 7,
    "vp": 8,
}

# Title words (matched whole-word, case-insensitive) -> rank, highest first. The
# first marker found wins, so "Senior Staff Engineer" resolves to staff (5).
_MARKERS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (8, ("vp", "svp", "evp", "vice president")),
    (7, ("director", "head", "distinguished", "fellow")),
    (6, ("principal",)),
    (5, ("staff",)),
    (4, ("lead",)),
    (3, ("senior", "sr", "snr")),
    (1, ("junior", "jr", "associate", "intern", "entry", "new grad", "graduate")),
)


def seniority_rank(title: str) -> int:
    """Return the title's level rank (mid=2 when no marker is present).

    Whole-word matching keeps ``staff`` out of "staffing", ``lead`` out of
    "leading"/"leadership", and ``head`` out of "headcount".
    """
    low = (title or "").lower()
    for rank, words in _MARKERS:
        if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
            return rank
    return 2  # mid / unspecified
