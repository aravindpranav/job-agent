"""Grounded yes/no answers: factual questions career_facts can answer, veto-first.

Safety properties pinned here:
  * an answer is planned ONLY when every content term of the question is
    explicitly present in the facts (yes) or the single-valued location fact
    contradicts it (no) — anything partial, judgment-based, or unknown pauses;
  * EEO/self-id, consent/legal, visa/sponsorship, salary, notice, and free-text
    fields are NEVER grounded, whatever their options;
  * grounded answers carry a [GROUNDED] tag + the grounding fact, so the human
    can veto them in the review panel before anything reaches the form.
"""

from __future__ import annotations

import pytest

from job_agent.apply.answer_bank import AnswerBank, Contact
from job_agent.apply.fields import FieldType, FillPlan, FormField, Unfilled
from job_agent.apply.grounded import apply_grounded_answers, ground_yes_no
from job_agent.apply.review import source_tag
from job_agent.tailor.career_facts import CareerFacts

FACTS = CareerFacts.model_validate({
    "name": "Aravind P", "role": "MLE", "email": "a@x.com", "phone": "1",
    "location": "Santa Clara, CA, USA",
    "skills_inventory": {"Deep Learning": ["PyTorch", "TensorFlow"]},
    "employers": [{
        "company": "JPMorgan Chase & Co.", "title": "GenAI Engineer",
        "duration": "Jul 2024 - Present",
        "project_description": "JPMorgan is a leading global firm.",  # excluded from grounding
        "real_bullets": [
            "Deployed ML models to production on Kubernetes (AKS, EKS) with "
            "autoscaling and drift detection.",
        ],
        "real_metrics": ["maintained 99.9% uptime for production GenAI services"],
        "real_skills": ["Python", "Docker", "Kubernetes (AKS, EKS)"],
    }],
})


def yn(label: str, *, ftype: FieldType = FieldType.RADIO,
       options: tuple[str, ...] = ("Yes", "No")) -> FormField:
    return FormField(selector=f'input[name="{label[:12]}"]', field_type=ftype,
                     label=label, name="q", required=True, options=options)


def unfilled_plan(*fields_and_reasons) -> FillPlan:
    return FillPlan(planned=(), unfilled=tuple(
        Unfilled(f, r) for f, r in fields_and_reasons))


MISSING = "no matching answer in the answer bank (pause to fill by hand)"


# --- grounded YES ------------------------------------------------------------

def test_skill_question_grounds_yes_with_the_fact_shown():
    hit = ground_yes_no(yn("Do you have production experience with PyTorch?"), FACTS)
    assert hit is not None
    value, grounding = hit
    assert value == "Yes"
    assert "PyTorch" in grounding                 # the grounding fact is named


def test_production_ml_question_grounds_yes_from_bullets():
    hit = ground_yes_no(yn("Have you taken ML models to production?"), FACTS)
    assert hit == ("Yes", hit[1])
    assert "production" in hit[1].lower()
    assert "JPMorgan" in hit[1]                   # source employer shown


def test_us_location_question_grounds_yes():
    hit = ground_yes_no(yn("Are you located in the United States?"), FACTS)
    assert hit is not None and hit[0] == "Yes"
    assert "Santa Clara" in hit[1]


# --- grounded NO (single-valued location contradiction only) -----------------

def test_foreign_location_question_grounds_no():
    hit = ground_yes_no(yn("Are you currently located in Canada?"), FACTS)
    assert hit is not None and hit[0] == "No"
    assert "Santa Clara" in hit[1]


def test_absent_skill_is_never_a_no():
    # absence in the facts is not explicit contradiction — pause, don't answer
    assert ground_yes_no(yn("Do you have experience with Rust?"), FACTS) is None


# --- ambiguous / judgment → pause -------------------------------------------

@pytest.mark.parametrize("label", [
    "Do you have experience with Rust?",                 # not in facts
    "Have you led a team of engineers?",                 # judgment/leadership
    "Are you comfortable working in ambiguity?",         # judgment
    "Are you willing to relocate?",                      # preference, not a fact
    "Do you have experience with PyTorch and Rust?",     # partial support only
])
def test_ambiguous_or_judgment_questions_pause(label):
    assert ground_yes_no(yn(label), FACTS) is None


def test_company_blurb_wording_cannot_ground_an_answer():
    # "leading global firm" lives only in the project_description, which is
    # excluded from the grounding corpus — no false yes from company blurbs.
    assert ground_yes_no(yn("Do you have experience leading a global team?"), FACTS) is None


def test_non_pure_yes_no_options_pause():
    f = yn("Do you have production experience with PyTorch?",
           options=("Yes", "No", "Prefer not to say"))
    assert ground_yes_no(f, FACTS) is None


def test_free_text_is_never_grounded():
    f = yn("Do you have production experience with PyTorch?",
           ftype=FieldType.TEXT, options=())
    assert ground_yes_no(f, FACTS) is None


# --- hard exclusions, each one ------------------------------------------------

@pytest.mark.parametrize("label", [
    "Are you a protected veteran?",                                # self-id/EEO
    "Do you identify as Hispanic or Latino?",                      # self-id/EEO
    "Do you agree to the privacy policy?",                         # consent/legal
    "Will you now or in the future require visa sponsorship?",     # visa
    "Are you authorized to work in the United States?",            # work authorization
    "Is your salary expectation flexible?",                        # salary
    "Can you start within your notice period?",                    # notice period
])
def test_hard_exclusions_always_pause(label):
    assert ground_yes_no(yn(label), FACTS) is None


# --- plan integration ---------------------------------------------------------

def test_grounded_answers_move_to_planned_with_tag_and_grounding():
    plan = unfilled_plan(
        (yn("Have you taken ML models to production?"), MISSING),
        (yn("Do you have experience with Rust?"), MISSING),
    )
    out = apply_grounded_answers(plan, FACTS)
    assert len(out.planned) == 1 and len(out.unfilled) == 1
    p = out.planned[0]
    assert p.value == "Yes"
    assert p.source.startswith("grounded-fact:")
    assert source_tag(p.source) == "[GROUNDED]"
    assert out.unfilled[0].field.label == "Do you have experience with Rust?"
    # immutable: the original plan is untouched
    assert plan.planned == () and len(plan.unfilled) == 2


def test_consent_and_credential_pauses_are_never_reconsidered():
    consent = yn("Do you agree to the terms?")
    plan = unfilled_plan(
        (consent, "legal consent/acknowledgment — never auto-filled"),
        (yn("Anything"), "credential field — never auto-filled"),
    )
    out = apply_grounded_answers(plan, FACTS)
    assert out.planned == () and len(out.unfilled) == 2


def test_grounded_value_uses_the_options_own_casing():
    f = yn("Have you taken ML models to production?", options=("YES", "NO"))
    plan = unfilled_plan((f, MISSING))
    out = apply_grounded_answers(plan, FACTS)
    assert out.planned[0].value == "YES"        # what the form shows, verbatim


# --- end to end through the extension endpoint --------------------------------

FACTS_YAML = """
name: Aravind P
role: MLE
email: a@x.com
phone: '1'
location: Santa Clara, CA, USA
skills_inventory:
  Deep Learning: [PyTorch]
employers:
  - company: JPMorgan Chase & Co.
    title: GenAI Engineer
    duration: Jul 2024 - Present
    real_bullets:
      - Deployed ML models to production on Kubernetes with drift detection.
"""


def _radio(group_label, option, name, selector):
    return {"tag": "input", "type": "radio", "name": name, "label": option,
            "groupLabel": group_label, "required": True, "maxlength": None,
            "options": [], "selector": selector}


def test_endpoint_grounds_for_review_and_still_pauses_eeo(tmp_path):
    from fastapi.testclient import TestClient

    from job_agent.dashboard.app import create_app

    (tmp_path / "career_facts.yaml").write_text(FACTS_YAML)
    (tmp_path / "answer_bank.yaml").write_text(
        "authorized_us: true\nrequires_sponsorship: false\n")
    client = TestClient(create_app(data_dir=tmp_path))
    fields = [
        _radio("Have you taken ML models to production?", "Yes", "prod", "#p-y"),
        _radio("Have you taken ML models to production?", "No", "prod", "#p-n"),
        _radio("Are you a protected veteran?", "Yes", "vet", "#v-y"),
        _radio("Are you a protected veteran?", "No", "vet", "#v-n"),
    ]
    data = client.post("/api/extension/fill-values", json={"fields": fields}).json()
    planned = {p["label"]: p for p in data["planned"]}
    entry = planned["Have you taken ML models to production?"]
    assert entry["tag"] == "[GROUNDED]"
    assert entry["value"] == "Yes"
    assert "production" in entry["source"]       # the grounding fact travels to review
    # EEO yes/no is untouched: still in unfilled, never grounded
    assert not any(p["label"].startswith("Are you a protected veteran")
                   for p in data["planned"])
