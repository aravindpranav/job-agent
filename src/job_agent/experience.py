"""Parse the years-of-experience a job description *requires*.

Used by the search experience filter (``passes_experience``) to drop roles whose
JD asks for clearly more years than the candidate has. Only requirement-style
phrasings count ("8+ years", "at least 8 years", "minimum of 8 years", "requires
8 years", "... required"), so ordinary prose like "over the past 5 years" never
triggers a drop.
"""

from __future__ import annotations

import re

_REQ_PATTERNS: tuple[str, ...] = (
    r"(\d{1,2})\s*\+\s*years",                    # "8+ years"
    r"(\d{1,2})\s*-\s*\d{1,2}\s*years",           # "5-8 years" -> 5 (lower bound)
    r"at least\s*(\d{1,2})\s*years",
    r"minimum(?:\s*of)?\s*(\d{1,2})\s*years",
    r"(\d{1,2})\s*or more\s*years",
    r"requires?\s*(\d{1,2})\s*years",
    r"(\d{1,2})\s*years?[^.]{0,30}\brequired\b",
)


def required_years(text: str) -> int | None:
    """Largest requirement-style 'N years' figure in ``text``, or None.

    Returns the max across matches so an overall "8+ years" gate is honored even
    when smaller per-skill minimums ("3+ years Python") appear alongside it.
    """
    low = (text or "").lower()
    nums = [int(n) for pat in _REQ_PATTERNS for n in re.findall(pat, low)]
    return max(nums) if nums else None
