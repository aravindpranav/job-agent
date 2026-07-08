"""SearchProfile validation for the seniority / experience knobs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_agent.config import SearchProfile, SourceRef


def _profile(**overrides) -> SearchProfile:
    data = dict(keywords=["data engineer"], sources=[SourceRef(ats="greenhouse", board="b")])
    data.update(overrides)
    return SearchProfile.model_validate(data)


def test_cross_company_search_ats_names_are_known():
    prof = _profile(sources=[
        SourceRef(ats="sr-search", board="machine learning engineer"),
        SourceRef(ats="remotive", board="machine learning"),
        SourceRef(ats="remoteok", board="machine-learning"),
        SourceRef(ats="greenhouse", board="stripe"),
    ])
    assert prof.unknown_sources() == []     # none warned about / skipped


def test_seniority_and_experience_default_to_off():
    prof = _profile()
    assert prof.max_seniority is None
    assert prof.experience_years is None


def test_max_seniority_accepts_known_level_case_insensitively():
    assert _profile(max_seniority="Senior").max_seniority == "senior"
    assert _profile(max_seniority="staff").max_seniority == "staff"


def test_max_seniority_rejects_unknown_level():
    with pytest.raises(ValidationError, match="max_seniority"):
        _profile(max_seniority="wizard")


def test_negative_experience_years_is_rejected():
    with pytest.raises(ValidationError):
        _profile(experience_years=-2)
