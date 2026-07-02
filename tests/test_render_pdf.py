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
    assert "Jordan Rivers" in text                     # real selectable text
    assert "PROFESSIONAL EXPERIENCE" in text.upper()   # headings present (CAPS)


def test_pdf_has_real_extractable_bullets_no_unmapped_glyphs(tmp_path):
    pdf = render_pdf(RESULT.resume_text, tmp_path / "resume.pdf")
    text = extract_pdf_text(pdf)
    assert "(cid:" not in text            # every glyph maps to real Unicode
    assert "•" in text                    # the bullet is a real, extractable character
    assert "Airflow" in text              # bullet-line words extract cleanly


def test_pdf_missing_standard_section_is_rejected(tmp_path):
    incomplete = ("Jordan Rivers\ne | p\nPROFESSIONAL SUMMARY\nx\n"
                  "TECHNICAL SKILLS\nx\nPROFESSIONAL EXPERIENCE\nx\nEDUCATION\nx\n")  # no Certifications
    pdf = render_pdf(incomplete, tmp_path / "bad.pdf")
    with pytest.raises(PdfVerifyError, match="Certifications"):
        verify_pdf(pdf)
