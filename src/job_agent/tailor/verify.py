"""Verification gates.

1. No-drift gate (before rendering): the tailored output may not change/add/remove
   employers, titles, or durations; may not print a certification that isn't real;
   and may not state a concrete metric number that has no basis in the career
   facts. Any of these raises :class:`DriftError` — the PDF is NOT written.
2. PDF gate (after rendering): re-extract the PDF's text and assert the standard
   sections appear IN ORDER and the text is selectable. Failure raises
   :class:`PdfVerifyError` — a PDF that can't be parsed back is a failed build.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from job_agent.tailor.career_facts import CareerFacts
from job_agent.tailor.render_pdf import CANONICAL_HEADINGS
from job_agent.tailor.tailor import TailorResult


class DriftError(Exception):
    """The tailored output drifted from the immutable career facts."""


class PdfVerifyError(Exception):
    """The generated PDF failed text-extraction verification."""


def _norm(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def _numbers(text: str) -> set[str]:
    """All numeric tokens in a blob, comma-normalized (e.g. '1,000' -> '1000')."""
    return {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


# Metric-shaped numbers in output: a number followed by %, x, or a scale unit,
# not glued to letters (so "GPT-4"/"LLaMA 3" are ignored, "45%"/"20 years" caught).
_OUTPUT_METRIC = re.compile(
    r"(?<![A-Za-z-])(\d[\d,]*(?:\.\d+)?)\s*"
    r"(?:%|x\b|\+?\s*(?:users|customers|records|requests|rows|years|applications|"
    r"daily users|ms|seconds|minutes))",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"\[(?:ADD REAL METRIC|METRIC\b[^\]]*)\]", re.IGNORECASE)


class VerifyReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    output_employers: tuple[tuple[str, str, str], ...]
    placeholders: tuple[str, ...]
    placeholders_missing_from_notes: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def _output_employer_triples(resume_text: str) -> list[tuple[str, str, str]]:
    """Reconstruct (company, title, duration) blocks from the output resume."""
    triples, role, company = [], None, None
    for raw in resume_text.splitlines():
        line = raw.strip()
        if m := re.match(r"(?i)^Role:\s*(.+)$", line):
            role = m.group(1)
        elif m := re.match(r"(?i)^Company:\s*(.+)$", line):
            company = m.group(1)
        elif m := re.match(r"(?i)^Duration:\s*(.+)$", line):
            if role is not None and company is not None:
                triples.append((company, role, m.group(1)))
            role = company = None
    return triples


def _output_certifications(resume_text: str) -> list[str]:
    """Lines printed under the Certifications heading (the strict format's last
    section) — the contiguous block up to the first blank line or a NOTES marker."""
    lines = resume_text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines)
                     if _norm(ln).rstrip(":") == "certifications")
    except StopIteration:
        return []
    certs = []
    for ln in lines[start + 1:]:
        if not ln.strip() or _norm(ln).startswith("notes"):
            break  # end of the Certifications section
        s = ln.strip().lstrip("-•* ").strip()
        if _norm(s) in {"none", "n/a", "(none)"}:
            continue
        certs.append(s)
    return certs


def verify_no_drift(result: TailorResult, facts: CareerFacts) -> VerifyReport:
    """Raise :class:`DriftError` on any fabrication; else return a report."""
    problems: list[str] = []

    # 1. Employers: every output (company,title,duration) must exist unchanged.
    allowed_ids = facts.employer_identities()
    allowed_companies = facts.employer_companies()
    out_triples = _output_employer_triples(result.resume_text)
    for company, title, duration in out_triples:
        key = (_norm(company), _norm(title), _norm(duration))
        if _norm(company) not in allowed_companies:
            problems.append(f"Unknown employer in output: {company!r}")
        elif key not in allowed_ids:
            problems.append(
                f"Altered employer identity: {company} / {title} / {duration} "
                f"does not match the base resume."
            )

    # 2. Certifications: only real ones may be printed.
    allowed_certs = facts.certification_names()
    for cert in _output_certifications(result.resume_text):
        if not any(a in _norm(cert) or _norm(cert) in a for a in allowed_certs):
            problems.append(f"Uncredentialed certification printed on resume: {cert!r}")

    # 3. Metrics: every concrete metric number must be traceable to the facts.
    facts_blob = " ".join([
        facts.name, facts.role, " ".join(facts.education),
        *[b for e in facts.employers for b in e.real_bullets],
        *[m for e in facts.employers for m in e.real_metrics],
        *[e.project_description for e in facts.employers],
        *[s for g in facts.skills_inventory.values() for s in g],
    ])
    allowed_numbers = _numbers(facts_blob)
    for match in _OUTPUT_METRIC.finditer(result.resume_text):
        number = match.group(1).replace(",", "")
        if number not in allowed_numbers:
            problems.append(
                f"Invented metric number {match.group(0).strip()!r} has no basis in the "
                f"career facts."
            )

    if problems:
        raise DriftError("No-drift gate failed:\n  - " + "\n  - ".join(problems))

    # Not drift, but reported: placeholders and whether NOTES surfaces them.
    placeholders = tuple(sorted(set(_PLACEHOLDER.findall(result.resume_text))))
    notes_norm = _norm(result.notes)
    missing = tuple(p for p in placeholders if _norm(p) not in notes_norm)
    warnings = ()
    if missing:
        warnings = (f"{len(missing)} metric placeholder(s) not listed in NOTES.",)

    return VerifyReport(
        ok=True,
        output_employers=tuple(out_triples),
        placeholders=placeholders,
        placeholders_missing_from_notes=missing,
        warnings=warnings,
    )


def extract_pdf_text(pdf_path: str | Path) -> str:
    from pdfminer.high_level import extract_text
    return extract_text(str(pdf_path)) or ""


def verify_pdf(pdf_path: str | Path) -> list[str]:
    """Assert the PDF has selectable text and the standard sections IN ORDER.

    Returns the headings found (in order). Raises :class:`PdfVerifyError` on failure.
    """
    text = extract_pdf_text(pdf_path)
    if not text.strip():
        raise PdfVerifyError(f"{pdf_path}: no selectable text extracted (not ATS-parseable).")
    if "(cid:" in text:
        raise PdfVerifyError(f"{pdf_path}: contains glyphs with no Unicode mapping "
                             f"(unextractable by ATS). Use standard-encoded characters.")

    low = text.lower()
    positions = []
    for heading in CANONICAL_HEADINGS:
        idx = low.find(heading.lower())
        if idx < 0:
            raise PdfVerifyError(f"{pdf_path}: missing standard section '{heading}'.")
        positions.append(idx)
    if positions != sorted(positions):
        order = [h for _, h in sorted(zip(positions, CANONICAL_HEADINGS))]
        raise PdfVerifyError(
            f"{pdf_path}: sections out of order. Expected {CANONICAL_HEADINGS}, got {order}."
        )
    return list(CANONICAL_HEADINGS)
