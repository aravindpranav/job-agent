"""Tailoring: strict-format compliance, NOTES parsing, and LLM injection."""

from __future__ import annotations

from pathlib import Path

import job_agent.tailor as tailor_pkg
from job_agent.tailor.career_facts import load_career_facts
from job_agent.tailor.render_pdf import CANONICAL_HEADINGS
from job_agent.tailor.tailor import build_facts_block, split_notes, tailor_resume

DEMO = Path(tailor_pkg.__file__).parent / "demo"
FACTS = load_career_facts(DEMO / "demo_career_facts.yaml")
JD = (DEMO / "demo_jd.txt").read_text()
STUB = (DEMO / "demo_response.txt").read_text()


def test_split_notes_separates_resume_and_notes():
    resume, notes = split_notes("Professional Summary\nbody\n\nNOTES\n- a gap")
    assert "NOTES" in notes and "a gap" in notes
    assert "NOTES" not in resume and "body" in resume


def test_stub_output_has_all_standard_headings_in_order():
    result = tailor_resume(FACTS, JD, stub_response=STUB, megaprompt="(x)")
    low = result.resume_text.lower()  # headings are ALL CAPS on the face
    positions = [low.find(h.lower()) for h in CANONICAL_HEADINGS]
    assert all(p >= 0 for p in positions), "a standard heading is missing"
    assert positions == sorted(positions), "headings out of order"
    assert result.notes.lower().startswith("notes")
    assert "[metric" not in low and "[" not in result.resume_text  # no placeholders on the face


def test_facts_block_carries_immutable_constraints():
    block = build_facts_block(FACTS)
    assert "IMMUTABLE" in block
    assert "Acme Analytics" in block and "Jan 2022 - Present" in block
    # per-role real metrics are labeled so the model can't invent numbers
    assert "Real metrics" in block


def test_generate_is_injected_with_the_facts():
    captured = {}

    def fake_generate(system, user, settings):
        captured["system"] = system
        captured["user"] = user
        return ("Jordan Rivers\nData Engineer | e | p\nProfessional Summary\nx\n"
                "Technical Skills\nx\nProfessional Experience\nx\nEducation\nx\n"
                "Certifications\n(none)\nNOTES\n- ok")

    result = tailor_resume(FACTS, JD, generate=fake_generate, megaprompt="MEGA", settings=None)
    assert "MEGA" in captured["system"]               # base prompt included
    assert "OVERRIDES" in captured["system"]          # policy addendum appended
    assert "Acme Analytics" in captured["user"]       # facts reached the model
    assert "TARGET JOB DESCRIPTION" in captured["user"]
    assert result.notes.lower().startswith("notes")
