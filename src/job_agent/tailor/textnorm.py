"""Text normalization shared by rendering and the no-drift gate.

Models don't always honor "no markdown": they wrap labels in ``**bold**`` and use
en/em dashes in dates. Both the renderer and the verifier must see through that,
or the PDF leaks ``**`` and — worse — the no-drift gate parses nothing and passes
vacuously. This centralizes the cleanup so both agree.
"""

from __future__ import annotations

import re

# Normalize dash variants so "Jul 2024 – Present" == "Jul 2024 - Present".
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-"})
_EMPHASIS = re.compile(r"\*\*|__|`")
_HEADING_MD = re.compile(r"^\s{0,3}#{1,6}\s*")
_QUOTE_MD = re.compile(r"^\s*>\s?")


def norm(text: str) -> str:
    """Comparison key for fixed facts: case-, whitespace-, and dash-insensitive,
    with '&'≡'and' and periods/commas dropped (cosmetic differences a model may
    introduce without changing the employer's identity, e.g. 'Chase & Co.' vs
    'Chase and Co')."""
    text = text.translate(_DASHES).replace("&", " and ")
    text = re.sub(r"[.,]", " ", text)
    return " ".join(text.split()).strip().lower()


def strip_markdown(line: str) -> str:
    """Remove markdown decoration a model may add (bold, heading, blockquote)."""
    line = _HEADING_MD.sub("", line)
    line = _QUOTE_MD.sub("", line)
    return _EMPHASIS.sub("", line).strip()
