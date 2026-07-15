"""Screening-question answering: route, draft (LLM), gate, review.

Extends the fill pipeline for questions the answer bank can't resolve. Every
unfilled field is routed into one of three buckets:

* **factual** — already resolved deterministically by ``filler.py`` when the
  fact exists; an unfilled factual field stays flagged (never guessed, no LLM).
* **free_text** — open questions ("why us?", "describe a project", cover
  letter): a Haiku draft, grounded ONLY in career facts + answer bank + this
  job's JD, then run through a no-fabrication gate. The draft goes to the
  review gate tagged ``[AI-DRAFT]`` — it is never submitted unreviewed.
* **consent** — legal/consent/EEO/demographic: untouched here; the filler
  already pauses them (``[PAUSED: consent]`` at review).

The no-fabrication gate mirrors the resume no-drift gate: an answer citing an
employer not in the career facts, a metric value not banked there, or a
first-person claim of having used the target company's product is regenerated
once with a stricter instruction, and if still violating is surfaced clearly as
``[GATE-FLAGGED]`` rather than silently passing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

from job_agent.apply.answer_bank import AnswerBank
from job_agent.apply.fields import FieldType, FillPlan, FormField, PlannedFill
from job_agent.apply.filler import _is_legal_consent
from job_agent.tailor.career_facts import CareerFacts
from job_agent.tailor.textnorm import norm as _norm
from job_agent.tailor.verify import _banked_metric_values, _to_value

# A drafter takes an unfilled free-text field and returns the PlannedFill (with
# an "ai-draft..." source tag) or None to leave it unfilled.
Drafter = Callable[[FormField], PlannedFill | None]

NEEDS_INPUT_MARKER = "NEEDS-INPUT:"

# --- routing -----------------------------------------------------------------

_OPEN_QUESTION_CUES = (
    "why", "describe", "tell us", "tell me", "cover letter", "what interests",
    "what excites", "anything else", "how would", "how did", "motivat",
    "in your own words", "share an example", "walk us through",
)


def classify_question(field: FormField, reason: str = "") -> str:
    """Route an unfilled field: 'consent' | 'free_text' | 'factual'."""
    if (field.field_type in (FieldType.EEO, FieldType.CREDENTIAL)
            or _is_legal_consent(field)
            or "consent" in reason or "credential" in reason):
        return "consent"
    hay = f"{field.label} {field.name}".lower()
    if field.field_type == FieldType.TEXTAREA:
        return "free_text"
    if field.field_type in (FieldType.TEXT, FieldType.UNKNOWN) \
            and any(cue in hay for cue in _OPEN_QUESTION_CUES):
        return "free_text"
    return "factual"


# Sub-typing of free-text questions, so each gets a fitting ANSWER FORMAT
# instead of one generic essay style. Checked in order: a factual cue wins
# (short answer, no essay), then a story cue, then a motivation cue.
_SHORT_FACTUAL_CUES = (
    "hear about", "hear of", "heard about", "heard of", "notice period",
    "start date", "earliest start", "referral source", "timezone", "time zone",
)
_EXPERIENCE_CUES = (
    "describe a", "describe your", "tell us about a", "tell me about a",
    "share an example", "walk us through", "give an example", "a time when",
    "a time you",
)
_MOTIVATION_CUES = (
    "why", "what interests", "what excites", "motivat", "interested in",
    "want to work", "want to join",
)


def classify_free_text(field: FormField) -> str:
    """Sub-route a free-text question:
    'short_factual' | 'experience' | 'motivation' | 'general'."""
    hay = f"{field.label} {field.name}".lower()
    if any(cue in hay for cue in _SHORT_FACTUAL_CUES):
        return "short_factual"
    if any(cue in hay for cue in _EXPERIENCE_CUES):
        return "experience"
    if any(cue in hay for cue in _MOTIVATION_CUES):
        return "motivation"
    return "general"


# --- no-fabrication gate -------------------------------------------------------

# Past-employment phrasings only: "worked at X", "my time at X", "while at X".
# Aspirational "excited to work at <target>" is fine and deliberately not caught.
_PAST_EMPLOYMENT = re.compile(
    r"(?:worked|was employed|my (?:time|role|tenure)|while)\s+(?:at|with)\s+"
    r"([A-Z][\w&.'\- ]{1,40}?)(?=[,.;:]|\s+(?:I|we|where|and)\b|$)", re.MULTILINE)

# First-person claims of having used the TARGET company's product/tech.
_USED_PRODUCT = (
    r"\bI(?:'ve| have)?\s+(?:used|use|worked with|built (?:on|with)|integrated|"
    r"shipped[^.]{0,20}with|implemented[^.]{0,20}with)[^.]{0,60}?\b{company}\b")

# Prose metric claims: percentages, $ amounts, K/M magnitudes, x-multipliers, N+.
_METRIC_TOKEN = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*[kKmM]?|\b\d[\d,]*(?:\.\d+)?\s*%|"
    r"\b\d[\d,]*(?:\.\d+)?\s*[kKmM]\b|\b\d[\d,]*(?:\.\d+)?x\b|\b\d[\d,]*(?:\.\d+)?\+")

# Mechanical style rules. DELIBERATE SPLIT: these are the deterministic,
# string-checkable voice rules (dashes, banned phrases, the not-only/but
# construction). TOPICAL honesty — "does the draft claim experience the facts
# don't contain?" — is semantic and is enforced in the PROMPT (DRAFT_SYSTEM +
# _TYPE_INSTRUCTIONS), with the regenerate-once-then-flag gate as backstop.
# Violations here FLAG the draft for human review; nothing is ever silently
# rewritten.
_BANNED_STYLE = (
    "leverage", "spearheaded", "passionate about", "excited to", "deep dive",
    "robust", "seamless", "cutting-edge", "in today's landscape", "i thrive",
    "wealth of experience", "delve", "testament", "underscore",
)
# whole-word, stem-tolerant ("leveraged", "underscores"), flexible whitespace
_BANNED_STYLE_RES = tuple(
    (phrase, re.compile(r"\b" + re.escape(phrase).replace(r"\ ", r"\s+") + r"\w*",
                        re.IGNORECASE))
    for phrase in _BANNED_STYLE)
_NOT_ONLY_BUT = re.compile(
    r"\bnot\s+(?:only|just)\b[^.?!]{0,120}?\bbut\b", re.IGNORECASE | re.DOTALL)


def _style_violations(text: str) -> list[str]:
    """The mechanical voice-rule violations in ``text`` (see split note above)."""
    violations: list[str] = []
    if "—" in text or "–" in text:
        violations.append("style: em/en dash (use a period or a comma)")
    for phrase, pattern in _BANNED_STYLE_RES:
        if pattern.search(text):
            violations.append(f"style: banned phrase {phrase!r}")
    if _NOT_ONLY_BUT.search(text):
        violations.append("style: 'not only/just X but Y' construction")
    return violations


def verify_answer(text: str, facts: CareerFacts, company: str = "") -> list[str]:
    """Return the fabrication violations in ``text`` (empty list = clean)."""
    violations: list[str] = []

    known = facts.employer_companies()
    for match in _PAST_EMPLOYMENT.finditer(text):
        name = match.group(1).strip()
        n = _norm(name)
        if not any(n in k or k in n for k in known):
            violations.append(f"claims past employment at {name!r} (not in career facts)")

    if company:
        # plain replace, NOT .format(): the regex quantifiers {0,20} would be
        # misread as format fields
        pat = _USED_PRODUCT.replace("{company}", re.escape(company))
        if re.search(pat, text, re.IGNORECASE):
            violations.append(
                f"first-person claim of having used {company}'s product/tech")

    banked = _banked_metric_values(facts)
    for tok in _METRIC_TOKEN.findall(text):
        num = re.sub(r"[^\d.,kKmM+]", "", tok)
        if (v := _to_value(num)) is not None and v not in banked:
            violations.append(f"metric {tok.strip()!r} is not among the banked real metrics")

    violations.extend(_style_violations(text))
    return violations


# --- drafting ------------------------------------------------------------------

DRAFT_SYSTEM = (
    "You draft short answers to job-application screening questions ON BEHALF OF "
    "a candidate, for the candidate to review before anything is submitted. You "
    "may use ONLY the career facts, application answers, and job description "
    "provided. HARD RULES: never invent employers, dates, anecdotes, or skills; "
    "cite ONLY the numeric metrics listed in the facts (no other numbers); never "
    "claim the candidate has used the hiring company's products or knows their "
    "internals.\n"
    "HONESTY ABOUT GAPS: if the facts contain no real experience with the thing "
    "the question asks about, the FIRST sentence says so plainly in first "
    "person. You may then name the nearest real work while stating outright "
    "that it is adjacent and a different thing. Never present a different "
    "technique as the asked-for one; never imply the experience exists. If the "
    "honest answer is no, the answer is no plus context. If the question needs "
    "information you were not given (a personal opinion, a specific story), "
    "write the best honest partial answer and append one final line starting "
    f"with '{NEEDS_INPUT_MARKER}' naming what the candidate must add. Every "
    "sentence must be defensible in an interview.\n"
    "VOICE: first person, plain and direct. Sound like an engineer typing an "
    "answer at 11pm, not a cover letter. Short sentences. Concrete nouns and "
    "specific systems. Never use an em-dash or en-dash; use a period or a "
    "comma. Never use these words or phrases: leverage, spearheaded, "
    "passionate about, excited to, deep dive, robust, seamless, cutting-edge, "
    "in today's landscape, I thrive, wealth of experience, delve, testament, "
    "underscore. Never write 'not only X but also Y' or 'it's not just X, "
    "it's Y'. No three-item rhetorical lists. Do not restate the question "
    "before answering. Length matches the question: a yes-with-context is two "
    "sentences, not a paragraph."
)

_STRICTER_NUDGE = (
    "\n\nYour previous draft violated the grounding rules. REGENERATE, strictly: "
    "no employers/metrics/claims beyond the provided facts; if the substance "
    f"isn't in the facts, say less and add a {NEEDS_INPUT_MARKER} line."
)


# Per-subtype answer-format instructions. Grounding rules are unchanged — these
# only shape the FORM of the answer (length, structure, register).
_TYPE_INSTRUCTIONS = {
    "short_factual": (
        "This is a SHORT FACTUAL question. Reply with ONE short factual "
        "phrase or sentence — no essay, no flattery or enthusiasm about the "
        "company, no restating the question. If the fact is not in the data, "
        f"answer plainly with what is known and add a {NEEDS_INPUT_MARKER} line."
    ),
    "experience": (
        "This is an EXPERIENCE question. FIRST decide from the employment "
        "history whether the candidate has real experience with the thing "
        "actually asked about. Only once that topical match is established may "
        "you answer, as ONE concise STAR story (Situation, Task, Action, "
        "Result) from the most relevant matching REAL project, never a "
        "composite or hypothetical. If the facts contain NO real experience "
        "with the asked-about thing: the first sentence states that plainly in "
        "first person, and you may then name the nearest real work while "
        "saying outright that it is adjacent and a different problem. "
        "FORBIDDEN framings: presenting a different technique as the asked-for "
        "one; 'essentially the same as'; 'closely related to'; 'which is a "
        "form of'; opening with an unrelated project and letting the reader "
        "infer; burying the absence after the story. Relevance is not a "
        "substitute for having done the thing. End an absent-experience answer "
        f"with a line starting with '{NEEDS_INPUT_MARKER}' asking the "
        "candidate to add any real experience with it missing from the facts, "
        "or to keep the honest no."
    ),
    "motivation": (
        "This is a MOTIVATION question. Write a short grounded paragraph "
        "connecting the candidate's real experience to this job's stated work — "
        "no generic praise of the company. The candidate's genuine personal "
        "motivation is NOT in the data, so end with a line starting with "
        f"'{NEEDS_INPUT_MARKER}' asking them to add it."
    ),
    "general": "",
}


def build_draft_prompt(field: FormField, facts: CareerFacts, bank: AnswerBank,
                       jd: str, company: str, qtype: str = "general") -> str:
    """The grounded per-question prompt (``qtype`` shapes the answer format)."""
    skills = "; ".join(f"{k}: {', '.join(v)}" for k, v in facts.skills_inventory.items())
    banked = [m for e in facts.employers for m in e.real_metrics]
    history = "\n".join(
        f"- {e.title} at {e.company} ({e.duration}): {e.project_description or ''}"
        for e in facts.employers)
    limit = (f"\nHARD LENGTH LIMIT: {field.max_length} characters."
             if field.max_length else "")
    type_note = _TYPE_INSTRUCTIONS.get(qtype, "")
    type_block = f"{type_note}\n\n" if type_note else ""
    return (
        f"QUESTION (from {company or 'the employer'}'s application form):\n"
        f"{field.label or field.name}\n\n"
        f"{type_block}"
        f"CANDIDATE CAREER FACTS (the ONLY employment history that exists):\n{history}\n"
        f"Education: {'; '.join(facts.education)}\n"
        f"Certifications: {'; '.join(c.name for c in facts.certifications)}\n"
        f"Skills: {skills}\n"
        f"BANKED METRICS (the ONLY numbers you may cite): {'; '.join(banked) or 'none'}\n\n"
        f"CANDIDATE'S APPLICATION ANSWERS: work mode {bank.work_mode or 'n/a'}; "
        f"location preference {bank.location_preference or 'n/a'}; "
        f"salary {bank.salary_expectation or 'n/a'}\n\n"
        f"JOB DESCRIPTION:\n{jd[:4000] or '(not available)'}\n\n"
        f"Write the answer in first person, {bank.answer_tone}, at most "
        f"{bank.answer_max_words} words.{limit} Return ONLY the answer text."
    )


def trim_to_limit(text: str, max_length: int | None) -> str:
    """Hard-respect a form's maxlength, cutting at a word boundary."""
    if max_length is None or len(text) <= max_length:
        return text
    cut = text[:max_length]
    return cut[:cut.rfind(" ")].rstrip() if " " in cut else cut


def _generate_with_retry(generate: Callable[[str], str], prompt: str) -> str:
    """One retry on failure: a single transient API error must not silently
    kill a draft (seen live: one of two identical-shape questions drafted, the
    other fell through to an unrelated pause message)."""
    try:
        return generate(prompt).strip()
    except Exception:
        return generate(prompt).strip()


def make_drafter(generate: Callable[[str], str], facts: CareerFacts, bank: AnswerBank,
                 jd: str, company: str, cache_path: Path | None = None) -> Drafter:
    """Build the drafter: cache -> draft -> gate -> regenerate once -> tag.

    ``generate`` is prompt->text (the real one is :func:`make_llm_generate`;
    tests inject a fake). Returned fills carry an ``ai-draft...`` source so the
    review gate can tag them and refuse blind auto-approval.
    """
    cache = _load_cache(cache_path) if cache_path else {}

    def drafter(field: FormField) -> PlannedFill | None:
        cached = cache.get(_cache_key(company, field.label))
        if cached:
            return PlannedFill(field, trim_to_limit(cached["answer"], field.max_length),
                               "ai-draft (cached — you approved this before; re-review)")
        qtype = classify_free_text(field)
        prompt = build_draft_prompt(field, facts, bank, jd, company, qtype)
        try:
            text = _generate_with_retry(generate, prompt)
        except Exception:
            return None  # LLM failure -> leave the question unfilled (paused)
        violations = verify_answer(text, facts, company)
        if violations:
            try:
                text = _generate_with_retry(generate, prompt + _STRICTER_NUDGE)
            except Exception:
                return None
            violations = verify_answer(text, facts, company)
        # Genuine per-company motivation is never in the data: guarantee the
        # flag in code, whether or not the model remembered to add it.
        if qtype == "motivation" and NEEDS_INPUT_MARKER not in text:
            text = (f"{text}\n{NEEDS_INPUT_MARKER} add your genuine reason for "
                    f"wanting to work at {company or 'this company'}.")
        text = trim_to_limit(text, field.max_length)
        if violations:
            source = "ai-draft GATE-FLAGGED: " + "; ".join(violations)
        elif qtype == "motivation" or NEEDS_INPUT_MARKER in text:
            source = "ai-draft NEEDS-INPUT (see the flag line — add what's missing)"
        else:
            source = "ai-draft (grounded in career facts + JD; review before approving)"
        return PlannedFill(field, text, source)

    return drafter


def make_llm_generate(settings) -> Callable[[str], str]:
    """The real prompt->text callable (same Haiku model/client style as scoring)."""
    import anthropic  # lazy, like scoring: importing this module needs no key

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def generate(prompt: str) -> str:
        resp = client.messages.create(
            model=settings.model,
            max_tokens=700,
            system=DRAFT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    return generate


def apply_drafts(plan: FillPlan, drafter: Drafter) -> FillPlan:
    """Route every unfilled field; draft the free-text ones. Pure over the plan.

    Consent stays unfilled (the drafter is never even called for it); factual
    stays flagged; free-text moves to planned with an ai-draft source.
    """
    from job_agent.apply.fields import Unfilled

    planned = list(plan.planned)
    unfilled = []
    for u in plan.unfilled:
        if classify_question(u.field, u.reason) == "free_text":
            fill = drafter(u.field)
            if fill is not None:
                planned.append(fill)
                continue
            # drafting was attempted and failed — say so; the original bank
            # message would misdescribe what happened
            u = Unfilled(u.field, "the AI draft failed (temporary error) — "
                                  "try again, or write this one yourself")
        unfilled.append(u)
    return FillPlan(planned=tuple(planned), unfilled=tuple(unfilled))


# --- approved-answer cache (gitignored, under /data) -----------------------------


def _cache_key(company: str, question: str) -> str:
    digest = hashlib.sha1(question.strip().lower().encode()).hexdigest()[:16]
    return f"{company.strip().lower()}:{digest}"


def _load_cache(path: Path | None) -> dict:
    if path is None or not Path(path).exists():
        return {}
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def store_approved_answers(path: Path, company: str, plan: FillPlan) -> None:
    """Persist human-approved AI answers so repeat questions come pre-drafted.

    Called only after an explicit approval; cached answers still re-enter the
    review as [AI-DRAFT] next time — never auto-approved.
    """
    path = Path(path)
    cache = _load_cache(path)
    for p in plan.planned:
        if p.source.lower().startswith("ai-draft"):
            cache[_cache_key(company, p.field.label)] = {
                "question": p.field.label, "answer": p.value}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))
