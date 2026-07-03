"""The review gate: renders values, blocks approval on missing required fields."""

from __future__ import annotations

import pytest

from job_agent.apply.fields import FieldType, FillPlan, FormField, PlannedFill, Unfilled
from job_agent.apply.prompt_io import ScriptedIO
from job_agent.apply.review import Decision, render_review, request_approval


def _field(sel, label, required=False):
    return FormField(selector=sel, field_type=FieldType.TEXT, label=label, required=required)


COMPLETE = FillPlan(
    planned=(
        PlannedFill(_field("#email", "Email", required=True), "a@b.com", "career_facts.email"),
        PlannedFill(_field("#salary", "Salary"), "$160k", "answer_bank.salary_expectation"),
    ),
)
MISSING_REQUIRED = FillPlan(
    planned=(PlannedFill(_field("#email", "Email", required=True), "a@b.com", "career_facts.email"),),
    unfilled=(Unfilled(_field("#resume", "Resume", required=True), "no resume provided"),),
)


def test_render_shows_every_value_and_its_source():
    text = render_review(COMPLETE)
    assert "Email" in text and "a@b.com" in text
    assert "career_facts.email" in text
    assert "$160k" in text


def test_render_flags_missing_required():
    text = render_review(MISSING_REQUIRED)
    assert "REQUIRED" in text
    assert "cannot approve" in text.lower()


def test_approve_when_complete_returns_approve():
    io = ScriptedIO(answers=["approve"])
    outcome = request_approval(COMPLETE, io.as_io())
    assert outcome.decision == Decision.APPROVE


def test_skip_returns_skip():
    io = ScriptedIO(answers=["skip"])
    outcome = request_approval(COMPLETE, io.as_io())
    assert outcome.decision == Decision.SKIP


def test_approve_is_blocked_when_required_field_missing():
    # First 'approve' must be refused; then the human skips.
    io = ScriptedIO(answers=["approve", "skip"])
    outcome = request_approval(MISSING_REQUIRED, io.as_io())
    assert outcome.decision == Decision.SKIP
    assert any("cannot approve" in w.lower() for w in io.written)


def test_edit_fills_a_missing_required_field_then_approves():
    io = ScriptedIO(answers=["edit #resume=/tmp/r.pdf", "approve"])
    outcome = request_approval(MISSING_REQUIRED, io.as_io())
    assert outcome.decision == Decision.APPROVE
    assert any(p.field.selector == "#resume" and p.value == "/tmp/r.pdf"
               for p in outcome.plan.planned)
    assert outcome.plan.missing_required() == ()


def test_edit_on_unknown_selector_is_reported_and_loops():
    io = ScriptedIO(answers=["edit #nope=x", "skip"])
    outcome = request_approval(COMPLETE, io.as_io())
    assert outcome.decision == Decision.SKIP
    assert any("#nope" in w for w in io.written)


def test_auto_approve_demo_path():
    io = ScriptedIO(answers=[])
    outcome = request_approval(COMPLETE, io.as_io(), auto_approve=True)
    assert outcome.decision == Decision.APPROVE
    assert any("simulated approval" in w for w in io.written)


def test_auto_approve_refuses_when_required_missing():
    io = ScriptedIO(answers=[])
    outcome = request_approval(MISSING_REQUIRED, io.as_io(), auto_approve=True)
    assert outcome.decision == Decision.SKIP


def test_running_out_of_input_is_an_explicit_error():
    io = ScriptedIO(answers=["huh?"])  # invalid, then no more answers
    with pytest.raises(EOFError):
        request_approval(COMPLETE, io.as_io())
