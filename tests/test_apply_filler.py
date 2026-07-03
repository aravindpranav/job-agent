"""Field-mapping logic: known answers map correctly; unknowns are recorded."""

from __future__ import annotations

from pathlib import Path

from job_agent.apply.answer_bank import AnswerBank, Contact
from job_agent.apply.fields import FieldType, FormField
from job_agent.apply.filler import apply_plan, build_fill_plan

CONTACT = Contact(
    name="Jordan Rivers", email="jordan@example.com", phone="+1 (555) 010-0100",
    location="Remote, US", linkedin="https://linkedin.com/in/jordan", github="https://github.com/jordan",
)
BANK = AnswerBank.model_validate({
    "authorized_us": True,
    "requires_sponsorship": False,
    "salary_expectation": "$160k",
    "work_mode": "remote",
    "willing_to_relocate": False,
    "notice_period": "2 weeks",
    "earliest_start_date": "2025-09-15",
    "total_years_experience": 6,
    "linkedin": "https://linkedin.com/in/jordan",
    "prepared_answers": {"why do you want": "Because your data platform is great."},
})


def _f(selector, ftype, label="", name="", required=False, options=()):
    return FormField(selector=selector, field_type=ftype, label=label, name=name,
                     required=required, options=tuple(options))


def _plan_for(fields, resume=Path("/tmp/resume.pdf")):
    return build_fill_plan(tuple(fields), BANK, CONTACT, resume)


def _value(plan, selector):
    return next(p.value for p in plan.planned if p.field.selector == selector)


def _sources(plan):
    return {p.field.selector: p.source for p in plan.planned}


def test_contact_fields_map_from_career_facts():
    plan = _plan_for([
        _f("#full_name", FieldType.TEXT, "Full name"),
        _f("#email", FieldType.TEXT, "Email"),
        _f("#phone", FieldType.TEXT, "Phone"),
        _f("#linkedin", FieldType.TEXT, "LinkedIn URL"),
    ])
    assert _value(plan, "#full_name") == "Jordan Rivers"
    assert _value(plan, "#email") == "jordan@example.com"
    assert _value(plan, "#phone") == "+1 (555) 010-0100"
    assert _value(plan, "#linkedin") == "https://linkedin.com/in/jordan"
    assert _sources(plan)["#email"] == "career_facts.email"


def test_work_authorization_maps_to_select_options():
    plan = _plan_for([
        _f("#auth", FieldType.SELECT, "Are you legally authorized to work in the US?",
           options=["Yes", "No"]),
        _f("#spon", FieldType.SELECT, "Will you require visa sponsorship?", options=["Yes", "No"]),
    ])
    assert _value(plan, "#auth") == "Yes"     # authorized_us True
    assert _value(plan, "#spon") == "No"      # requires_sponsorship False


def test_salary_notice_start_experience_map():
    plan = _plan_for([
        _f("#salary", FieldType.TEXT, "Salary expectation"),
        _f("#notice", FieldType.TEXT, "Notice period"),
        _f("#start", FieldType.TEXT, "Earliest start date"),
        _f("#years", FieldType.TEXT, "Total years of experience"),
    ])
    assert _value(plan, "#salary") == "$160k"
    assert _value(plan, "#notice") == "2 weeks"
    assert _value(plan, "#start") == "2025-09-15"
    assert _value(plan, "#years") == "6"       # 6.0 formatted as "6"


def test_work_mode_matches_select_option():
    plan = _plan_for([
        _f("#mode", FieldType.SELECT, "Preferred work arrangement",
           options=["Remote", "Hybrid", "Onsite"]),
    ])
    assert _value(plan, "#mode") == "Remote"


def test_resume_file_field_uploads_tailored_pdf():
    plan = _plan_for([_f("#resume", FieldType.FILE, "Resume", required=True)])
    assert _value(plan, "#resume") == "/tmp/resume.pdf"
    assert _sources(plan)["#resume"] == "resume (tailored PDF)"


def test_missing_resume_is_recorded_not_guessed():
    plan = build_fill_plan((_f("#resume", FieldType.FILE, "Resume", required=True),),
                           BANK, CONTACT, resume_path=None)
    assert plan.planned == ()
    assert len(plan.unfilled) == 1
    assert "resume" in plan.unfilled[0].reason.lower()
    assert plan.missing_required()  # required + unfilled -> blocks approval


def test_credential_field_is_never_filled():
    plan = _plan_for([_f("#pw", FieldType.CREDENTIAL, "Password")])
    assert plan.planned == ()
    assert "never auto-filled" in plan.unfilled[0].reason


def test_eeo_declines_by_default_when_not_provided():
    plan = _plan_for([
        _f("#gender", FieldType.EEO, "Gender", options=["Decline to state", "Male", "Female"]),
    ])
    assert _value(plan, "#gender") == "Decline to state"


def test_eeo_uses_provided_value():
    bank = AnswerBank.model_validate({
        "authorized_us": True, "requires_sponsorship": False,
        "eeo": {"gender": "Female"},
    })
    plan = build_fill_plan(
        (_f("#gender", FieldType.EEO, "Gender", options=["Decline to state", "Male", "Female"]),),
        bank, CONTACT, None)
    assert _value(plan, "#gender") == "Female"


def test_prepared_answer_matches_textarea_question():
    plan = _plan_for([
        _f("#why", FieldType.TEXTAREA, "Why do you want to work at Demo Co?"),
    ])
    assert _value(plan, "#why") == "Because your data platform is great."


def test_unmatched_textarea_is_left_empty_with_reason():
    plan = _plan_for([
        _f("#essay", FieldType.TEXTAREA, "Describe a hard technical decision."),
    ])
    assert plan.planned == ()
    assert "no prepared answer" in plan.unfilled[0].reason


def test_unknown_field_is_recorded_not_guessed():
    plan = _plan_for([_f("#mystery", FieldType.TEXT, "Favorite color")])
    assert plan.planned == ()
    assert "no matching answer" in plan.unfilled[0].reason


def test_build_fill_plan_does_not_mutate_inputs():
    fields = (_f("#email", FieldType.TEXT, "Email"),)
    before = fields[0]
    build_fill_plan(fields, BANK, CONTACT, None)
    assert fields[0] is before  # frozen dataclass, untouched


# --- apply_plan drives a (fake) page ----------------------------------------

class _FakeLocator:
    def __init__(self, sink, selector):
        self.sink, self.selector = sink, selector

    @property
    def first(self):
        return self

    def fill(self, value):
        self.sink.append(("fill", self.selector, value))

    def select_option(self, value=None, label=None):
        self.sink.append(("select", self.selector, label or value))

    def set_input_files(self, path):
        self.sink.append(("upload", self.selector, path))

    def check(self):
        self.sink.append(("check", self.selector, True))


class _FakePage:
    def __init__(self):
        self.calls = []

    def locator(self, selector):
        return _FakeLocator(self.calls, selector)

    def get_by_label(self, label):
        return _FakeLocator(self.calls, f"label:{label}")


def test_apply_plan_only_touches_planned_fields():
    plan = _plan_for([
        _f("#email", FieldType.TEXT, "Email"),
        _f("#resume", FieldType.FILE, "Resume"),
        _f("#mode", FieldType.SELECT, "Work arrangement", options=["Remote", "Hybrid"]),
        _f("#mystery", FieldType.TEXT, "Favorite color"),  # unfilled -> untouched
    ])
    page = _FakePage()
    apply_plan(page, plan)
    touched = {sel for _, sel, _ in page.calls}
    assert "#email" in touched and "#resume" in touched and "#mode" in touched
    assert "#mystery" not in touched
    assert ("upload", "#resume", "/tmp/resume.pdf") in page.calls
