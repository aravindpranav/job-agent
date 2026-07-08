"""Configuration loading and validation.

Two things live here:

* :class:`SearchProfile` — the validated contents of ``search_profile.yaml``
  (roles, location rule, boards to fetch). Validated with Pydantic so a
  malformed profile fails fast with a clear message instead of deep in a fetch.
* :class:`Settings` — runtime bits from the environment (API key, model, paths).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

from job_agent.seniority import LEVEL_NAMES

# A low-cost, current Haiku-class model. Confirmed against the Anthropic docs
# rather than assumed. Overridable via the JOB_AGENT_MODEL env var.
DEFAULT_MODEL = "claude-haiku-4-5"

SourceName = str  # validated against the known set below


class SourceRef(BaseModel):
    """One board to fetch: which ATS, and the board/company identifier."""

    ats: str
    board: str

    model_config = {"extra": "forbid"}


class LocationRule(BaseModel):
    remote_ok: bool = True
    allowed_countries: list[str] = Field(default_factory=lambda: ["US"])

    model_config = {"extra": "forbid"}


class SearchProfile(BaseModel):
    """Validated ``search_profile.yaml``."""

    keywords: list[str] = Field(min_length=1)
    location: LocationRule = Field(default_factory=LocationRule)
    sources: list[SourceRef] = Field(min_length=1)
    candidate_summary: str = ""
    # Optional seniority ceiling: titles ranked above this are dropped before
    # scoring (see job_agent.seniority.LEVEL_NAMES). None = filter off.
    max_seniority: str | None = None
    # Optional candidate years of experience: a job whose JD requires clearly
    # more (see search.EXPERIENCE_GAP) is dropped before scoring. None = off.
    experience_years: int | None = Field(default=None, ge=0)

    model_config = {"extra": "forbid"}

    _KNOWN_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters",
                  # cross-company search sources: board = keyword query / tag
                  "sr-search", "remotive", "remoteok"}

    @field_validator("max_seniority")
    @classmethod
    def _known_level(cls, v: str | None) -> str | None:
        if v is None:
            return None
        level = v.strip().lower()
        if level not in LEVEL_NAMES:
            raise ValueError(
                f"max_seniority must be one of {sorted(LEVEL_NAMES)}; got {v!r}"
            )
        return level

    def unknown_sources(self) -> list[str]:
        """ATS names in the profile we don't have a source for."""
        return sorted({s.ats for s in self.sources if s.ats not in self._KNOWN_ATS})


class Settings(BaseModel):
    """Runtime settings from the environment."""

    anthropic_api_key: str | None = None
    model: str = DEFAULT_MODEL
    data_dir: Path = Path("data")


def load_settings() -> Settings:
    """Read ``.env`` (if present) and the environment into :class:`Settings`."""
    load_dotenv()  # loads .env from CWD if it exists; no-op otherwise
    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        model=os.environ.get("JOB_AGENT_MODEL") or DEFAULT_MODEL,
        data_dir=Path(os.environ.get("JOB_AGENT_DATA_DIR", "data")),
    )


def load_profile(path: str | Path) -> SearchProfile:
    """Load and validate a search profile YAML file.

    Raises :class:`FileNotFoundError` if missing and :class:`ValueError` with a
    readable message if the YAML is malformed or fails validation.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Search profile not found: {path}. "
            f"Copy search_profile.example.yaml to {path} and edit it."
        )
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Could not parse {path} as YAML: {exc}") from exc
    try:
        return SearchProfile.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid search profile {path}:\n{exc}") from exc
