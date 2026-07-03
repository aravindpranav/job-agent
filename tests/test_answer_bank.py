"""Answer-bank validation: required work-auth, decline-to-state, contact merge."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.apply.answer_bank import (
    DECLINE_TO_STATE,
    AnswerBank,
    EeoAnswers,
    load_answer_bank,
    resolve_contact,
)
from job_agent.tailor.career_facts import load_career_facts
import job_agent.tailor as tailor_pkg

EXAMPLE = Path(__file__).resolve().parents[1] / "data" / "answer_bank.example.yaml"
DEMO_FACTS = load_career_facts(
    Path(tailor_pkg.__file__).parent / "demo" / "demo_career_facts.yaml"
)

_MINIMAL = {"authorized_us": True, "requires_sponsorship": False}


def test_minimal_bank_needs_only_work_authorization():
    bank = AnswerBank.model_validate(_MINIMAL)
    assert bank.authorized_us is True
    assert bank.requires_sponsorship is False
    # everything else is optional and empty/None by default
    assert bank.salary_expectation == ""
    assert bank.work_mode is None
    assert bank.total_years_experience is None
    assert bank.eeo is None
    assert bank.prepared_answers == {}


@pytest.mark.parametrize("missing", ["authorized_us", "requires_sponsorship"])
def test_missing_work_authorization_field_is_rejected(missing):
    payload = {k: v for k, v in _MINIMAL.items() if k != missing}
    with pytest.raises(Exception) as exc:  # pydantic ValidationError
        AnswerBank.model_validate(payload)
    assert missing in str(exc.value)


def test_work_authorization_must_be_explicit_not_guessed():
    # No defaults on either field — they cannot be silently assumed.
    assert AnswerBank.model_fields["authorized_us"].is_required()
    assert AnswerBank.model_fields["requires_sponsorship"].is_required()


def test_eeo_defaults_to_decline_to_state():
    bank = AnswerBank.model_validate({**_MINIMAL, "eeo": {}})
    assert isinstance(bank.eeo, EeoAnswers)
    assert bank.eeo.gender == DECLINE_TO_STATE
    assert bank.eeo.race == DECLINE_TO_STATE
    assert bank.eeo.veteran_status == DECLINE_TO_STATE
    assert bank.eeo.disability_status == DECLINE_TO_STATE


def test_eeo_partial_fill_keeps_declines_for_the_rest():
    bank = AnswerBank.model_validate({**_MINIMAL, "eeo": {"veteran_status": "Veteran"}})
    assert bank.eeo.veteran_status == "Veteran"
    assert bank.eeo.gender == DECLINE_TO_STATE  # untouched fields still decline


def test_invalid_work_mode_is_rejected():
    with pytest.raises(Exception):
        AnswerBank.model_validate({**_MINIMAL, "work_mode": "hybrid-ish"})


def test_negative_experience_is_rejected():
    with pytest.raises(Exception):
        AnswerBank.model_validate({**_MINIMAL, "total_years_experience": -1})


def test_unknown_field_is_rejected():
    # extra="forbid" — a typo'd key fails loudly rather than being silently dropped.
    with pytest.raises(Exception):
        AnswerBank.model_validate({**_MINIMAL, "salery_expectation": "$100k"})


def test_bank_is_immutable():
    bank = AnswerBank.model_validate(_MINIMAL)
    with pytest.raises(Exception):
        bank.authorized_us = False  # frozen


def test_prepared_answers_round_trip():
    bank = AnswerBank.model_validate(
        {**_MINIMAL, "prepared_answers": {"why this company": "Because X."}}
    )
    assert bank.prepared_answers["why this company"] == "Because X."


# --- contact merge ----------------------------------------------------------


def test_resolve_contact_takes_identity_from_career_facts():
    bank = AnswerBank.model_validate(_MINIMAL)
    contact = resolve_contact(DEMO_FACTS, bank)
    assert contact.name == DEMO_FACTS.name
    assert contact.email == DEMO_FACTS.email
    assert contact.phone == DEMO_FACTS.phone


def test_resolve_contact_prefers_bank_links():
    bank = AnswerBank.model_validate(
        {**_MINIMAL, "linkedin": "https://linkedin.com/in/aravind"}
    )
    contact = resolve_contact(DEMO_FACTS, bank)
    assert contact.linkedin == "https://linkedin.com/in/aravind"


def test_resolve_contact_falls_back_to_facts_links():
    from job_agent.tailor.career_facts import CareerFacts

    facts = CareerFacts.model_validate(
        {
            "name": "A",
            "role": "Engineer",
            "email": "a@example.com",
            "phone": "1",
            "links": ["https://github.com/a", "https://linkedin.com/in/a"],
            "employers": [
                {"company": "C", "title": "T", "duration": "2020 - Present"}
            ],
        }
    )
    contact = resolve_contact(facts, AnswerBank.model_validate(_MINIMAL))
    assert contact.github == "https://github.com/a"
    assert contact.linkedin == "https://linkedin.com/in/a"


# --- the committed example template ------------------------------------------


def test_example_template_loads_and_validates():
    bank = load_answer_bank(EXAMPLE)
    assert bank.authorized_us is True
    assert bank.requires_sponsorship is False
    assert bank.work_mode == "remote"
    assert bank.eeo is not None
    assert "why this company" in bank.prepared_answers


def test_missing_file_raises_with_guidance():
    with pytest.raises(FileNotFoundError, match="answer_bank.example.yaml"):
        load_answer_bank("data/does_not_exist.yaml")


def test_unparseable_yaml_is_rejected(tmp_path):
    bad = tmp_path / "bank.yaml"
    bad.write_text("authorized_us: true\n  bad: : indent")
    with pytest.raises(ValueError, match="YAML"):
        load_answer_bank(bad)
