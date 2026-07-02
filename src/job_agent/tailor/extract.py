"""One-time extraction: base resume (.docx) → immutable career facts (YAML).

This reads the base resume with python-docx and produces the source-of-truth
``career_facts.yaml`` that the tailoring engine is constrained to. Company names,
titles, and durations are captured verbatim from the resume; only the true
metric-bearing clauses are pulled into ``real_metrics`` (nothing is invented).

Contact fields and certifications are NOT in the resume text — they're supplied
explicitly (see ``build_career_facts``) so no PII is hardcoded in this module.

Run via the CLI (``python -m job_agent tailor extract ...``) or ``main()``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from docx import Document

# A bold header line like "JPMorgan Chase & Co. | Texas, USA| Title  Jul 2024 - Present".
_DURATION = re.compile(
    r"([A-Z][a-z]{2,8}\.?\s+\d{4}\s*[-–]\s*(?:Present|[A-Z][a-z]{2,8}\.?\s+\d{4}))\s*$"
)
_SECTION_MARKERS = ("OBJECTIVE", "PROFESSIONAL SUMMARY", "TECHNICAL SKILLS",
                    "EDUCATION", "WORK EXPERIENCE")

# Only clauses matching these are treated as real, citable metrics.
_METRIC = re.compile(
    r"\d{1,3}\s*%|\d[\d,]*\+?\s*(?:daily users|users|years|applications|records)"
    r"|minutes to seconds|tripl(?:e|ed)",
    re.IGNORECASE,
)


def _paragraphs(doc: Document) -> list[tuple[str, bool]]:
    """Non-empty paragraphs as (text, is_bold)."""
    out = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        runs = [r for r in p.runs if r.text.strip()]
        is_bold = bool(runs) and all(r.bold for r in runs)
        out.append((text, is_bold))
    return out


def _is_employer_header(text: str, is_bold: bool) -> bool:
    return is_bold and "|" in text and bool(_DURATION.search(text))


def _split_header(text: str) -> tuple[str, str, str, str]:
    """'Company | Location | Title  Duration' → (company, location, title, duration)."""
    duration = _DURATION.search(text).group(1).strip()
    without_dur = _DURATION.sub("", text).strip()
    parts = [p.strip() for p in without_dur.split("|")]
    company = parts[0]
    if company.endswith("'s") or company.endswith("’s"):
        company = company[:-2].strip()          # "Globe Life's" -> "Globe Life"
    location = parts[1] if len(parts) > 1 else ""
    title = " ".join(parts[2:]).strip() if len(parts) > 2 else ""
    return company, location, title, duration


def _paren_aware_split(text: str) -> list[str]:
    """Split a comma list without breaking parenthesized groups."""
    items, depth, buf = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            items.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        items.append(buf.strip())
    return [i for i in items if i]


def _metric_clauses(bullets: list[str]) -> list[str]:
    """The true, metric-bearing clauses from a role's bullets (no invention)."""
    found: list[str] = []
    for bullet in bullets:
        for clause in re.split(r"[;.]", bullet):
            clause = clause.strip()
            if clause and _METRIC.search(clause) and clause not in found:
                found.append(clause)
    return found


def extract_career_facts(docx_path: str | Path) -> dict[str, Any]:
    """Parse the resume into a career-facts dict (no contact PII, no certs)."""
    paras = _paragraphs(Document(str(docx_path)))
    texts = [t for t, _ in paras]

    name = texts[0] if texts else ""
    role = texts[1] if len(texts) > 1 else ""

    # Index the section markers so we can slice between them.
    marker_idx = {m: i for i, (t, _) in enumerate(paras)
                  for m in _SECTION_MARKERS if t.upper().startswith(m)}

    # Education: between EDUCATION and WORK EXPERIENCE.
    education = []
    if "EDUCATION" in marker_idx and "WORK EXPERIENCE" in marker_idx:
        for text, _ in paras[marker_idx["EDUCATION"] + 1: marker_idx["WORK EXPERIENCE"]]:
            education.append(text)

    # Technical skills: between TECHNICAL SKILLS and EDUCATION, grouped by bold header.
    skills_inventory: dict[str, list[str]] = {}
    if "TECHNICAL SKILLS" in marker_idx and "EDUCATION" in marker_idx:
        current = None
        for text, is_bold in paras[marker_idx["TECHNICAL SKILLS"] + 1: marker_idx["EDUCATION"]]:
            if is_bold:
                current = text
                skills_inventory[current] = []
            elif current:
                skills_inventory[current].append(text)

    # Work experience: split by employer headers.
    employers: list[dict[str, Any]] = []
    work = paras[marker_idx.get("WORK EXPERIENCE", len(paras)) + 1:]
    header_positions = [i for i, (t, b) in enumerate(work) if _is_employer_header(t, b)]
    for n, start in enumerate(header_positions):
        end = header_positions[n + 1] if n + 1 < len(header_positions) else len(work)
        block = work[start:end]
        company, location, title, duration = _split_header(block[0][0])

        # project description = paragraphs after header until "Key Responsibilities".
        body = block[1:]
        resp_at = next((i for i, (t, _) in enumerate(body)
                        if "key responsibilities" in t.lower()), len(body))
        env_at = next((i for i, (t, _) in enumerate(body)
                       if t.lower().startswith("environment")), len(body))
        project_description = " ".join(t for t, _ in body[:resp_at]).strip()
        bullets = [t for t, _ in body[resp_at + 1: env_at]
                   if "key responsibilities" not in t.lower()]
        environment_raw = body[env_at][0] if env_at < len(body) else ""
        environment_raw = re.sub(r"(?i)^environment:\s*", "", environment_raw).strip()

        employers.append({
            "company": company,
            "title": title,
            "location": location,
            "duration": duration,
            "project_description": project_description,
            "real_bullets": bullets,
            "real_metrics": _metric_clauses(bullets),
            "real_skills": _paren_aware_split(environment_raw),
        })

    return {
        "name": name,
        "role": role,
        "education": education,
        "skills_inventory": skills_inventory,
        "employers": employers,
    }


def build_career_facts(
    docx_path: str | Path,
    *,
    email: str,
    phone: str,
    location: str | None = None,
    links: list[str] | None = None,
    certifications: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Extraction + the explicitly-supplied contact/cert facts."""
    facts = extract_career_facts(docx_path)
    facts.update({
        "email": email,
        "phone": phone,
        "location": location,
        "links": links or [],
        "certifications": certifications or [],
    })
    # Order keys for a readable YAML file.
    order = ["name", "role", "email", "phone", "location", "links",
             "education", "certifications", "skills_inventory", "employers"]
    return {k: facts[k] for k in order}


def write_career_facts(facts: dict[str, Any], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(facts, sort_keys=False, allow_unicode=True, width=100))
    return out_path
