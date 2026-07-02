"""Tailoring: mega prompt + career facts + JD → resume text + NOTES.

The mega prompt is the system instruction; the user turn carries the immutable
career facts (the constraints) and the target job description. The model returns
the strict-format resume followed by a separate NOTES block, which we split out.

The LLM call is injectable (``generate`` / ``stub_response``) so demo mode and
tests run with no key and no network.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from job_agent.config import Settings
from job_agent.tailor.career_facts import CareerFacts

# Tailoring quality model — Sonnet-class, confirmed from the Anthropic docs.
TAILOR_MODEL = "claude-sonnet-4-6"

MEGAPROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "tailor_megaprompt.txt"

# Case-insensitive start of the trailing NOTES block.
_NOTES_RE = re.compile(r"(?im)^[#*\s]*NOTES\b.*$")


class TailorResult(BaseModel):
    """The parsed tailoring output."""

    model_config = ConfigDict(frozen=True)

    resume_text: str
    notes: str
    raw: str


def load_megaprompt(path: str | Path = MEGAPROMPT_PATH) -> str:
    return Path(path).read_text().strip()


def build_facts_block(facts: CareerFacts) -> str:
    """Serialize the immutable career facts into the constraint block."""
    lines: list[str] = [
        "=== MY CAREER FACTS (IMMUTABLE — obey exactly) ===",
        f"Name: {facts.name}",
        f"Role: {facts.role}",
        f"Email: {facts.email}",
        f"Phone: {facts.phone}",
    ]
    if facts.location:
        lines.append(f"Location: {facts.location}")
    lines.append("\nEducation:")
    lines += [f"- {e}" for e in facts.education]

    lines.append("\nCertifications I actually hold (print ONLY these; if none, print none):")
    if facts.certifications:
        lines += [f"- {c.name}" + (f" ({c.issuer})" if c.issuer else "") for c in facts.certifications]
    else:
        lines.append("- (none)")

    lines.append("\nReal skills inventory (use only these, in real contexts):")
    for category, skills in facts.skills_inventory.items():
        lines.append(f"- {category}: " + "; ".join(skills))

    lines.append("\nEmployers (company / title / duration are FIXED — never change/add/remove):")
    for e in facts.employers:
        lines.append(f"\n>>> {e.company} | {e.title} | {e.location} | {e.duration}")
        if e.project_description:
            lines.append(f"Project: {e.project_description}")
        lines.append("Real responsibilities (rewrite for the JD using only these facts):")
        lines += [f"  - {b}" for b in e.real_bullets]
        lines.append("Real metrics (the ONLY numbers you may cite for this role):")
        lines += ([f"  - {m}" for m in e.real_metrics] or ["  - (none — use a JD-aware [METRIC — …?] placeholder)"])
        lines.append("Real tools available in this engagement (era-appropriate; no anachronisms):")
        lines.append("  " + "; ".join(e.real_skills))
    return "\n".join(lines)


def build_user_message(facts: CareerFacts, jd_text: str) -> str:
    return (
        f"{build_facts_block(facts)}\n\n"
        "=== TARGET JOB DESCRIPTION ===\n"
        f"{jd_text.strip()}\n\n"
        "Produce the tailored resume in the strict format, then the separate NOTES block."
    )


def split_notes(raw: str) -> tuple[str, str]:
    """Split model output into (resume_text, notes) on the NOTES delimiter."""
    match = _NOTES_RE.search(raw)
    if not match:
        return raw.strip(), ""
    return raw[: match.start()].strip(), raw[match.start():].strip()


def _default_generate(system: str, user: str, settings: Settings) -> str:
    """Real Sonnet call (imported lazily so demo/tests need no SDK/key)."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=TAILOR_MODEL,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def tailor_resume(
    facts: CareerFacts,
    jd_text: str,
    *,
    settings: Settings | None = None,
    megaprompt: str | None = None,
    generate=None,
    stub_response: str | None = None,
) -> TailorResult:
    """Tailor the resume to ``jd_text``.

    * ``stub_response`` — use canned text instead of calling the model (demo/tests).
    * ``generate`` — inject a custom ``(system, user, settings) -> str`` (tests).
    * otherwise a real Sonnet call is made using ``settings``.
    """
    system = megaprompt or load_megaprompt()
    user = build_user_message(facts, jd_text)

    if stub_response is not None:
        raw = stub_response
    elif generate is not None:
        raw = generate(system, user, settings)
    else:
        if settings is None or not settings.anthropic_api_key:
            raise ValueError("A real tailoring run needs Settings with an ANTHROPIC_API_KEY.")
        raw = _default_generate(system, user, settings)

    resume_text, notes = split_notes(raw)
    return TailorResult(resume_text=resume_text, notes=notes, raw=raw)
