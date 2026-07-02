"""Test helpers (importable from test modules)."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict | list:
    """Load a saved real-shape ATS response body."""
    return json.loads((FIXTURES / name).read_text())
