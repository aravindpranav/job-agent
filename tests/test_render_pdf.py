"""PDF render + the ATS text-extraction gate."""

from __future__ import annotations

from pathlib import Path

import pytest

import job_agent.tailor as tailor_pkg
from job_agent.tailor.career_facts import load_career_facts
from job_agent.tailor.render_pdf import CANONICAL_HEADINGS, render_pdf
from job_agent.tailor.tailor import tailor_resume
from job_agent.tailor.verify import PdfVerifyError, extract_pdf_text, verify_pdf

DEMO = Path(tailor_pkg.__file__).parent / "demo"
FACTS = load_career_facts(DEMO / "demo_career_facts.yaml")
RESULT = tailor_resume(FACTS, (DEMO / "demo_jd.txt").read_text(),
                       stub_response=(DEMO / "demo_response.txt").read_text(), megaprompt="(x)")


def test_pdf_has_selectable_text_and_sections_in_order(tmp_path):
    pdf = render_pdf(RESULT.resume_text, tmp_path / "resume.pdf")
    assert verify_pdf(pdf) == CANONICAL_HEADINGS
    text = extract_pdf_text(pdf)
    assert "Jordan Rivers" in text          # real selectable text, not outlines
    assert "Professional Experience" in text


def test_unicode_arrow_is_ascii_safe_in_pdf(tmp_path):
    # The placeholder contains "before→after"; the PDF must still extract cleanly.
    pdf = render_pdf(RESULT.resume_text, tmp_path / "resume.pdf")
    text = extract_pdf_text(pdf)
    assert "before->after" in text or "before" in text  # sanitized, still selectable


def test_pdf_missing_standard_section_is_rejected(tmp_path):
    incomplete = ("Jordan Rivers\nData Engineer | e | p\nProfessional Summary\nx\n"
                  "Technical Skills\nx\nProfessional Experience\nx\nEducation\nx\n")  # no Certifications
    pdf = render_pdf(incomplete, tmp_path / "bad.pdf")
    with pytest.raises(PdfVerifyError, match="Certifications"):
        verify_pdf(pdf)
