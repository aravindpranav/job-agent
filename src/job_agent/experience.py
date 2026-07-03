"""Parse the years-of-experience a job description *requires*.

Used by the search experience filter (``passes_experience``) to drop roles whose
JD asks for clearly more years than the candidate has. Only an **unambiguously
hard** requirement counts:

* a hard cue must adjoin the years figure — "required", "must", "minimum",
  "at least" ("8 years of experience required", "minimum of 10 years");
* a soft cue adjoining the figure makes it advisory, never a drop — "preferred",
  "or equivalent", "ideally", "nice to have", "a plus" ("8+ years preferred
  (or equivalent)" survives);
* a bare figure with neither ("8+ years of experience.") is treated as soft —
  reachable-role phrasing varies too much to hard-drop on it.
"""

from __future__ import annotations

import re

_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?years?")

#: Words that make an adjoining years figure a hard requirement.
_HARD_CUES = ("required", "require", "must", "minimum", "at least")
#: Words that soften it to advisory — these always win over a hard cue.
_SOFT_CUES = ("prefer", "or equivalent", "ideally", "nice to have", "a plus", "bonus")

_CONTEXT = 40  # chars around the match considered "adjoining"


def required_years(text: str) -> int | None:
    """Largest HARD 'N years' requirement in ``text``, or None.

    Returns the max across hard matches so an overall "minimum 8 years" gate is
    honored even when smaller per-skill minimums appear alongside it.
    """
    low = (text or "").lower()
    hard: list[int] = []
    for m in _YEARS.finditer(low):
        ctx = low[max(0, m.start() - _CONTEXT):m.end() + _CONTEXT]
        if any(cue in ctx for cue in _SOFT_CUES):
            continue                                  # softened — never a drop
        if any(cue in ctx for cue in _HARD_CUES):
            hard.append(int(m.group(1)))              # lower bound of a range
    return max(hard) if hard else None
