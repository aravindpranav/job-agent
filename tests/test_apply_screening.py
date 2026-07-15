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
    classify_free_text,
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


def test_free_text_subtype_routing():
    assert classify_free_text(
        _f("#h", FieldType.TEXT, "How did you hear about this job?")) == "short_factual"
    assert classify_free_text(
        _f("#n", FieldType.TEXTAREA, "What is your notice period?")) == "short_factual"
    assert classify_free_text(
        _f("#w", FieldType.TEXTAREA, "Why do you want to work at Plaid?")) == "motivation"
    assert classify_free_text(
        _f("#i", FieldType.TEXTAREA, "What interests you about this role?")) == "motivation"
    assert classify_free_text(
        _f("#e", FieldType.TEXTAREA, "Describe a time you led a project")) == "experience"
    assert classify_free_text(
        _f("#p", FieldType.TEXTAREA, "Tell us about a project you are proud of")) == "experience"
    assert classify_free_text(
        _f("#g", FieldType.TEXTAREA, "Anything else we should know?")) == "general"


# --- question-type-appropriate answers -------------------------------------------

def test_how_did_you_hear_gets_a_short_plain_answer_no_llm():
    # Deterministic from the bank (default "LinkedIn") — never an essay, never
    # praise, never a specific invented referrer, and never an LLM call.
    plan = build_fill_plan(
        (_f("#hear", FieldType.TEXT, "How did you hear about this job?"),),
        BANK, CONTACT, None)
    (fill,) = plan.planned
    assert fill.value == "LinkedIn"
    assert fill.source == "answer_bank.how_heard"
    assert len(fill.value.split()) < 15
    for praise in ("excited", "passionate", "thrilled", "mission", "love", "admire"):
        assert praise not in fill.value.lower()


def test_how_did_you_hear_variants_map_and_never_reach_the_drafter():
    called = []

    def spy(field):
        called.append(field.selector)
        return None
    plan = build_fill_plan((
        _f("#h1", FieldType.TEXT, "How did you hear about us?"),
        _f("#h2", FieldType.TEXTAREA, "Where did you hear about this position?"),
    ), BANK, CONTACT, None)
    apply_drafts(plan, spy)
    assert {p.value for p in plan.planned} == {"LinkedIn"}
    assert called == []                       # deterministic — the LLM never sees it


def test_why_company_is_always_flagged_needs_input():
    # Genuine per-company motivation isn't in the data: even a clean grounded
    # draft (no marker from the model) must surface as NEEDS-INPUT.
    drafter, calls = _drafter([GOOD])
    fill = drafter(_f("#why", FieldType.TEXTAREA, "Why do you want to work at Plaid?"))
    assert source_tag(fill.source) == "[NEEDS-INPUT]"
    assert NEEDS_INPUT_MARKER in fill.value   # the flag line is added for the human
    assert "MOTIVATION" in calls[0]           # type-specific instruction in the prompt


def test_experience_question_is_star_prompted_and_stays_grounded():
    drafter, calls = _drafter([GOOD])
    fill = drafter(_f("#exp", FieldType.TEXTAREA,
                      "Describe a time you improved a data pipeline"))
    assert "STAR" in calls[0]                 # type-specific instruction
    assert source_tag(fill.source) == "[AI-DRAFT]"     # clean grounded draft
    # the no-fabrication gate is unchanged for experience answers:
    drafter2, _ = _drafter([BAD, BAD])
    fill2 = drafter2(_f("#exp2", FieldType.TEXTAREA, "Describe a time you cut costs"))
    assert "GATE-FLAGGED" in fill2.source


def test_short_factual_question_is_prompted_for_one_plain_answer():
    drafter, calls = _drafter(["2 weeks"])
    fill = drafter(_f("#np", FieldType.TEXTAREA, "What is your notice period?"))
    assert "ONE short factual" in calls[0]
    assert "flattery" in calls[0] or "enthusiasm" in calls[0]
    assert fill.value == "2 weeks"


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
    # aspirational wording about the TARGET company is not a fabrication —
    # ("excited to" itself is now style-banned, so the fixture avoids it)
    ok = "I want to work at Plaid on data infrastructure."
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
    assert fill.value.startswith(GOOD)     # motivation appends its NEEDS-INPUT line
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


def test_transient_llm_failure_is_retried_once():
    # one flaky API call must not silently kill the draft (live bug: one of
    # two "please describe" questions drafted, the other paused with the
    # unrelated answer-bank message)
    attempts = []

    def flaky(prompt):
        attempts.append(prompt)
        if len(attempts) == 1:
            raise RuntimeError("overloaded")
        return GOOD
    drafter = make_drafter(flaky, FACTS, BANK, jd="", company="X")
    fill = drafter(_f("#q", FieldType.TEXTAREA,
                      "Please describe the primary focus of your ML experience"))
    assert fill is not None and GOOD in fill.value
    assert len(attempts) == 2


def test_failed_draft_reason_is_honest_not_the_bank_message():
    from job_agent.apply.fields import FillPlan, Unfilled
    from job_agent.apply.screening import apply_drafts

    def boom(prompt):
        raise RuntimeError("api down")
    drafter = make_drafter(boom, FACTS, BANK, jd="", company="X")
    plan = FillPlan(unfilled=(Unfilled(
        _f("#q", FieldType.TEXTAREA, "Please describe your ML experience"),
        "free-text question — no prepared answer (pause to answer)"),))
    out = apply_drafts(plan, drafter)
    (u,) = out.unfilled
    assert "draft" in u.reason.lower()          # says drafting was attempted
    assert "answer bank" not in u.reason.lower()  # not the misleading message


# --- absent-experience honesty (the live rec-sys incident) ---------------------------
# LLM output is nondeterministic, so these assert on the PROMPT construction
# and on the deterministic gate, not on generated text.

RECSYS_Q = _f("#q", FieldType.TEXTAREA,
              "Please describe your experience with Recommendation Systems.")


def test_experience_prompt_gates_on_topical_match_before_any_project():
    prompt = build_draft_prompt(RECSYS_Q, FACTS, BANK, jd="", company="X",
                                qtype="experience")
    low = prompt.lower()
    assert "actually asked about" in low          # the topical decision comes first
    assert "adjacent" in low                      # absent -> nearest work labelled as such
    assert "first sentence" in low                # the absence goes up front
    for framing in ("essentially the same as", "closely related to",
                    "which is a form of"):
        assert framing in low                     # forbidden framings named explicitly
    assert "relevance is not a substitute" in low
    # 'most relevant' only after the match is established
    assert "only once that topical match is established" in low


def test_system_prompt_carries_the_voice_rules():
    from job_agent.apply.screening import DRAFT_SYSTEM
    low = DRAFT_SYSTEM.lower()
    assert "em-dash" in low
    assert "cover letter" in low                  # "engineer at 11pm, not a cover letter"
    assert "leverage" in low and "spearheaded" in low
    assert "restate" in low


def test_honest_absent_experience_draft_passes_gate_and_tags_needs_input():
    honest = ("I have not built recommendation systems in production. "
              "The closest work is churn modeling at Acme Analytics, which is "
              "adjacent but a different problem.\n"
              f"{NEEDS_INPUT_MARKER} add any real recommendation-systems work "
              "that's missing from your career facts, or keep the honest no.")
    assert verify_answer(honest, FACTS) == []     # honesty is style-clean
    drafter, _ = _drafter([honest])
    fill = drafter(RECSYS_Q)
    assert source_tag(fill.source) == "[NEEDS-INPUT]"   # surfaces distinctly at review
    assert fill.value.startswith("I have not built recommendation systems")


# --- mechanical style gate (deterministic; flag-for-review, never a rewrite) ---------

def test_style_gate_flags_em_and_en_dashes():
    assert any("dash" in v for v in
               verify_answer("I built pipelines — they were fast.", FACTS))
    assert any("dash" in v for v in
               verify_answer("I built pipelines – they were fast.", FACTS))


def test_style_gate_flags_banned_phrases_whole_word():
    for text in ("I leveraged Spark daily.",
                 "I am passionate about data.",
                 "I built a robust pipeline.",
                 "I spearheaded the migration.",
                 "I thrive in ambiguity.",
                 "Not only did I ship it, but I also scaled it."):
        assert any(v.startswith("style:") for v in verify_answer(text, FACTS)), text


def test_style_gate_passes_plain_engineer_prose():
    assert verify_answer(GOOD, FACTS) == []
    assert verify_answer("No. My work has been batch pipelines, not streaming.",
                         FACTS) == []


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
    # A "why <company>" draft is a motivation answer -> always [NEEDS-INPUT].
    assert "[NEEDS-INPUT]" in text and GOOD in text
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
    assert fill.value.startswith(GOOD)                    # pre-populated from cache
    assert calls == []                                    # no LLM call
    assert source_tag(fill.source) == "[AI-DRAFT]"        # still reviewed, never auto
