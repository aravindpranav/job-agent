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
from job_agent.geo import infer_country
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


def _pick_location_option(options: tuple[str, ...], want: str) -> str | None:
    """Pick exactly ONE work-mode/location option, never a non-US one.

    From ["Remote Canada", "Remote US", "Hybrid"] with want="remote": candidates
    are both remotes; "Remote Canada" is discarded (infer_country -> Canada),
    "Remote US" wins (infer_country -> US). A generic "Remote" (no country) is
    fine when it's the only candidate; several ambiguous candidates -> None
    (pause) rather than guess.
    """
    cands = [o for o in options if want.lower() in o.lower()]
    cands = [o for o in cands if infer_country(o) in (None, "US")]  # never non-US
    if not cands:
        return None
    us_marked = [o for o in cands if infer_country(o) == "US"]
    if us_marked:
        return us_marked[0]
    return cands[0] if len(cands) == 1 else None


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


# Decline-option wordings across ATSes: Greenhouse "Decline To Self Identify",
# Ashby "Prefer not to answer"/"Prefer not to say", plus "I don't wish to answer".
_DECLINE_VARIANTS = ("decline", "prefer not", "don't wish", "do not wish", "rather not")


def _match_decline(options: tuple[str, ...]) -> str | None:
    for variant in _DECLINE_VARIANTS:
        if (hit := _match_option(options, variant)):
            return hit
    return None


def _eeo_value(f: FormField, bank: AnswerBank) -> tuple[str, str]:
    """(value, source) for a self-identification field.

    Deterministic from the answer bank (top-level key, else the eeo block) —
    never LLM-drafted, never guessed: an absent key resolves to the form's own
    decline option ("Decline To Self Identify" / "Prefer not to answer" / ...).
    """
    hay = _haystack(f)
    # Hispanic/Latino is checked FIRST: forms often title it "Ethnicity: Are you
    # Hispanic or Latino?", and "ethnic" alone would otherwise mis-route it to
    # the race value. Race keeps only the separate race/ethnicity question.
    key = ("hispanic_latino" if "hispanic" in hay or "latino" in hay or "latinx" in hay
           else "gender" if "gender" in hay or "sex" in hay
           else "race" if "race" in hay or "ethnic" in hay
           else "veteran_status" if "veteran" in hay
           else "disability_status" if "disab" in hay else "")
    value = bank.eeo_answer(key) if key else DECLINE_TO_STATE
    if f.options:
        value = _match_option(f.options, value) or _match_decline(f.options) or value
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
    # Full name: the word "name" without a qualifier that means a DIFFERENT
    # name (Ashby renders it as label "Name" + name="_systemfield_name", which
    # an exact-string check missed).
    if _word(hay, "name") and not any(w in hay for w in (
            "first", "last", "middle", "company", "employer", "school",
            "university", "college", "user", "file", "nickname")):
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
    if _word(hay, "discipline") or "field of study" in hay:
        if not bank.discipline:
            return None
        picked = _match_option(f.options, bank.discipline) if f.options else bank.discipline
        return (picked, "answer_bank.discipline") if picked else None

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
    # Office-preference options ("New York City Office", "San Francisco HQ")
    # are choices about THEIR offices, not our city/work_mode — pause. An
    # arrangement cue ("remote or in-office?") means it's a work-mode question,
    # which the next matcher handles.
    if ("office" in hay or _word(hay, "hq") or "headquarters" in hay) and not any(
            cue in hay for cue in ("remote", "arrangement", "work mode",
                                   "hybrid", "on-site", "onsite")):
        return None
    if "remote" in hay or "work mode" in hay or "arrangement" in hay or "on-site" in hay or "onsite" in hay:
        if not bank.work_mode:
            return None
        if f.field_type == FieldType.CHECKBOX and not f.options:
            # A bare checkbox labeled "Remote US" / "Remote Canada" is one
            # option of a location picker — checking it from work_mode is how
            # multiple (and non-US) options got selected. Pause instead.
            return None
        if f.options:
            picked = _pick_location_option(f.options, bank.work_mode)
            return (picked, "answer_bank.work_mode") if picked else None
        return bank.work_mode, "answer_bank.work_mode"

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


def click_select(page, locator, value: str) -> bool:
    """Select ``value`` in an ARIA combobox/listbox by CLICKING its option.

    Opens the widget, types the value when it accepts text (filters the list),
    then clicks the [role=option] whose name matches — exact first, containing
    second. If no option matches, the popup is closed and NOTHING is selected
    (never guess a near-miss option); returns False so callers can report it.
    """
    locator.click()
    try:
        locator.fill(value)          # input-backed combobox: typing filters options
    except Exception:
        pass                         # div-backed widget — click alone opens it
    exact = re.compile(rf"^\s*{re.escape(value)}\s*$", re.IGNORECASE)
    loose = re.compile(re.escape(value), re.IGNORECASE)
    for pattern in (exact, loose):
        option = page.get_by_role("option", name=pattern).first
        try:
            option.click(timeout=3000)
            return True
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")   # close the popup; leave it unselected
    except Exception:
        pass
    return False


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
        elif f.field_type == FieldType.COMBOBOX:
            click_select(page, locator, pf.value)
        elif f.field_type in (FieldType.SELECT, FieldType.EEO) and f.options:
            try:
                locator.select_option(label=pf.value)
            except Exception:
                try:
                    locator.select_option(pf.value)
                except Exception:
                    # React select misclassified as native — click path fallback
                    click_select(page, locator, pf.value)
        elif f.field_type in (FieldType.CHECKBOX, FieldType.RADIO) and f.options:
            # grouped picker: check exactly the ONE box whose label was picked
            page.get_by_label(pf.value).first.check()
        elif f.field_type == FieldType.CHECKBOX:
            if pf.value.strip().lower() in {"yes", "true", "on"}:
                locator.check()
        elif f.field_type == FieldType.RADIO:
            page.get_by_label(pf.value).first.check()
        else:
            locator.fill(pf.value)
