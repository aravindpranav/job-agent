"""Screening answers: routing, no-fabrication gate, drafting, review UX."""

from __future__ import annotations

from pathlib import Path

import job_agent.tailor as tailor_pkg
from job_agent.apply.answer_bank import AnswerBank, Contact
from job_agent.apply.fields import FieldType, FormField, Unfilled
from job_agent.apply.filler import build_fill_plan
from job_agent.apply.prompt_io import ScriptedIO
from job_agent.apply.review import (
    Decision,
    has_ai_drafts,
    render_review,
    request_approval,
    source_tag,
)
from job_agent.apply.screening import (
    NEEDS_INPUT_MARKER,
    apply_drafts,
    build_draft_prompt,
    classify_question,
    make_drafter,
    store_approved_answers,
    trim_to_limit,
    verify_answer,
)
from job_agent.tailor.career_facts import load_career_facts

FACTS = load_career_facts(Path(tailor_pkg.__file__).parent / "demo" / "demo_career_facts.yaml")
BANK = AnswerBank.model_validate({
    "authorized_us": True, "requires_sponsorship": False,
    "salary_expectation": "$160k", "work_mode": "remote",
})
CONTACT = Contact(name="Jordan Rivers", email="j@example.com", phone="1",
                  employer="Acme Analytics", title="Data Engineer")


def _f(sel, ftype, label, required=False, options=(), max_length=None):
    return FormField(selector=sel, field_type=ftype, label=label,
                     required=required, options=tuple(options), max_length=max_length)


# --- routing ------------------------------------------------------------------

def test_router_factual():
    f = _f("#y", FieldType.TEXT, "How many years of Python experience do you have?")
    assert classify_question(f) == "factual"


def test_router_free_text():
    assert classify_question(_f("#w", FieldType.TEXTAREA, "Why Plaid?")) == "free_text"
    assert classify_question(
        _f("#d", FieldType.TEXT, "Describe a project you are proud of")) == "free_text"


def test_router_consent_wins_over_everything():
    # Consent even though it's a textarea with a "why"-style label.
    f = _f("#c", FieldType.TEXTAREA, "Why do you consent to SMS updates?")
    assert classify_question(f) == "consent"
    assert classify_question(_f("#e", FieldType.EEO, "Gender")) == "consent"
    assert classify_question(
        _f("#sms", FieldType.CHECKBOX, "I agree to receive SMS messages")) == "consent"


# --- no-fabrication gate --------------------------------------------------------

def test_gate_catches_invented_employer():
    bad = "When I worked at Shadow Corp, I led the data team."
    assert any("Shadow Corp" in v for v in verify_answer(bad, FACTS))


def test_gate_allows_real_employer():
    ok = "While at Acme Analytics, I built streaming pipelines."
    assert verify_answer(ok, FACTS) == []


def test_gate_catches_unbanked_metric():
    bad = "I reduced infrastructure costs by 73% at Acme Analytics."
    assert any("73%" in v for v in verify_answer(bad, FACTS))


def test_gate_allows_banked_metric():
    ok = "I reduced pipeline runtime by 30% and processed 5,000,000 records/day."
    assert verify_answer(ok, FACTS) == []


def test_gate_catches_target_company_product_claim():
    bad = "I've used Plaid's API extensively in production."
    assert any("Plaid" in v for v in verify_answer(bad, FACTS, company="Plaid"))


def test_gate_allows_aspirational_company_mention():
    ok = "I'm excited to work at Plaid on data infrastructure."
    assert verify_answer(ok, FACTS, company="Plaid") == []


# --- drafter: regenerate once, then flag -----------------------------------------

BAD = "When I worked at Shadow Corp I cut costs 73%."
GOOD = "I build reliable pipelines; at Acme Analytics I reduced runtime by 30%."


def _drafter(responses, cache_path=None):
    answers = list(responses)
    calls = []

    def generate(prompt):
        calls.append(prompt)
        return answers.pop(0)
    return make_drafter(generate, FACTS, BANK, jd="We need a data engineer.",
                        company="Plaid", cache_path=cache_path), calls


def test_violating_draft_is_regenerated_then_clean():
    drafter, calls = _drafter([BAD, GOOD])
    fill = drafter(_f("#q", FieldType.TEXTAREA, "Why Plaid?"))
    assert fill.value == GOOD
    assert fill.source.startswith("ai-draft")
    assert "GATE-FLAGGED" not in fill.source
    assert len(calls) == 2 and "REGENERATE" in calls[1]


def test_still_violating_draft_is_gate_flagged_not_silent():
    drafter, _ = _drafter([BAD, BAD])
    fill = drafter(_f("#q", FieldType.TEXTAREA, "Why Plaid?"))
    assert "GATE-FLAGGED" in fill.source
    assert source_tag(fill.source) == "[GATE-FLAGGED]"


def test_needs_input_marker_is_tagged():
    text = f"I focus on data reliability.\n{NEEDS_INPUT_MARKER} your personal motivation."
    drafter, _ = _drafter([text])
    fill = drafter(_f("#q", FieldType.TEXTAREA, "Why this role?"))
    assert source_tag(fill.source) == "[NEEDS-INPUT]"


def test_draft_respects_char_limit():
    drafter, calls = _drafter([GOOD * 20])
    fill = drafter(_f("#q", FieldType.TEXTAREA, "Why?", max_length=100))
    assert len(fill.value) <= 100
    assert "100 characters" in calls[0]           # limit stated in the prompt
    assert trim_to_limit("abc def", 5) == "abc"   # word-boundary cut


def test_llm_failure_leaves_question_unfilled():
    def boom(prompt):
        raise RuntimeError("api down")
    drafter = make_drafter(boom, FACTS, BANK, jd="", company="X")
    assert drafter(_f("#q", FieldType.TEXTAREA, "Why?")) is None


def test_prompt_grounds_in_facts_and_banked_metrics():
    prompt = build_draft_prompt(_f("#q", FieldType.TEXTAREA, "Why us?"),
                                FACTS, BANK, jd="JD text", company="Plaid")
    assert "Acme Analytics" in prompt
    assert "ONLY numbers you may cite" in prompt
    assert "JD text" in prompt


# --- routing + drafting over a plan ----------------------------------------------

def test_consent_and_factual_never_reach_the_drafter():
    called = []

    def spy_drafter(field):
        called.append(field.selector)
        return None
    plan = build_fill_plan((
        _f("#sms", FieldType.CHECKBOX, "I agree to receive SMS messages"),   # consent
        _f("#years", FieldType.TEXT, "Years of Rust experience"),            # factual, unknown
        _f("#why", FieldType.TEXTAREA, "Why Plaid?"),                        # free text
    ), BANK, CONTACT, None)
    apply_drafts(plan, spy_drafter)
    assert called == ["#why"]        # drafter saw ONLY the free-text question


# --- integration: one factual + one why-company + one SMS consent ------------------

def _integration_plan():
    fields = (
        _f("#salary", FieldType.TEXT, "Salary expectation"),                # factual, in bank
        _f("#why", FieldType.TEXTAREA, "Why do you want to work at Plaid?"),
        _f("#sms", FieldType.CHECKBOX, "I agree to receive SMS updates", required=True),
    )
    plan = build_fill_plan(fields, BANK, CONTACT, None)
    drafter, _ = _drafter([GOOD])
    return apply_drafts(plan, drafter)


def test_integration_tags_from_answer_bank_ai_draft_and_paused_consent():
    text = render_review(_integration_plan())
    assert "[FROM ANSWER_BANK]" in text and "$160k" in text
    assert "[AI-DRAFT]" in text and GOOD in text
    assert "[PAUSED: consent]" in text
    assert "AI-DRAFTED answers above" in text     # the read-before-approving banner


def test_integration_cannot_auto_approve_with_ai_draft():
    # No missing-required here, so the AI-draft guard itself must block.
    fields = (
        _f("#salary", FieldType.TEXT, "Salary expectation"),
        _f("#why", FieldType.TEXTAREA, "Why do you want to work at Plaid?"),
    )
    drafter, _ = _drafter([GOOD])
    plan = apply_drafts(build_fill_plan(fields, BANK, CONTACT, None), drafter)
    assert has_ai_drafts(plan)
    io = ScriptedIO(answers=[])
    outcome = request_approval(plan, io.as_io(), auto_approve=True)
    assert outcome.decision == Decision.SKIP
    assert any("require human review" in w for w in io.written)


def test_integration_human_can_edit_then_approve():
    plan = _integration_plan()
    io = ScriptedIO(answers=["edit #sms=Yes", "edit #why=My own words.", "approve"])
    outcome = request_approval(plan, io.as_io())
    assert outcome.decision == Decision.APPROVE
    # Edited value preserved EXACTLY as typed (the gate must not lowercase it).
    assert any(p.field.selector == "#why" and p.value == "My own words."
               for p in outcome.plan.planned)


# --- approved-answer cache ---------------------------------------------------------

def test_cache_round_trip_re_reviews_as_ai_draft(tmp_path):
    cache = tmp_path / "answers_cache.json"
    plan = _integration_plan()
    store_approved_answers(cache, "Plaid", plan)          # simulate post-approval save
    drafter, calls = _drafter([], cache_path=cache)       # NO responses: must not call LLM
    fill = drafter(_f("#why2", FieldType.TEXTAREA, "Why do you want to work at Plaid?"))
    assert fill.value == GOOD                             # pre-populated from cache
    assert calls == []                                    # no LLM call
    assert source_tag(fill.source) == "[AI-DRAFT]"        # still reviewed, never auto
