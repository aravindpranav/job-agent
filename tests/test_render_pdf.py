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


def test_skill_wrap_preserves_within_category_extraction_order(tmp_path):
    # Skills render as a two-column table, so the two columns interleave in raw
    # y-ordered extraction (accepted trade-off of the reference layout). What
    # must still hold: nothing is lost, and WITHIN each category the values
    # extract in their written order — the hanging indent may not scramble a
    # wrapped cell.
    txt = ("Aravind\ne | p\nTECHNICAL SKILLS\n"
           "Languages: one, two, three, four, five, six, seven, eight, nine, ten, "
           "eleven, twelve, thirteen, fourteen, fifteen, sixteen, seventeen\n"
           "Frameworks: alpha, beta\n"
           "PROFESSIONAL EXPERIENCE\nRole: r\nCompany: c\nDuration: d\n"
           "EDUCATION\nx\nCERTIFICATIONS\nNone")
    pdf = render_pdf(txt, tmp_path / "skills.pdf")
    text = extract_pdf_text(pdf)
    for token in ("one", "seventeen", "alpha", "beta"):
        assert token in text                                  # nothing lost
    assert text.find("one") < text.find("nine") < text.find("seventeen")
    assert text.find("alpha") < text.find("beta")


def test_drop_last_responsibility_only_touches_responsibilities():
    from job_agent.tailor.render_pdf import drop_last_responsibility
    txt = ("Role: A\nCompany: B\nDuration: d\nResponsibilities:\n- r0\n- r1\n- r2\n- r3\n"
           "Achievements:\n- a0\n- a1\nEDUCATION\nx")
    out = drop_last_responsibility(txt)
    assert out.count("- r") == 3      # one responsibility dropped (4 -> 3)
    assert out.count("- a") == 2      # achievements untouched


def test_page_trim_floor_matches_the_older_role_depth_floor():
    # The 3-page fit loop may never trim a role back to the old 2-bullet
    # density: the floor is 3, matching the prompt's older-role minimum.
    from job_agent.tailor.render_pdf import _RESP_FLOOR, drop_last_responsibility
    assert _RESP_FLOOR == 3
    at_floor = ("Role: A\nCompany: B\nDuration: d\nResponsibilities:\n- r0\n- r1\n- r2\n"
                "Achievements:\n- a0\nEDUCATION\nx")
    assert drop_last_responsibility(at_floor) == at_floor   # at the floor: untouchable


def test_page_budget_is_three_pages():
    from job_agent.cli import MAX_RESUME_PAGES
    assert MAX_RESUME_PAGES == 3


def test_pdf_missing_standard_section_is_rejected(tmp_path):
    incomplete = ("Jordan Rivers\ne | p\nPROFESSIONAL SUMMARY\nx\n"
                  "TECHNICAL SKILLS\nx\nPROFESSIONAL EXPERIENCE\nx\nEDUCATION\nx\n")  # no Certifications
    pdf = render_pdf(incomplete, tmp_path / "bad.pdf")
    with pytest.raises(PdfVerifyError, match="Certifications"):
        verify_pdf(pdf)
