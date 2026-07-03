"""Pause-and-resume handoff for logins, account creation, captchas.

The agent NEVER logs in, creates an account, or solves a captcha. When it
detects one of those, it stops, tells the human exactly what to do in the SAME
browser window, waits for them to signal continue, then re-checks the page is
clear before resuming — it does not reload or lose page state.

Detection (``detect_blockers``) is a pure function over the page's visible text,
so it is fully unit-testable without a browser.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from job_agent.apply.prompt_io import PromptIO


class BlockerKind(str, Enum):
    LOGIN = "login"
    ACCOUNT = "account creation"
    CAPTCHA = "captcha"


@dataclass(frozen=True)
class Blocker:
    kind: BlockerKind
    detail: str

    @property
    def instructions(self) -> str:
        return {
            BlockerKind.LOGIN:
                "This form requires you to log in. Please log in yourself in the "
                "browser window (the agent never types credentials), then continue.",
            BlockerKind.ACCOUNT:
                "This form wants you to create an account. Please create it yourself "
                "in the browser window (the agent never creates accounts), then continue.",
            BlockerKind.CAPTCHA:
                "This form has a captcha. Please solve it yourself in the browser "
                "window (the agent never solves captchas), then continue.",
        }[self.kind]


# Marker phrases, most specific first. Ordered so a captcha isn't misread as a login.
_MARKERS: tuple[tuple[BlockerKind, tuple[str, ...]], ...] = (
    (BlockerKind.CAPTCHA, ("captcha", "recaptcha", "hcaptcha", "i'm not a robot", "not a robot")),
    (BlockerKind.ACCOUNT, ("create an account", "create account", "sign up", "register to apply")),
    (BlockerKind.LOGIN, ("sign in to continue", "log in to continue", "please sign in",
                         "please log in", "enter your password")),
)


def detect_blockers(page_text: str) -> tuple[Blocker, ...]:
    """Pure: scan visible text for login / account / captcha markers."""
    text = page_text.lower()
    found: list[Blocker] = []
    seen: set[BlockerKind] = set()
    for kind, phrases in _MARKERS:
        for phrase in phrases:
            if phrase in text and kind not in seen:
                found.append(Blocker(kind=kind, detail=f"matched {phrase!r} on the page"))
                seen.add(kind)
                break
    return tuple(found)


def pause_and_resume(blocker: Blocker, io: PromptIO,
                     resume_check: Callable[[], bool], *, max_rounds: int = 20) -> bool:
    """Pause for the human to clear ``blocker``; resume only once it's gone.

    Returns True when the page is clear and we may continue, False if the human
    chose to abort. Re-checks ``resume_check`` after each continue signal so we
    never resume into a page that's still blocked — without reloading it.
    """
    io.write("")
    io.write(f"⏸  PAUSED — {blocker.kind.value.upper()} detected ({blocker.detail}).")
    io.write(f"   {blocker.instructions}")
    for _ in range(max_rounds):
        answer = io.read("   Complete it in the browser, then press Enter (or type 'skip'): ").strip().lower()
        if answer in ("skip", "s", "abort", "quit", "q"):
            io.write("   Skipped — leaving this application untouched.")
            return False
        if resume_check():
            io.write("   ✓ Page is clear — resuming from where we paused.")
            return True
        io.write(f"   Still detecting the {blocker.kind.value}. Finish it in the browser, then press Enter.")
    io.write("   Gave up waiting for the page to clear.")
    return False
