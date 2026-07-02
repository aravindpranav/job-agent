"""No-drift gate: fabrication is rejected; honest output + placeholders pass."""

from __future__ import annotations

from pathlib import Path

import pytest

import job_agent.tailor as tailor_pkg
from job_agent.tailor.career_facts import load_career_facts
from job_agent.tailor.tailor import TailorResult, tailor_resume
from job_agent.tailor.verify import DriftError, verify_no_drift

DEMO = Path(tailor_pkg.__file__).parent / "demo"
FACTS = load_career_facts(DEMO / "demo_career_facts.yaml")
BASE = tailor_resume(FACTS, (DEMO / "demo_jd.txt").read_text(),
                     stub_response=(DEMO / "demo_response.txt").read_text(), megaprompt="(x)")


def _mutated(resume_text: str) -> TailorResult:
    return TailorResult(resume_text=resume_text, notes=BASE.notes, raw=resume_text)


def test_control_passes_and_surfaces_placeholder():
    report = verify_no_drift(BASE, FACTS)
    assert report.ok
    assert report.placeholders  # the [METRIC — …] placeholder was detected
    assert not report.placeholders_missing_from_notes  # and it's listed in NOTES


def test_added_employer_is_rejected():
    mutated = BASE.resume_text.replace(
        "Role: Data Analyst\nCompany: Beacon Software",
        "Role: Staff Engineer\nCompany: Shadow Corp\nProject Description: x\n"
        "Duration: Jan 2015 - Dec 2018\nResponsibilities:\n- x\nAchievements:\n- x\n\n"
        "Role: Data Analyst\nCompany: Beacon Software")
    with pytest.raises(DriftError, match="Shadow Corp"):
        verify_no_drift(_mutated(mutated), FACTS)


def test_altered_duration_is_rejected():
    mutated = BASE.resume_text.replace("Duration: Jan 2022 - Present", "Duration: Jan 2018 - Present")
    with pytest.raises(DriftError, match="Altered employer identity"):
        verify_no_drift(_mutated(mutated), FACTS)


def test_uncredentialed_certification_is_rejected():
    mutated = BASE.resume_text.replace(
        "AWS Certified Data Engineer - Associate",
        "AWS Certified Data Engineer - Associate\nGoogle Cloud Professional Data Engineer")
    with pytest.raises(DriftError, match="certification"):
        verify_no_drift(_mutated(mutated), FACTS)


def test_invented_metric_is_rejected():
    mutated = BASE.resume_text.replace("Reduced pipeline runtime by 30%",
                                       "Reduced infrastructure cost by 73%")
    with pytest.raises(DriftError, match="Invented metric"):
        verify_no_drift(_mutated(mutated), FACTS)
