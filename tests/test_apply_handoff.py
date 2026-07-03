"""Pause-and-resume handoff: detection is pure; resume re-checks page state."""

from __future__ import annotations

from job_agent.apply.handoff import (
    Blocker,
    BlockerKind,
    detect_blockers,
    pause_and_resume,
)
from job_agent.apply.prompt_io import ScriptedIO


def test_detects_captcha():
    blockers = detect_blockers("Please complete the reCAPTCHA to continue.")
    assert [b.kind for b in blockers] == [BlockerKind.CAPTCHA]


def test_detects_login():
    blockers = detect_blockers("Please sign in to continue your application.")
    assert BlockerKind.LOGIN in [b.kind for b in blockers]


def test_detects_account_creation():
    blockers = detect_blockers("Create an account to apply.")
    assert BlockerKind.ACCOUNT in [b.kind for b in blockers]


def test_clean_page_has_no_blockers():
    assert detect_blockers("Full name. Email. Submit application.") == ()


def test_pause_resumes_after_state_clears():
    # The page is blocked on the first check, clear on the second.
    checks = iter([False, True])
    io = ScriptedIO(answers=["", ""])  # two Enter presses
    ok = pause_and_resume(Blocker(BlockerKind.CAPTCHA, "matched 'captcha'"),
                          io.as_io(), resume_check=lambda: next(checks))
    assert ok is True
    assert any("resuming" in w.lower() for w in io.written)


def test_pause_can_be_skipped_by_the_human():
    io = ScriptedIO(answers=["skip"])
    ok = pause_and_resume(Blocker(BlockerKind.LOGIN, "matched 'sign in'"),
                          io.as_io(), resume_check=lambda: False)
    assert ok is False
    assert any("skipped" in w.lower() for w in io.written)


def test_pause_prints_instructions_and_never_asks_for_credentials():
    io = ScriptedIO(answers=["skip"])
    pause_and_resume(Blocker(BlockerKind.LOGIN, "x"), io.as_io(), resume_check=lambda: False)
    transcript = io.transcript.lower()
    assert "log in yourself" in transcript
    assert "password" not in transcript  # we never ask the user to hand us a password
