"""Map the answer bank + resume onto a read form — purely, then apply to a page.

``build_fill_plan`` is a **pure function**: given the enumerated fields, the
answer bank, resolved contact, and the tailored resume path, it returns a
:class:`FillPlan` — what will be filled (with the source of every value) and
what is deliberately left empty (with a reason). It never guesses: an
unmatched or ambiguous field is recorded as ``Unfilled``, to be surfaced in the
review and paused on. Credential/password fields are never filled.

``apply_plan`` is the only part that touches Playwright; it walks the plan and
sets each value on the live page.
"""

from __future__ import annotations

import re
from pathlib import Path

from job_agent.apply.answer_bank import DECLINE_TO_STATE, AnswerBank, Contact
from job_agent.apply.fields import (
    FieldType,
    FillPlan,
    FormField,
    PlannedFill,
    Unfilled,
)

_NO_MATCH = "no matching answer in the answer bank (pause to fill by hand)"

# Legal consent / acknowledgment markers. Any field whose label carries one is
# NEVER auto-filled — it always pauses for the human, whatever its type. (Found
# the hard way: a "confirm receipt of the Global Data Privacy Notice and US
# Arbitration Agreement" field got notice_period via the bare word "notice".)
# "authorize" is deliberately absent — it would collide with "authorized to
# work in the US".
_CONSENT_MARKERS = (
    "consent", "agreement", "acknowledge", "acknowledgment", "privacy",
    "arbitration", "terms and conditions", "terms of service", "certify",
    "signature", "waiver", "disclaimer", "confirm receipt", "data protection",
    "gdpr",
)


def _haystack(f: FormField) -> str:
    return f"{f.label} {f.name}".lower()


def _word(hay: str, term: str) -> bool:
    """Whole-word match — keeps 'city' out of 'ethnicity' and 'state' out of
    'personal statement'."""
    return re.search(rf"\b{re.escape(term)}\b", hay) is not None


def _fmt_years(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def _yes_no(options: tuple[str, ...], want_yes: bool) -> str:
    """Pick the option matching a yes/no answer, defaulting to 'Yes'/'No'."""
    want = "yes" if want_yes else "no"
    for opt in options:
        if opt.strip().lower().startswith(want):
            return opt
    return "Yes" if want_yes else "No"


def _match_option(options: tuple[str, ...], want: str) -> str | None:
    """Return the option equal-ish to ``want`` (case-insensitive), else None."""
    want = want.strip().lower()
    for opt in options:
        if opt.strip().lower() == want or want in opt.strip().lower():
            return opt
    return None


_US_COUNTRY_SPELLINGS = ("united states of america", "united states", "usa",
                         "u.s.a.", "u.s.", "america")


def _match_country(options: tuple[str, ...], want: str) -> str | None:
    """Pick the country option, bridging spellings ("USA" bank vs a "United
    States" dropdown option). Free-text fields just get the bank value."""
    if not options:
        return want
    hit = _match_option(options, want)
    if hit:
        return hit
    if want.strip().lower() in _US_COUNTRY_SPELLINGS:
        for alias in _US_COUNTRY_SPELLINGS:
            if (hit := _match_option(options, alias)):
                return hit
    return None


def _eeo_value(f: FormField, bank: AnswerBank) -> tuple[str, str]:
    """(value, source) for a self-identification field — declines by default."""
    hay = _haystack(f)
    eeo = bank.eeo
    # Hispanic/Latino is checked FIRST: forms often title it "Ethnicity: Are you
    # Hispanic or Latino?", and "ethnic" alone would otherwise mis-route it to
    # the race value. Race keeps only the separate race/ethnicity question.
    key = ("hispanic" if "hispanic" in hay or "latino" in hay or "latinx" in hay
           else "gender" if "gender" in hay or "sex" in hay
           else "race" if "race" in hay or "ethnic" in hay
           else "veteran" if "veteran" in hay
           else "disability" if "disab" in hay else "")
    value = getattr(eeo, {"hispanic": "hispanic_latino", "gender": "gender", "race": "race",
                          "veteran": "veteran_status",
                          "disability": "disability_status"}[key]) if (eeo and key) else DECLINE_TO_STATE
    if f.options:
        value = _match_option(f.options, value) or _match_option(f.options, "decline") or value
    return value, f"answer_bank.eeo.{key or 'decline'}"


def _text_value(f: FormField, bank: AnswerBank, contact: Contact) -> tuple[str, str] | None:
    """Resolve a text/select field to (value, source), or None if unmatched."""
    hay = _haystack(f)

    def has(*words: str) -> bool:
        return all(w in hay for w in words)

    # contact identity (from career facts) --------------------------------
    if "first name" in hay:
        return contact.name.split()[0], "career_facts.name"
    if "last name" in hay or "surname" in hay:
        return contact.name.split()[-1], "career_facts.name"
    if has("full", "name") or hay.strip() in {"name", "your name", "name*"}:
        return contact.name, "career_facts.name"
    if "email" in hay:
        return contact.email, "career_facts.email"
    if "phone" in hay or "mobile" in hay:
        return contact.phone, "career_facts.phone"
    if "linkedin" in hay:
        return (contact.linkedin, "answer_bank.linkedin") if contact.linkedin else None
    if "github" in hay:
        return (contact.github, "answer_bank.github") if contact.github else None

    # employment history / education / opt-ins ----------------------------
    # "previously employed" is checked BEFORE the employer matcher so a
    # "Have you previously been employed by X?" screener can never receive
    # the company name.
    if ("previously" in hay and ("employed" in hay or "worked" in hay)) or "former employee" in hay:
        picked = _match_option(f.options, bank.previously_employed_here) if f.options \
            else bank.previously_employed_here
        return (picked, "answer_bank.previously_employed_here") if picked else None
    if "whatsapp" in hay:
        picked = _match_option(f.options, bank.whatsapp_optin) if f.options else bank.whatsapp_optin
        return (picked, "answer_bank.whatsapp_optin") if picked else None
    if _word(hay, "employer") or "current company" in hay or "recent company" in hay:
        return (contact.employer, "career_facts.employer") if contact.employer else None
    if _word(hay, "title") and ("job" in hay or "current" in hay or "recent" in hay
                                or "position" in hay):
        # bare "Title" is often a salutation (Mr/Ms) — needs a job cue to map
        return (contact.title, "career_facts.title") if contact.title else None
    if (_word(hay, "school") and "high school" not in hay) or _word(hay, "university") \
            or _word(hay, "college") or "alma mater" in hay:
        return (contact.school, "career_facts.school") if contact.school else None
    if _word(hay, "degree") or "highest education" in hay or "level of education" in hay:
        return (contact.degree, "career_facts.degree") if contact.degree else None

    # work authorization (booleans -> option) -----------------------------
    if "sponsor" in hay:
        return _yes_no(f.options, bank.requires_sponsorship), "answer_bank.requires_sponsorship"
    if "authoriz" in hay or ("legally" in hay and "work" in hay) or has("eligible", "work"):
        return _yes_no(f.options, bank.authorized_us), "answer_bank.authorized_us"

    # compensation / availability / experience ----------------------------
    if "salary" in hay or "compensation" in hay or has("expected", "pay"):
        return (bank.salary_expectation, "answer_bank.salary_expectation") if bank.salary_expectation else None
    if "notice period" in hay:   # NOT bare "notice" — see _CONSENT_MARKERS story
        return (bank.notice_period, "answer_bank.notice_period") if bank.notice_period else None
    if "start date" in hay or has("start") and ("available" in hay or "when" in hay):
        return (bank.earliest_start_date, "answer_bank.earliest_start_date") if bank.earliest_start_date else None
    if "years" in hay and ("experience" in hay or "exp" in hay):
        return (_fmt_years(bank.total_years_experience), "answer_bank.total_years_experience") \
            if bank.total_years_experience is not None else None

    # location / mobility --------------------------------------------------
    if "relocat" in hay:
        if bank.willing_to_relocate is None:
            return None
        return _yes_no(f.options, bank.willing_to_relocate), "answer_bank.willing_to_relocate"
    if "remote" in hay or "work mode" in hay or "arrangement" in hay or "on-site" in hay or "onsite" in hay:
        if not bank.work_mode:
            return None
        picked = _match_option(f.options, bank.work_mode) if f.options else bank.work_mode
        return (picked, "answer_bank.work_mode") if picked else None

    # structured location — checked BEFORE the generic "location" fallback, and
    # each returns None (-> unfilled, pause) when the bank value is empty, so
    # the location_preference sentence can never land in a city/state/zip field.
    if _word(hay, "zip") or "postal" in hay:
        return (bank.zip, "answer_bank.zip") if bank.zip else None
    if _word(hay, "city") or _word(hay, "town"):
        return (bank.city, "answer_bank.city") if bank.city else None
    if _word(hay, "state") or "province" in hay:
        return (bank.state, "answer_bank.state") if bank.state else None
    if _word(hay, "country") or _word(hay, "countries") or "reside" in hay \
            or "applying from" in hay or "anticipate working" in hay:
        if not bank.country:
            return None
        picked = _match_country(f.options, bank.country)
        return (picked, "answer_bank.country") if picked else None

    if _word(hay, "location"):
        val = bank.location_preference or contact.location
        return (val, "answer_bank.location_preference") if val else None

    return None


def _is_legal_consent(f: FormField) -> bool:
    """True for consent / acknowledgment / legal-agreement fields."""
    hay = _haystack(f)
    return any(m in hay for m in _CONSENT_MARKERS) or _word(hay, "agree")


def _resolve(f: FormField, bank: AnswerBank, contact: Contact,
             resume_path: Path | None) -> PlannedFill | Unfilled:
    """Resolve a single field to either a PlannedFill or an Unfilled (never guess)."""
    # FIRST check, before any mapping — a consent field must never receive an
    # unrelated value; the human reads and answers it (a required one blocks
    # approval at the review gate until edited in or skipped).
    if _is_legal_consent(f):
        return Unfilled(f, "legal consent/acknowledgment — never auto-filled "
                           "(read and answer this yourself)")

    if f.field_type == FieldType.CREDENTIAL:
        return Unfilled(f, "credential field — never auto-filled (log in yourself)")

    if f.field_type == FieldType.FILE:
        if resume_path is None:
            return Unfilled(f, "no tailored resume provided (pass --resume)")
        return PlannedFill(f, str(resume_path), "resume (tailored PDF)")

    if f.field_type == FieldType.EEO:
        value, source = _eeo_value(f, bank)
        return PlannedFill(f, value, source)

    if f.field_type == FieldType.TEXTAREA:
        hay = _haystack(f)
        for key, answer in bank.prepared_answers.items():
            if key.lower() in hay:
                return PlannedFill(f, answer, f"answer_bank.prepared_answers[{key!r}]")
        # a plain-text matcher may still apply (e.g. a textarea labelled "salary")
        hit = _text_value(f, bank, contact)
        if hit:
            return PlannedFill(f, hit[0], hit[1])
        return Unfilled(f, "free-text question — no prepared answer (pause to answer)")

    hit = _text_value(f, bank, contact)
    if hit:
        return PlannedFill(f, hit[0], hit[1])
    return Unfilled(f, _NO_MATCH)


def build_fill_plan(fields: tuple[FormField, ...], bank: AnswerBank,
                    contact: Contact, resume_path: Path | None = None) -> FillPlan:
    """Pure: resolve every field to a PlannedFill or an Unfilled."""
    planned: list[PlannedFill] = []
    unfilled: list[Unfilled] = []
    for f in fields:
        result = _resolve(f, bank, contact, resume_path)
        (planned if isinstance(result, PlannedFill) else unfilled).append(result)  # type: ignore[arg-type]
    return FillPlan(planned=tuple(planned), unfilled=tuple(unfilled))


def apply_plan(page, plan: FillPlan) -> None:
    """Drive Playwright to enter each planned value on the live page.

    Fills only what's in ``plan.planned`` — unfilled fields are never touched.
    Uses the field type to choose the right interaction.
    """
    for pf in plan.planned:
        f = pf.field
        locator = page.locator(f.selector).first
        if f.field_type == FieldType.FILE:
            locator.set_input_files(pf.value)
        elif f.field_type in (FieldType.SELECT, FieldType.EEO) and f.options:
            try:
                locator.select_option(label=pf.value)
            except Exception:
                locator.select_option(pf.value)
        elif f.field_type == FieldType.CHECKBOX:
            if pf.value.strip().lower() in {"yes", "true", "on"}:
                locator.check()
        elif f.field_type == FieldType.RADIO:
            page.get_by_label(pf.value).first.check()
        else:
            locator.fill(pf.value)
