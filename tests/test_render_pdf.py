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


def test_trim_to_caps_limits_bullets_per_section():
    from job_agent.tailor.render_pdf import trim_to_caps
    txt = ("Role: A\nCompany: B\nDuration: d\nResponsibilities:\n"
           + "\n".join(f"- r{i}" for i in range(8))
           + "\nAchievements:\n" + "\n".join(f"- a{i}" for i in range(5))
           + "\nRole: A2\nCompany: B2\nDuration: d2\nResponsibilities:\n"
           + "\n".join(f"- s{i}" for i in range(6)) + "\nEDUCATION\nx")
    out = trim_to_caps(txt)
    assert out.count("- r") == 6   # most-recent role responsibilities capped at 6
    assert out.count("- a") == 3   # achievements capped at 3
    assert out.count("- s") == 4   # older role responsibilities capped at 4


def test_skill_hanging_indent_preserves_extraction_order(tmp_path):
    # A long skills line wraps; its continuation must extract IN ORDER (before the
    # next category), not get reordered by the PDF text extractor.
    txt = ("Aravind\ne | p\nTECHNICAL SKILLS\n"
           "Languages: one, two, three, four, five, six, seven, eight, nine, ten, "
           "eleven, twelve, thirteen, fourteen, fifteen, sixteen, seventeen\n"
           "Frameworks: alpha, beta\n"
           "PROFESSIONAL EXPERIENCE\nRole: r\nCompany: c\nDuration: d\n"
           "EDUCATION\nx\nCERTIFICATIONS\nNone")
    pdf = render_pdf(txt, tmp_path / "skills.pdf")
    text = extract_pdf_text(pdf)
    assert text.find("seventeen") < text.find("Frameworks")  # wrap stays before next category


def test_drop_last_responsibility_only_touches_responsibilities():
    from job_agent.tailor.render_pdf import drop_last_responsibility
    txt = ("Role: A\nCompany: B\nDuration: d\nResponsibilities:\n- r0\n- r1\n- r2\n"
           "Achievements:\n- a0\n- a1\nEDUCATION\nx")
    out = drop_last_responsibility(txt)
    assert out.count("- r") == 2      # one responsibility dropped (3 -> 2)
    assert out.count("- a") == 2      # achievements untouched


def test_pdf_missing_standard_section_is_rejected(tmp_path):
    incomplete = ("Jordan Rivers\ne | p\nPROFESSIONAL SUMMARY\nx\n"
                  "TECHNICAL SKILLS\nx\nPROFESSIONAL EXPERIENCE\nx\nEDUCATION\nx\n")  # no Certifications
    pdf = render_pdf(incomplete, tmp_path / "bad.pdf")
    with pytest.raises(PdfVerifyError, match="Certifications"):
        verify_pdf(pdf)
