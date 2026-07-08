"""Field-mapping logic: known answers map correctly; unknowns are recorded."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.apply.answer_bank import AnswerBank, Contact
from job_agent.apply.fields import FieldType, FormField
from job_agent.apply.filler import apply_plan, build_fill_plan

CONTACT = Contact(
    name="Jordan Rivers", email="jordan@example.com", phone="+1 (555) 010-0100",
    location="Remote, US", linkedin="https://linkedin.com/in/jordan", github="https://github.com/jordan",
    employer="Acme Analytics", title="Data Engineer",
    school="State University", degree="B.S., Computer Science",
)
BANK = AnswerBank.model_validate({
    "authorized_us": True,
    "requires_sponsorship": False,
    "salary_expectation": "$160k",
    "work_mode": "remote",
    "willing_to_relocate": False,
    "location_preference": "Anywhere (US), remote preferred",
    "city": "Santa Clara",
    "state": "California",
    "zip": "95050",
    "country": "USA",
    "notice_period": "2 weeks",
    "earliest_start_date": "2025-09-15",
    "total_years_experience": 6,
    "linkedin": "https://linkedin.com/in/jordan",
    "eeo": {"hispanic_latino": "No", "race": "Asian"},
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


# --- structured location ------------------------------------------------------

def test_city_state_zip_map_to_structured_values_not_the_sentence():
    plan = _plan_for([
        _f("#city", FieldType.TEXT, "City"),
        _f("#state", FieldType.TEXT, "State"),
        _f("#zip", FieldType.TEXT, "Zip / Postal code"),
    ])
    assert _value(plan, "#city") == "Santa Clara"
    assert _value(plan, "#state") == "California"
    assert _value(plan, "#zip") == "95050"
    for p in plan.planned:   # the location_preference sentence appears nowhere
        assert "Anywhere" not in p.value


def test_country_select_matches_united_states_from_usa():
    plan = _plan_for([
        _f("#country", FieldType.SELECT, "Country",
           options=["Australia", "Belgium", "United States", "India"]),
        _f("#reside", FieldType.SELECT, "Where do you currently reside?",
           options=["United States of America", "Other"]),
    ])
    assert _value(plan, "#country") == "United States"
    assert _value(plan, "#reside") == "United States of America"
    assert _sources(plan)["#country"] == "answer_bank.country"


def test_country_text_field_gets_bank_value_verbatim():
    plan = _plan_for([_f("#c", FieldType.TEXT, "Country")])
    assert _value(plan, "#c") == "USA"


def test_empty_city_pauses_instead_of_using_the_sentence():
    bank = AnswerBank.model_validate({
        "authorized_us": True, "requires_sponsorship": False,
        "location_preference": "Anywhere (US), remote preferred",   # city empty
    })
    plan = build_fill_plan((_f("#city", FieldType.TEXT, "City", required=True),),
                           bank, CONTACT, None)
    assert plan.planned == ()           # never the sentence
    assert plan.missing_required()      # surfaced + blocks approval


def test_location_preference_still_maps_on_the_word_location():
    plan = _plan_for([_f("#loc", FieldType.TEXT, "Preferred work location")])
    assert _value(plan, "#loc") == "Anywhere (US), remote preferred"


def test_word_boundaries_keep_state_out_of_statement_and_city_out_of_ethnicity():
    plan = _plan_for([
        _f("#essay", FieldType.TEXTAREA, "Personal statement"),
        _f("#eth", FieldType.TEXT, "What is your ethnicity capacity limit?"),  # contrived
    ])
    assert plan.planned == ()   # neither gets a location value


# --- office-location options: pause, never city/work_mode --------------------

def test_office_checkboxes_never_get_city_or_work_mode():
    # The Plaid (Ashby) bug: office-preference checkboxes were filled with
    # city ("Santa Clara") via "City" and with work_mode via "Remote".
    plan = _plan_for([
        _f("#nyc", FieldType.CHECKBOX, "New York City Office"),
        _f("#sf", FieldType.CHECKBOX, "San Francisco HQ"),
    ])
    assert plan.planned == ()               # both pause
    for u in plan.unfilled:
        assert u.field.selector in ("#nyc", "#sf")


def test_grouped_office_field_pauses_too():
    plan = _plan_for([
        _f("#offices", FieldType.CHECKBOX, "Which office locations interest you?",
           options=["New York City Office", "San Francisco HQ", "Remote US"]),
    ])
    assert plan.planned == ()


def test_bare_remote_checkboxes_pause_never_multi_select():
    # Ungrouped "Remote US" / "Remote Canada" checkboxes: both previously
    # matched work_mode. Now both pause — nothing is selected at all.
    plan = _plan_for([
        _f("#rus", FieldType.CHECKBOX, "Remote US"),
        _f("#rca", FieldType.CHECKBOX, "Remote Canada"),
    ])
    assert plan.planned == ()


def test_grouped_location_picker_selects_only_the_us_option():
    plan = _plan_for([
        _f("#loc", FieldType.RADIO, "Remote or in-office arrangement",
           options=["Remote Canada", "Remote US", "Hybrid"]),
    ])
    assert _value(plan, "#loc") == "Remote US"    # exactly one, never Canada
    assert len(plan.planned) == 1


def test_only_non_us_remote_option_pauses():
    plan = _plan_for([
        _f("#loc", FieldType.SELECT, "Remote arrangement",
           options=["Remote Canada", "Onsite Toronto"]),
    ])
    assert plan.planned == ()               # never select a non-US location


def test_generic_remote_option_still_picked_when_unambiguous():
    plan = _plan_for([
        _f("#mode", FieldType.SELECT, "Preferred work arrangement",
           options=["Remote", "Hybrid", "Onsite"]),
    ])
    assert _value(plan, "#mode") == "Remote"


# --- Ashby name field ---------------------------------------------------------

def test_ashby_systemfield_name_maps_to_full_name():
    # label "Name" + name="_systemfield_name" — the exact-string check missed it.
    plan = _plan_for([
        _f('input[name="_systemfield_name"]', FieldType.TEXT, "Name",
           name="_systemfield_name", required=True),
        _f("#email", FieldType.TEXT, "Email"),
    ])
    assert _value(plan, 'input[name="_systemfield_name"]') == "Jordan Rivers"
    assert _value(plan, "#email") == "jordan@example.com"


def test_other_name_fields_do_not_get_the_full_name():
    plan = _plan_for([
        _f("#co", FieldType.TEXT, "Company name"),
        _f("#un", FieldType.TEXT, "Username"),
    ])
    assert all(p.field.selector not in ("#co", "#un") or p.value != "Jordan Rivers"
               for p in plan.planned)


# --- legal consent guard: NEVER auto-filled ----------------------------------

def test_privacy_notice_consent_is_never_filled_with_notice_period():
    # The reported bug: this label contains the word "notice" and got "2 weeks".
    plan = _plan_for([
        _f("#consent", FieldType.CHECKBOX,
           "Please confirm receipt of the Global Data Privacy Notice and "
           "US Arbitration Agreement", required=True),
    ])
    assert plan.planned == ()                          # nothing auto-filled
    assert "consent" in plan.unfilled[0].reason
    assert plan.missing_required()                     # pauses/blocks approval


@pytest.mark.parametrize("label", [
    "I agree to the Terms of Service",
    "Do you consent to a background check?",
    "Acknowledgment of the candidate privacy policy",
    "Signature",
    "I certify that my answers are true",
    "GDPR data protection notice",
])
def test_consent_and_acknowledgment_fields_always_pause(label):
    plan = _plan_for([_f("#c", FieldType.CHECKBOX, label)])
    assert plan.planned == ()
    assert "never auto-filled" in plan.unfilled[0].reason


def test_consent_guard_beats_every_mapper_even_country_and_eeo():
    # Consent wording mixed with otherwise-mappable words still pauses.
    plan = _plan_for([
        _f("#c1", FieldType.SELECT, "I consent to work in the United States country terms",
           options=["Yes", "No"]),
        _f("#c2", FieldType.EEO, "Privacy consent for gender data", options=["Yes", "No"]),
    ])
    assert plan.planned == ()


def test_notice_period_still_maps_and_work_auth_not_blocked():
    plan = _plan_for([
        _f("#np", FieldType.TEXT, "Notice period"),
        _f("#auth", FieldType.SELECT, "Are you legally authorized to work in the US?",
           options=["Yes", "No"]),
    ])
    assert _value(plan, "#np") == "2 weeks"     # tightened matcher still works
    assert _value(plan, "#auth") == "Yes"       # "authorize" is not a consent marker


# --- employment history / education (from career facts) -------------------------

def test_recent_employer_and_title_map_from_career_facts():
    plan = _plan_for([
        _f("#emp", FieldType.TEXT, "Current or most recent employer"),
        _f("#title", FieldType.TEXT, "Most recent job title"),
    ])
    assert _value(plan, "#emp") == "Acme Analytics"
    assert _value(plan, "#title") == "Data Engineer"
    assert _sources(plan)["#emp"] == "career_facts.employer"


def test_school_and_degree_map_from_career_facts():
    plan = _plan_for([
        _f("#school", FieldType.TEXT, "School / University"),
        _f("#degree", FieldType.TEXT, "Degree"),
    ])
    assert _value(plan, "#school") == "State University"
    assert _value(plan, "#degree") == "B.S., Computer Science"


def test_bare_title_is_not_mapped_it_may_be_a_salutation():
    plan = _plan_for([_f("#t", FieldType.SELECT, "Title", options=["Mr", "Ms", "Dr"])])
    assert plan.planned == ()   # pause — a bare "Title" is ambiguous


def test_previously_employed_gets_no_not_the_company_name():
    plan = _plan_for([
        _f("#prev", FieldType.SELECT, "Have you previously been employed by Demo Co?",
           options=["Yes", "No"]),
    ])
    assert _value(plan, "#prev") == "No"
    assert _sources(plan)["#prev"] == "answer_bank.previously_employed_here"


def test_whatsapp_optin_defaults_no():
    plan = _plan_for([
        _f("#wa", FieldType.SELECT, "Would you like to receive updates via WhatsApp?",
           options=["Yes", "No"]),
    ])
    assert _value(plan, "#wa") == "No"


def test_high_school_question_does_not_get_the_university():
    plan = _plan_for([_f("#hs", FieldType.SELECT, "Did you graduate high school?",
                         options=["Yes", "No"])])
    assert plan.planned == ()   # pause — never the university name


def test_countries_anticipate_working_maps_to_country():
    plan = _plan_for([
        _f("#cw", FieldType.SELECT, "Which countries do you anticipate working in?",
           options=["Australia", "United States", "India"]),
    ])
    assert _value(plan, "#cw") == "United States"
    assert _sources(plan)["#cw"] == "answer_bank.country"


# --- Current/Last Company label variants (the Plaid/Ashby gap) -------------------

@pytest.mark.parametrize("label", [
    "Current/Last Company",
    "Current Employer",
    "Most recent company",
])
def test_current_last_company_variants_map_to_recent_employer(label):
    plan = _plan_for([_f("#co", FieldType.TEXT, label)])
    assert _value(plan, "#co") == "Acme Analytics"
    assert _sources(plan)["#co"] == "career_facts.employer"


def test_company_without_a_recency_cue_still_pauses():
    # A bare "Company name" is ambiguous (could ask about a referral's company).
    plan = _plan_for([_f("#co", FieldType.TEXT, "Company name")])
    assert plan.planned == ()


def test_empty_employer_in_facts_pauses_instead_of_guessing():
    contact = CONTACT.model_copy(update={"employer": ""})
    plan = build_fill_plan((_f("#co", FieldType.TEXT, "Current/Last Company"),),
                           BANK, contact, None)
    assert plan.planned == ()


# --- Yes/No button pairs (Ashby toggles) ------------------------------------------

def test_sponsorship_toggle_maps_to_the_no_button():
    plan = _plan_for([
        _f('[data-ja-toggle="1"]', FieldType.TOGGLE,
           "Do you now or will you in the future require sponsorship for "
           "employment visa status?", options=["Yes", "No"]),
    ])
    assert _value(plan, '[data-ja-toggle="1"]') == "No"    # requires_sponsorship False
    assert _sources(plan)['[data-ja-toggle="1"]'] == "answer_bank.requires_sponsorship"


def test_previously_employed_toggle_maps_to_the_no_button():
    plan = _plan_for([
        _f('[data-ja-toggle="2"]', FieldType.TOGGLE,
           "Have you previously been employed by Plaid?", options=["Yes", "No"]),
    ])
    assert _value(plan, '[data-ja-toggle="2"]') == "No"
    assert _sources(plan)['[data-ja-toggle="2"]'] == "answer_bank.previously_employed_here"


def test_unmatched_toggle_still_pauses():
    plan = _plan_for([
        _f('[data-ja-toggle="3"]', FieldType.TOGGLE,
           "Have you used our API before?", options=["Yes", "No"]),
    ])
    assert plan.planned == ()


def test_consent_toggle_still_pauses():
    plan = _plan_for([
        _f('[data-ja-toggle="4"]', FieldType.TOGGLE,
           "Do you consent to the privacy policy?", options=["Yes", "No"]),
    ])
    assert plan.planned == ()
    assert "never auto-filled" in plan.unfilled[0].reason


# --- Plaid human-input questions must KEEP pausing --------------------------------

def test_plaid_interest_checkboxes_and_ai_rating_still_pause():
    plan = _plan_for([
        _f("#why", FieldType.CHECKBOX, "Why are you interested in working at Plaid?",
           options=["The products", "The people", "The mission"]),
        _f("#rate", FieldType.RADIO, "How would you rate Plaid's AI products?",
           options=["1", "2", "3", "4", "5"]),
    ])
    assert plan.planned == ()               # both pause for the human
    assert {u.field.selector for u in plan.unfilled} == {"#why", "#rate"}


# --- top-level EEO keys + decline variants ---------------------------------------

def test_top_level_eeo_keys_are_accepted_not_extra_forbidden():
    bank = AnswerBank.model_validate({
        "authorized_us": True, "requires_sponsorship": False,
        "gender": "Male", "hispanic_latino": "No", "race": "Asian",
        "veteran_status": "I am not a protected veteran",
        "disability_status": "No, I do not have a disability",
        "discipline": "Software Engineering",
    })
    assert bank.gender == "Male" and bank.discipline == "Software Engineering"


def test_top_level_eeo_key_overrides_the_eeo_block():
    bank = AnswerBank.model_validate({
        "authorized_us": True, "requires_sponsorship": False,
        "gender": "Male",                       # top level
        "eeo": {"gender": "Non-binary"},        # block loses
    })
    assert bank.eeo_answer("gender") == "Male"
    assert bank.eeo_answer("race") == "Decline to state"   # absent everywhere


def test_eeo_resolves_deterministically_from_top_level_keys():
    bank = AnswerBank.model_validate({
        "authorized_us": True, "requires_sponsorship": False,
        "gender": "Male", "veteran_status": "I am not a protected veteran",
    })
    plan = build_fill_plan((
        _f("#g", FieldType.EEO, "Gender", options=["Male", "Female", "Decline To Self Identify"]),
        _f("#v", FieldType.EEO, "Are you a protected veteran?",
           options=["I am not a protected veteran", "I identify as one or more...",
                    "I don't wish to answer"]),
    ), bank, CONTACT, None)
    assert _value(plan, "#g") == "Male"
    assert _value(plan, "#v") == "I am not a protected veteran"


@pytest.mark.parametrize("options,expected", [
    (["Male", "Female", "Decline To Self Identify"], "Decline To Self Identify"),   # Greenhouse
    (["Male", "Female", "Prefer not to answer"], "Prefer not to answer"),           # Ashby
    (["Male", "Female", "Prefer not to say"], "Prefer not to say"),
    (["Male", "Female", "I don't wish to answer"], "I don't wish to answer"),
])
def test_absent_eeo_key_falls_back_to_the_forms_decline_variant(options, expected):
    bank = AnswerBank.model_validate({"authorized_us": True, "requires_sponsorship": False})
    plan = build_fill_plan((_f("#g", FieldType.EEO, "Gender", options=tuple(options)),),
                           bank, CONTACT, None)
    assert _value(plan, "#g") == expected


def test_disability_self_identification_label_variant():
    bank = AnswerBank.model_validate({
        "authorized_us": True, "requires_sponsorship": False,
        "disability_status": "No, I do not have a disability",
    })
    plan = build_fill_plan((
        _f("#d", FieldType.EEO, "Voluntary Self-Identification of Disability",
           options=["Yes, I have a disability", "No, I do not have a disability",
                    "I don't wish to answer"]),
    ), bank, CONTACT, None)
    assert _value(plan, "#d") == "No, I do not have a disability"


def test_discipline_maps_from_bank_and_pauses_when_absent():
    bank = AnswerBank.model_validate({
        "authorized_us": True, "requires_sponsorship": False,
        "discipline": "Software Engineering",
    })
    plan = build_fill_plan((
        _f("#disc", FieldType.SELECT, "Discipline",
           options=["Software Engineering", "Data Science", "Design"]),
    ), bank, CONTACT, None)
    assert _value(plan, "#disc") == "Software Engineering"
    empty = AnswerBank.model_validate({"authorized_us": True, "requires_sponsorship": False})
    plan2 = build_fill_plan((_f("#disc", FieldType.TEXT, "Discipline"),), empty, CONTACT, None)
    assert plan2.planned == ()          # absent -> pause, never guessed


def test_consent_still_pauses_regardless_of_eeo_keys():
    bank = AnswerBank.model_validate({
        "authorized_us": True, "requires_sponsorship": False, "gender": "Male",
    })
    plan = build_fill_plan((
        _f("#c", FieldType.CHECKBOX, "I consent to the privacy policy", required=True),
    ), bank, CONTACT, None)
    assert plan.planned == ()
    assert "consent" in plan.unfilled[0].reason


# --- Hispanic/Latino vs race ---------------------------------------------------

def test_hispanic_latino_maps_to_hispanic_latino_not_race():
    plan = _plan_for([
        _f("#hisp", FieldType.EEO, "Ethnicity: Are you Hispanic or Latino?",
           options=["Yes", "No", "Decline to state"]),
        _f("#race", FieldType.EEO, "Race",
           options=["Asian", "White", "Decline to state"]),
    ])
    assert _value(plan, "#hisp") == "No"       # eeo.hispanic_latino
    assert _value(plan, "#race") == "Asian"    # eeo.race, untouched
    assert _sources(plan)["#hisp"] == "answer_bank.eeo.hispanic_latino"


def test_hispanic_latino_declines_when_not_provided():
    bank = AnswerBank.model_validate({"authorized_us": True, "requires_sponsorship": False})
    plan = build_fill_plan(
        (_f("#hisp", FieldType.EEO, "Are you Hispanic/Latino?",
            options=["Yes", "No", "Decline to state"]),),
        bank, CONTACT, None)
    assert _value(plan, "#hisp") == "Decline to state"


# --- ARIA combobox (React selects) ---------------------------------------------

def test_combobox_country_resolves_from_scanned_options():
    plan = _plan_for([
        _f("#country", FieldType.COMBOBOX, "Country",
           options=["Australia", "United States"]),
    ])
    assert _value(plan, "#country") == "United States"


def test_combobox_with_no_scanned_options_plans_the_bank_value():
    # Popup options render only on open — plan the bank value; the click path
    # selects it only if a matching option actually appears.
    plan = _plan_for([_f("#country", FieldType.COMBOBOX, "Country")])
    assert _value(plan, "#country") == "USA"


def test_unmatched_combobox_still_pauses():
    plan = _plan_for([_f("#mystery", FieldType.COMBOBOX, "Favorite dinosaur")])
    assert plan.planned == ()          # pause-don't-guess unchanged


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

    def click(self):
        self.sink.append(("click", self.selector, None))

    def get_by_role(self, role, name=None, exact=False):
        sink, selector = self.sink, self.selector

        class _Btn:
            @property
            def first(self):
                return self

            def click(self, timeout=None):
                sink.append((f"{role}-click", selector, name))
        return _Btn()


class _FakePage:
    def __init__(self):
        self.calls = []

    def locator(self, selector):
        return _FakeLocator(self.calls, selector)

    def get_by_label(self, label):
        return _FakeLocator(self.calls, f"label:{label}")


class _ComboPage(_FakePage):
    """Fake page with role=option lookup for the combobox click path."""

    def __init__(self, option_names):
        super().__init__()
        self._options = option_names
        self.escape_pressed = False

        page = self

        class _KB:
            def press(self, key):
                if key == "Escape":
                    page.escape_pressed = True
        self.keyboard = _KB()

    def get_by_role(self, role, name=None):
        assert role == "option"
        matches = [o for o in self._options if name.search(o)]
        page, calls = self, self.calls

        class _Opt:
            @property
            def first(self):
                return self

            def click(self, timeout=None):
                if not matches:
                    raise TimeoutError("no matching option")
                calls.append(("option-click", matches[0], None))
        return _Opt()


def test_combobox_is_selected_by_clicking_its_option():
    from job_agent.apply.filler import click_select
    page = _ComboPage(["Australia", "United States", "India"])
    ok = click_select(page, page.locator("#country"), "United States")
    assert ok is True
    assert ("option-click", "United States", None) in page.calls
    assert page.escape_pressed is False


def test_combobox_with_no_matching_option_selects_nothing():
    # Never guess a near-miss option: close the popup, select nothing.
    from job_agent.apply.filler import click_select
    page = _ComboPage(["Australia", "Belgium"])
    ok = click_select(page, page.locator("#country"), "United States")
    assert ok is False
    assert not any(c[0] == "option-click" for c in page.calls)
    assert page.escape_pressed is True


def test_apply_plan_routes_combobox_through_click_select():
    plan = _plan_for([
        _f("#country", FieldType.COMBOBOX, "Country",
           options=["Australia", "United States"]),
    ])
    page = _ComboPage(["Australia", "United States"])
    apply_plan(page, plan)
    assert ("option-click", "United States", None) in page.calls
    assert not any(c[0] == "select" for c in page.calls)   # no native select_option


def test_apply_plan_clicks_the_matching_toggle_button():
    plan = _plan_for([
        _f('[data-ja-toggle="1"]', FieldType.TOGGLE,
           "Will you require visa sponsorship?", options=["Yes", "No"]),
    ])
    page = _FakePage()
    apply_plan(page, plan)
    assert ("button-click", '[data-ja-toggle="1"]', "No") in page.calls
    assert not any(c[0] == "fill" for c in page.calls)   # a toggle is never typed into


def test_a_vanished_toggle_never_aborts_the_run():
    # The real Plaid failure: Ashby re-rendered after resume upload, the toggle
    # locator timed out, and the WHOLE run crashed. A failed fill must be
    # returned (to pause on), never raised, and later fields still fill.
    plan = _plan_for([
        _f("#email", FieldType.TEXT, "Email"),
        _f('[data-ja-toggle="1"]', FieldType.TOGGLE,
           "Will you require visa sponsorship?", options=["Yes", "No"]),
        _f("#phone", FieldType.TEXT, "Phone"),
    ])
    page = _FakePage()

    class _Vanished:
        @property
        def first(self):
            return self

        def click(self, timeout=None):
            raise TimeoutError("locator timed out — element re-rendered away")

    real_locator = page.locator
    def locator(selector):
        loc = real_locator(selector)
        if "data-ja-toggle" in selector:
            loc.get_by_role = lambda role, name=None, exact=False: _Vanished()
        return loc
    page.locator = locator

    failed = apply_plan(page, plan)
    assert [pf.field.selector for pf in failed] == ['[data-ja-toggle="1"]']
    filled = {sel for kind, sel, _ in page.calls if kind == "fill"}
    assert {"#email", "#phone"} <= filled       # the run carried on past the failure


def test_apply_plan_returns_no_failures_when_all_fills_land():
    plan = _plan_for([_f("#email", FieldType.TEXT, "Email")])
    assert apply_plan(_FakePage(), plan) == ()


def test_demote_moves_a_planned_field_to_unfilled():
    plan = _plan_for([
        _f("#email", FieldType.TEXT, "Email"),
        _f('[data-ja-toggle="1"]', FieldType.TOGGLE,
           "Will you require visa sponsorship?", required=True, options=["Yes", "No"]),
    ])
    demoted = plan.demote('[data-ja-toggle="1"]', "element vanished at fill time")
    assert [p.field.selector for p in demoted.planned] == ["#email"]
    assert [u.field.selector for u in demoted.unfilled] == ['[data-ja-toggle="1"]']
    assert demoted.unfilled[0].reason == "element vanished at fill time"
    assert demoted.missing_required()           # required again -> blocks approval
    assert len(plan.planned) == 2               # original untouched (immutable)


def test_edits_since_returns_only_new_or_changed_fills():
    # After approval the runner must re-apply ONLY what the human edited:
    # re-clicking an already-selected Ashby Yes/No button DESELECTS it
    # (verified live on the Plaid form), so unchanged fills must not re-apply.
    plan = _plan_for([
        _f("#email", FieldType.TEXT, "Email"),
        _f('[data-field-path="abc"]', FieldType.TOGGLE,
           "Will you require visa sponsorship?", options=["Yes", "No"]),
    ])
    unchanged = plan.edits_since(plan)
    assert unchanged.planned == ()              # nothing edited -> nothing re-applied

    edited = plan.with_value("#email", "new@example.com")
    delta = edited.edits_since(plan)
    assert [(p.field.selector, p.value) for p in delta.planned] == \
        [("#email", "new@example.com")]         # only the edit, never the toggle


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
