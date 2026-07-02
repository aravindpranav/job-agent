"""No-drift gate: fabrication is rejected; honest output + placeholders pass."""

from __future__ import annotations

from pathlib import Path

import pytest

import job_agent.tailor as tailor_pkg
from job_agent.tailor.career_facts import load_career_facts
from job_agent.tailor.tailor import TailorResult, tailor_resume
from job_agent.tailor.verify import DriftError, FormatError, verify_format, verify_no_drift

DEMO = Path(tailor_pkg.__file__).parent / "demo"
FACTS = load_career_facts(DEMO / "demo_career_facts.yaml")
BASE = tailor_resume(FACTS, (DEMO / "demo_jd.txt").read_text(),
                     stub_response=(DEMO / "demo_response.txt").read_text(), megaprompt="(x)")


def _mutated(resume_text: str) -> TailorResult:
    return TailorResult(resume_text=resume_text, notes=BASE.notes, raw=resume_text)


def test_control_passes_both_gates_with_no_placeholders():
    report = verify_no_drift(BASE, FACTS)
    assert report.ok
    assert report.placeholders == ()      # policy: no [METRIC …] on the résumé face
    verify_format(BASE, FACTS)            # must not raise


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
    with pytest.raises(DriftError, match="not among the banked real metrics"):
        verify_no_drift(_mutated(mutated), FACTS)


def test_metric_value_not_in_banked_list_is_rejected():
    # 40% is a real-looking figure but not in the demo's banked metrics (30, 5,000,000).
    mutated = BASE.resume_text.replace("Reduced pipeline runtime by 30%",
                                       "Improved model accuracy by 40%")
    with pytest.raises(DriftError, match="banked"):
        verify_no_drift(_mutated(mutated), FACTS)


def test_to_value_handles_magnitudes_and_plus():
    from job_agent.tailor.verify import _to_value
    assert _to_value("1,000+") == 1000
    assert _to_value("500K") == 500000
    assert _to_value("2M") == 2000000
    assert _to_value("99.9") == 99.9


# The model may emit markdown (**Company:**), en-dash dates, and an inline
# location on the company line. The gate must see through all of that — and must
# NOT pass vacuously when it can't parse the employers.

_MD_RESUME = """Jordan Rivers
Data Engineer | jordan.rivers@example.com | +1 (555) 010-0100

## Professional Summary
Data engineer on AWS.

## Technical Skills
Python, SQL

## Professional Experience
**Role:** Data Engineer
**Company:** Acme Analytics — Remote, US
**Project Description:** Pipelines.
**Duration:** Jan 2022 – Present
**Responsibilities:**
- Built Apache Airflow DAGs.
**Achievements:**
- Processed 5,000,000 records/day.

**Role:** Data Analyst
**Company:** Beacon Software — Austin, TX
**Duration:** Jun 2019 – Dec 2021

## Education
B.S., Computer Science, State University

## Certifications
AWS Certified Data Engineer - Associate
"""


def test_markdown_wrapped_legit_output_passes():
    report = verify_no_drift(_mutated(_MD_RESUME), FACTS)
    assert report.ok
    assert len(report.output_employers) == 2  # employers WERE parsed, not skipped


def test_markdown_wrapped_fake_employer_is_rejected():
    bad = _MD_RESUME.replace("**Company:** Beacon Software — Austin, TX",
                             "**Company:** Shadow Corp — Remote")
    with pytest.raises(DriftError, match="Shadow Corp"):
        verify_no_drift(_mutated(bad), FACTS)


def test_unparseable_employers_fail_loudly_not_vacuously():
    text = ("Jordan Rivers\nData Engineer | e | p\nProfessional Summary\nx\n"
            "Professional Experience\nWorked at various places.\nEducation\nx\n"
            "Certifications\nNone")
    with pytest.raises(DriftError, match="Could not find any employer"):
        verify_no_drift(_mutated(text), FACTS)


# --- format gate ------------------------------------------------------------

def test_format_gate_rejects_bracket_placeholder():
    bad = BASE.resume_text.replace("Reduced pipeline runtime by 30%",
                                   "Reduced pipeline runtime by [METRIC — runtime?]")
    with pytest.raises(FormatError, match="[Bb]racket"):
        verify_format(_mutated(bad), FACTS)


def test_format_gate_rejects_em_dash():
    bad = BASE.resume_text.replace("Acme Analytics", "Acme Analytics — West")
    with pytest.raises(FormatError, match="[Ee]m-dash"):
        verify_format(_mutated(bad), FACTS)


def test_format_gate_rejects_pipe_table_line():
    bad = BASE.resume_text.replace("Languages: Python, SQL",
                                   "| Skill | Level |\n| Python | Expert |")
    with pytest.raises(FormatError, match="[Pp]ipe"):
        verify_format(_mutated(bad), FACTS)


def test_format_gate_requires_certifications_printed():
    bad = BASE.resume_text.replace("AWS Certified Data Engineer - Associate", "None")
    with pytest.raises(FormatError, match="[Cc]ertification"):
        verify_format(_mutated(bad), FACTS)
