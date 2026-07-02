"""CareerFacts loading, allow-lists, immutability, and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

import job_agent.tailor as tailor_pkg
from job_agent.tailor.career_facts import load_career_facts

DEMO = Path(tailor_pkg.__file__).parent / "demo"


def test_load_demo_facts():
    cf = load_career_facts(DEMO / "demo_career_facts.yaml")
    assert cf.name == "Jordan Rivers"
    assert len(cf.employers) == 2
    assert ("acme analytics", "data engineer", "jan 2022 - present") in cf.employer_identities()
    assert cf.certification_names() == {"aws certified data engineer - associate"}
    assert {"python", "sql"} <= cf.real_skills()


def test_facts_are_frozen():
    cf = load_career_facts(DEMO / "demo_career_facts.yaml")
    with pytest.raises(Exception):
        cf.employers[0].company = "Changed Corp"


def test_invalid_facts_raise(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nrole: y\n")  # no employers (min_length=1)
    with pytest.raises(ValueError):
        load_career_facts(bad)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_career_facts(tmp_path / "nope.yaml")
