"""Immutable career facts — the source of truth the tailoring engine obeys.

Loaded from ``career_facts.yaml`` (real: gitignored; demo: committed fake). The
frozen models make employers/titles/durations/certs read-only in memory, and the
helper accessors give ``verify.py`` the exact allow-lists it enforces.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


def _norm(text: str) -> str:
    """Case/whitespace-insensitive key for comparing fixed facts."""
    return " ".join(text.split()).strip().lower()


class Certification(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    issuer: str | None = None
    year: str | None = None


class Employer(BaseModel):
    model_config = ConfigDict(frozen=True)
    company: str
    title: str
    location: str = ""
    duration: str
    project_description: str = ""
    real_bullets: tuple[str, ...] = ()
    real_metrics: tuple[str, ...] = ()
    real_skills: tuple[str, ...] = ()


class CareerFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    role: str
    email: str
    phone: str
    location: str | None = None
    links: tuple[str, ...] = ()
    education: tuple[str, ...] = ()
    certifications: tuple[Certification, ...] = ()
    skills_inventory: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    employers: tuple[Employer, ...] = Field(min_length=1)

    # --- allow-lists consumed by the no-drift verifier ---------------------

    def employer_companies(self) -> set[str]:
        return {_norm(e.company) for e in self.employers}

    def employer_identities(self) -> set[tuple[str, str, str]]:
        """The (company, title, duration) triples that must survive unchanged."""
        return {(_norm(e.company), _norm(e.title), _norm(e.duration)) for e in self.employers}

    def certification_names(self) -> set[str]:
        return {_norm(c.name) for c in self.certifications}

    def real_skills(self) -> set[str]:
        """Every real skill (global inventory + per-employer environments)."""
        skills = {_norm(s) for group in self.skills_inventory.values() for s in group}
        for e in self.employers:
            skills |= {_norm(s) for s in e.real_skills}
        return skills


def load_career_facts(path: str | Path) -> CareerFacts:
    """Load and validate a career-facts YAML file with a readable error on failure."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Career facts not found: {path}. Generate it from the base resume first "
            f"(job_agent.tailor.extract)."
        )
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Could not parse {path} as YAML: {exc}") from exc
    try:
        return CareerFacts.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid career facts {path}:\n{exc}") from exc
