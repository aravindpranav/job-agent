"""The application answer bank — validated answers real ATS forms ask for.

Loaded from ``answer_bank.yaml`` (real: gitignored PII) or the committed fake
``answer_bank.example.yaml`` template. Everything here is immutable once loaded
(frozen Pydantic v2), so a downstream filler can never mutate an answer on its
way to a real employer.

Design rules baked in (not just documented):

* **Contact is never duplicated.** Name / email / phone / location come from
  ``career_facts.yaml``; the bank only adds apply-specific links. :func:`resolve_contact`
  merges the two into one :class:`Contact`.
* **Work authorization must be explicit.** ``authorized_us`` and
  ``requires_sponsorship`` have no defaults — a bank that omits them fails to
  load, because we must never guess a work-authorization answer to an employer.
* **Everything else is optional.** A missing salary / notice period / EEO field
  is not an error; Slice 4 records it as unknown and pauses for the human rather
  than inventing a value.
* **EEO is opt-in and always declinable.** Every demographic field defaults to
  ``"Decline to state"``; the whole block is absent unless the user fills it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from job_agent.tailor.career_facts import CareerFacts

#: The always-acceptable answer to any demographic / EEO question.
DECLINE_TO_STATE = "Decline to state"

WorkMode = Literal["remote", "hybrid", "onsite"]


class EeoAnswers(BaseModel):
    """Optional self-identification answers. Every field defaults to declining.

    This block is only present when the user chooses to fill it; each field can
    always be left as :data:`DECLINE_TO_STATE`, which is a valid, complete answer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    gender: str = DECLINE_TO_STATE
    race: str = DECLINE_TO_STATE
    # The "Are you Hispanic/Latino?" question is asked SEPARATELY from race on
    # real forms (e.g. Greenhouse EEO sections) — it maps here, never to race.
    hispanic_latino: str = DECLINE_TO_STATE
    veteran_status: str = DECLINE_TO_STATE
    disability_status: str = DECLINE_TO_STATE


class AnswerBank(BaseModel):
    """Apply-specific answers, minus contact (merged from career facts).

    Only work-authorization is required; the rest are optional so the bank is
    usable while partially filled. Unknown answers stay empty and are surfaced,
    never guessed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- work authorization (REQUIRED — must be explicit, never guessed) -----
    authorized_us: bool = Field(
        description="Are you legally authorized to work in the United States?"
    )
    requires_sponsorship: bool = Field(
        description="Will you now or in the future require visa sponsorship?"
    )

    # --- compensation --------------------------------------------------------
    salary_expectation: str = ""

    # --- location / mobility -------------------------------------------------
    work_mode: WorkMode | None = None
    willing_to_relocate: bool | None = None
    location_preference: str = ""

    # --- structured location (for City / State / ZIP / Country form fields) --
    # These map to the corresponding form fields verbatim; the free-text
    # ``location_preference`` sentence is never used for them.
    city: str = ""            # e.g. "Santa Clara"
    state: str = ""           # e.g. "California"
    zip: str = ""             # e.g. "95050"
    country: str = ""         # e.g. "USA"

    # --- availability --------------------------------------------------------
    notice_period: str = ""
    earliest_start_date: str = ""

    # --- experience ----------------------------------------------------------
    total_years_experience: float | None = Field(default=None, ge=0)

    # --- common yes/no screeners (sensible defaults, overridable) ------------
    previously_employed_here: str = "No"   # "Have you previously worked here?"
    whatsapp_optin: str = "No"             # "Receive updates via WhatsApp?"
    # "How did you hear about this job?" — a plain generic source, deliberately
    # never a specific referrer (that would be inventing a person). Overridable.
    how_heard: str = "LinkedIn"

    # --- EEO / demographic, top-level (optional; overrides the eeo block) ----
    # Resolved deterministically — never LLM-drafted, never guessed. An absent
    # key falls back to declining (see eeo_answer / DECLINE_TO_STATE).
    gender: str = ""
    hispanic_latino: str = ""
    race: str = ""
    veteran_status: str = ""
    disability_status: str = ""
    # Some forms ask your field/discipline (e.g. "Software Engineering").
    discipline: str = ""

    # --- apply-specific links (contact proper comes from career facts) -------
    linkedin: str = ""
    github: str = ""

    # --- optional demographic block ------------------------------------------
    eeo: EeoAnswers | None = None

    # --- AI-draft style knobs (for screening-question drafting) --------------
    answer_max_words: int = Field(default=120, gt=0)
    answer_tone: str = "concise and professional"

    # --- prepared free-text answers, keyed by question keyword ---------------
    #: e.g. {"why this company": "…", "why leaving": "…"}. A Slice-4 filler can
    #: match a form's free-text question against these keys; an unmatched
    #: question still pauses for the human rather than guessing.
    prepared_answers: dict[str, str] = Field(default_factory=dict)

    def eeo_answer(self, key: str) -> str:
        """Resolve one EEO answer: top-level field > eeo block > decline.

        ``key`` is an EEO attribute name (gender, hispanic_latino, race,
        veteran_status, disability_status). Deterministic by construction —
        the fallback is always a decline, never a guess.
        """
        top = getattr(self, key, "")
        if top:
            return top
        if self.eeo is not None:
            return getattr(self.eeo, key)
        return DECLINE_TO_STATE


class Contact(BaseModel):
    """Resolved facts for form-filling: identity, links, and the most recent
    employer/education — all derived from career facts, never typed twice."""

    model_config = ConfigDict(frozen=True)

    name: str
    email: str
    phone: str
    location: str = ""
    linkedin: str = ""
    github: str = ""
    # Most recent employer / education, derived from the career facts (facts
    # list most-recent first, mirroring the resume).
    employer: str = ""     # e.g. "JPMorgan Chase and Co."
    title: str = ""        # e.g. "Generative AI Engineer"
    school: str = ""       # e.g. "University of North Texas"
    degree: str = ""       # e.g. "Masters, Computer Science"


def _link_matching(links: tuple[str, ...], needle: str) -> str:
    """Return the first link containing ``needle`` (case-insensitive), else ""."""
    for link in links:
        if needle in link.lower():
            return link
    return ""


_SCHOOL_WORDS = ("university", "college", "institute", "school", "academy")


def _split_education(entry: str) -> tuple[str, str]:
    """Split one education line into (school, degree).

    "Masters, Computer Science, University of North Texas" -> the segments
    naming an institution become the school; the rest stay the degree,
    verbatim (no reformatting — honesty rule).
    """
    parts = [p.strip() for p in entry.split(",") if p.strip()]
    school = [p for p in parts if any(w in p.lower() for w in _SCHOOL_WORDS)]
    degree = [p for p in parts if p not in school]
    return ", ".join(school), ", ".join(degree)


def resolve_contact(facts: CareerFacts, bank: AnswerBank) -> Contact:
    """Merge contact + recent employer/education from ``facts`` with the bank's links.

    Name / email / phone / location / employer / title / school / degree always
    come from the career facts, so the answer bank can never drift from the
    résumé. LinkedIn / GitHub prefer an explicit value in the bank, falling back
    to a matching entry in ``facts.links``.
    """
    linkedin = bank.linkedin or _link_matching(facts.links, "linkedin.com")
    github = bank.github or _link_matching(facts.links, "github.com")
    recent = facts.employers[0]
    school, degree = _split_education(facts.education[0]) if facts.education else ("", "")
    return Contact(
        name=facts.name,
        email=facts.email,
        phone=facts.phone,
        location=facts.location or "",
        linkedin=linkedin,
        github=github,
        employer=recent.company,
        title=recent.title,
        school=school,
        degree=degree,
    )


def load_answer_bank(path: str | Path) -> AnswerBank:
    """Load and validate an answer-bank YAML file with a readable error.

    Mirrors ``load_career_facts``: a missing file, unparseable YAML, or a schema
    violation (e.g. omitted work-authorization) each fail fast with a message
    that names what is wrong.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Answer bank not found: {path}. Copy data/answer_bank.example.yaml to "
            f"data/answer_bank.yaml and fill in your real answers."
        )
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Could not parse {path} as YAML: {exc}") from exc
    try:
        return AnswerBank.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid answer bank {path}:\n{exc}") from exc
