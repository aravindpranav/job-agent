"""Grounded yes/no answers: factual possession questions career_facts can settle.

Deterministic by design — no LLM, no fuzzy scoring. A yes/no question is
answered ONLY when:

  * YES — every content term of the question is explicitly present
    (whole-word) in the facts corpus: skills inventory, per-employer
    real_bullets / real_metrics / real_skills, certifications, education, and
    the profile location. Project descriptions are deliberately EXCLUDED — they
    are company blurbs ("a leading global firm") and would manufacture false
    yeses.
  * NO — only the single-valued location fact can contradict a question
    ("located in Canada?" when the profile says USA). Absence of a skill is
    never treated as contradiction: it pauses.

Everything else pauses exactly as before. Hard exclusions pause regardless of
options: self-identification/EEO, consent/legal, visa/sponsorship/work
authorization, salary, notice period, and any free-text control.

A grounded answer is tagged ``[GROUNDED]`` and carries its grounding fact, so
the human sees "Yes — JPMorgan: deployed ML models to production…" in the
review panel and can veto it BEFORE it reaches the form — the extension treats
the tag like a draft (review-then-insert), never an auto-fill.
"""

from __future__ import annotations

import re

from job_agent.apply.fields import FieldType, FillPlan, FormField, PlannedFill
from job_agent.geo import infer_country
from job_agent.tailor.career_facts import CareerFacts

#: Control types a yes/no can arrive as. Free text is structurally excluded.
_YES_NO_TYPES = frozenset({FieldType.RADIO, FieldType.SELECT, FieldType.TOGGLE})

#: Hard exclusions — these topics always pause, whatever the options say.
_EXCLUDED_TOPICS = (
    # self-identification / EEO
    "gender", "race", "ethnic", "veteran", "disability", "disabled", "hispanic",
    "latino", "latinx", "lgbtq", "orientation", "pronoun", "transgender",
    # consent / legal
    "consent", "agree", "agreement", "acknowledge", "privacy", "terms",
    "certify", "signature", "arbitration",
    # visa / sponsorship / work authorization
    "visa", "sponsor", "sponsorship", "authorized", "authorization",
    "work permit", "clearance",
    # compensation and notice
    "salary", "compensation", "pay", "wage", "notice",
)

#: Judgment/soft-skill markers — never factual, never grounded.
_JUDGMENT_WORDS = (
    "lead", "leads", "led", "leading", "leadership", "manage", "managed",
    "managing", "management", "mentor", "mentored", "team", "teams", "senior",
    "expert", "fluent", "willing", "comfortable", "prefer", "preferred",
    "relocate", "relocation", "travel", "ambiguity",
)

#: Words that carry no factual content in a possession question — they are
#: dropped, NOT matched. "production" is deliberately not here: it is a real
#: claim that must be backed by the facts.
_GENERIC_WORDS = frozenset("""
do does you your have has had did are is was were the a an any with in on of to
for at or and ever currently previously prior previous experience experienced
work worked working use used using take taken built build building knowledge
know known familiar familiarity proficient proficiency hands skill skills
please required minimum
""".split())

_POSSESSION_SHAPES = ("do you have", "have you", "did you", "do you possess",
                      "do you know", "are you experienced")

_LOCATION_RE = re.compile(
    r"\b(?:located|based|reside|residing|live|living)\s+in\s+(?:the\s+)?(.+?)\s*\??$",
    re.IGNORECASE)

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.-]*")


def _word_in(term: str, text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                     text, re.IGNORECASE) is not None


def _facts_lines(facts: CareerFacts) -> list[tuple[str, str]]:
    """(source label, fact text) pairs — the ONLY licence pool for a yes.
    Project descriptions are excluded (company blurbs, not the candidate's
    own experience)."""
    lines: list[tuple[str, str]] = []
    for group, skills in facts.skills_inventory.items():
        for s in skills:
            lines.append(("skills inventory", f"{group}: {s}"))
    for e in facts.employers:
        for b in e.real_bullets:
            lines.append((e.company, b))
        for m in e.real_metrics:
            lines.append((e.company, m))
        if e.real_skills:
            lines.append((e.company, "tools used: " + "; ".join(e.real_skills)))
    for c in facts.certifications:
        lines.append(("certifications", c.name))
    for ed in facts.education:
        lines.append(("education", ed))
    return lines


def _pure_yes_no(field: FormField) -> bool:
    if field.field_type not in _YES_NO_TYPES:
        return False
    return {o.strip().lower() for o in field.options} == {"yes", "no"}


def _option_cased(field: FormField, answer: str) -> str:
    """The form's own spelling of Yes/No — filled verbatim, never invented."""
    return next(o for o in field.options if o.strip().lower() == answer)


def _location_answer(label: str, facts: CareerFacts) -> tuple[str, str] | None:
    match = _LOCATION_RE.search(label)
    if not match or not facts.location:
        return None
    facts_country = infer_country(facts.location)
    asked_country = infer_country(match.group(1))
    if facts_country is None or asked_country is None:
        return None                      # can't settle it — pause
    grounding = f"profile location: {facts.location}"
    return ("yes" if asked_country == facts_country else "no", grounding)


def _skill_answer(label: str, facts: CareerFacts) -> tuple[str, str] | None:
    low = label.lower()
    if not any(shape in low for shape in _POSSESSION_SHAPES):
        return None
    terms = [t for t in _TOKEN.findall(low) if t not in _GENERIC_WORDS]
    if not terms:
        return None                      # nothing factual to check
    lines = _facts_lines(facts)
    matched_lines: dict[str, list[tuple[str, str]]] = {}
    for term in terms:
        hits = [(src, text) for src, text in lines if _word_in(term, text)]
        if not hits:
            return None                  # partial support is not support — pause
        matched_lines[term] = hits
    # Grounding line: most matched terms wins; rarer terms weigh more so a
    # specific skill line beats an incidental generic hit.
    df = {term: len(hits) for term, hits in matched_lines.items()}

    def score(entry: tuple[str, str]) -> float:
        return sum(1.0 / df[t] for t in terms
                   if any(entry == hit for hit in matched_lines[t]))

    best = max((hit for hits in matched_lines.values() for hit in hits), key=score)
    src, text = best
    return ("yes", f"{src}: {text[:160]}")


def ground_yes_no(field: FormField, facts: CareerFacts) -> tuple[str, str] | None:
    """(value in the form's own casing, grounding fact) — or None: pause.

    None is the default and the safe answer; every gate below only narrows.
    """
    if not _pure_yes_no(field):
        return None
    label = (field.label or field.name or "").strip()
    low = label.lower()
    if any(topic in low for topic in _EXCLUDED_TOPICS):
        return None
    if any(_word_in(w, low) for w in _JUDGMENT_WORDS):
        return None
    answer = _location_answer(label, facts) or _skill_answer(label, facts)
    if answer is None:
        return None
    value, grounding = answer
    return _option_cased(field, value), grounding


#: Unfilled reasons that must never be reconsidered here.
_UNTOUCHABLE_REASONS = ("consent", "credential")


def apply_grounded_answers(plan: FillPlan, facts: CareerFacts) -> FillPlan:
    """Move explicitly-supported yes/no questions from unfilled to planned.

    Pure: returns a new FillPlan; the input plan is untouched. Consent and
    credential pauses are never reconsidered, whatever the field looks like.
    """
    planned = list(plan.planned)
    still_unfilled = []
    for u in plan.unfilled:
        reason_low = u.reason.lower()
        hit = (None if any(r in reason_low for r in _UNTOUCHABLE_REASONS)
               else ground_yes_no(u.field, facts))
        if hit is None:
            still_unfilled.append(u)
            continue
        value, grounding = hit
        planned.append(PlannedFill(
            u.field, value, f"grounded-fact: {value} — {grounding}"))
    return FillPlan(planned=tuple(planned), unfilled=tuple(still_unfilled))
