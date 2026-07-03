"""A tiny console-IO seam so interactive gates are testable offline.

The approval gate (``review``) and the pause/resume handoff (``handoff``) must
block for real human input during a live run, but in tests we drive them with a
scripted list of answers and capture what was printed. Both take a :class:`PromptIO`;
production passes the real terminal, tests pass a fake.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class PromptIO:
    """Reader/writer pair. Defaults to the real terminal (``input``/``print``)."""

    read: Callable[[str], str] = input
    write: Callable[[str], None] = print


@dataclass
class ScriptedIO:
    """Test double: replays queued answers, records every written line.

    ``read`` pops the next scripted answer; running out is an explicit error
    (a gate that reads more than the test scripted is a bug worth surfacing).
    """

    answers: list[str]
    written: list[str] = field(default_factory=list)

    def read(self, prompt: str = "") -> str:
        self.written.append(prompt)
        if not self.answers:
            raise EOFError("ScriptedIO ran out of answers (gate asked for more input).")
        return self.answers.pop(0)

    def write(self, message: str = "") -> None:
        self.written.append(message)

    def as_io(self) -> PromptIO:
        return PromptIO(read=self.read, write=self.write)

    @property
    def transcript(self) -> str:
        return "\n".join(self.written)
