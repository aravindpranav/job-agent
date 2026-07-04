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
from job_agent.tailor.render_pdf import CANONICAL_HEADINGS, _canonical_heading
from job_agent.tailor.tailor import TailorResult
from job_agent.tailor.textnorm import norm as _norm
from job_agent.tailor.textnorm import strip_markdown


class DriftError(Exception):
    """The tailored output drifted from the immutable career facts."""


class ScopeDriftError(Exception):
    """Unbanked scale/scope qualifier on the face (e.g. "multi-terabyte").

    Deliberately NOT a DriftError subclass: hard fabrication (employers, certs,
    metric values) is never retried, while an unsupported scope qualifier is
    soft drift worth one stricter regeneration before failing loudly.
    """


class PdfVerifyError(Exception):
    """The generated PDF failed text-extraction verification."""


class FormatError(Exception):
    """The tailored output violates the required résumé format."""


_BRACKET = re.compile(r"\[[^\]]*\]")
_SEP_LINE = re.compile(r"^[\-_.=|*·•\s]+$")


def verify_format(result: TailorResult, facts: CareerFacts) -> None:
    """Enforce the clean-résumé rules on the face. Raises :class:`FormatError`."""
    face = result.resume_text
    problems: list[str] = []

    if brackets := _BRACKET.findall(face):
        problems.append(f"Placeholder/bracket on the résumé face: {brackets[:3]}")
    if "—" in face:
        problems.append("Em-dash present on the résumé face.")
    for ln in face.splitlines():
        if "|" in ln and "@" not in ln:  # pipe allowed only on the 'email | phone' line
            problems.append(f"Pipe/table character on a non-contact line: {ln.strip()[:50]!r}")
            break
    for ln in face.splitlines():
        s = strip_markdown(ln).strip()
        if s and _SEP_LINE.fullmatch(s):
            problems.append(f"Separator line present: {s[:20]!r}")
            break
    if facts.certifications and not _output_certifications(face):
        problems.append("Certifications section is empty/None but real certifications exist.")

    # Project Description must describe the PROJECT, not restate the company.
    for company, desc in _project_descriptions(face):
        dn = _norm(desc)
        if dn.startswith(_norm(company)) or re.search(r"\bis a leading\b|\bis a global\b", dn):
            problems.append(f"Project Description restates the company (not the project): {desc[:60]!r}")

    # Per-role bullet caps: most-recent role <=6 responsibilities, older roles <=4;
    # achievements <=3 per role.
    for i, counts in enumerate(_role_bullet_counts(face)):
        cap = 6 if i == 0 else 4
        if counts["resp"] > cap:
            problems.append(f"Role #{i + 1} has {counts['resp']} responsibility bullets (cap {cap}).")
        if counts["ach"] > 3:
            problems.append(f"Role #{i + 1} has {counts['ach']} achievement bullets (cap 3).")

    # Each banked metric appears ONCE on the face — no restating the same number
    # in both Responsibilities and Achievements.
    banked = _banked_metric_values(facts)
    seen: dict[float, int] = {}
    for match in _OUTPUT_METRIC.finditer(face):
        value = _to_value(match.group(1))
        if value is not None and value in banked:
            seen[value] = seen.get(value, 0) + 1
    for value, n in seen.items():
        if n > 1:
            problems.append(
                f"Banked metric {value:g} appears {n} times on the face; each metric "
                f"appears ONCE, in its single strongest placement.")

    if problems:
        raise FormatError("Format gate failed:\n  - " + "\n  - ".join(problems))


def _role_bullet_counts(resume_text: str) -> list[dict[str, int]]:
    """Count Responsibilities / Achievements bullets per role block, in order."""
    roles: list[dict[str, int]] = []
    current: dict[str, int] | None = None
    section: str | None = None
    for raw in resume_text.splitlines():
        line = strip_markdown(raw)
        if re.match(r"(?i)^Role:", line):
            current = {"resp": 0, "ach": 0}
            roles.append(current)
            section = None
        elif re.match(r"(?i)^Responsibilities:", line):
            section = "resp"
        elif re.match(r"(?i)^Achievements:", line):
            section = "ach"
        elif _canonical_heading(line):     # EDUCATION/CERTIFICATIONS ends experience
            section = None
        elif line[:1] in "-•*" and current is not None and section:
            current[section] += 1
    return roles


def _project_descriptions(resume_text: str) -> list[tuple[str, str]]:
    """(company, project-description) pairs from the output resume."""
    pairs, company = [], None
    for raw in resume_text.splitlines():
        line = strip_markdown(raw)
        if m := re.match(r"(?i)^Company:\s*(.+)$", line):
            company = m.group(1)
        elif m := re.match(r"(?i)^Project Description:\s*(.+)$", line):
            pairs.append((company or "", m.group(1)))
    return pairs


def pdf_page_count(pdf_path: str | Path) -> int:
    """Number of pages in the generated PDF."""
    from pdfminer.pdfpage import PDFPage
    with open(pdf_path, "rb") as fh:
        return sum(1 for _ in PDFPage.get_pages(fh))


# Metric numbers = a number (optionally with K/M/B magnitude and '+') that is
# immediately followed by % or an IMPACT unit — not glued to letters (so "GPT-4"
# / "LLaMA 3" are ignored). Tenure/scope units like "years" and "applications"
# are deliberately NOT metrics, so "4+ years" / "20 years" are never flagged.
_METRIC_UNIT = r"(?:%|x\b|\+?\s*(?:daily\s+)?(?:users|customers|records|queries|requests|rows|transactions))"
_OUTPUT_METRIC = re.compile(rf"(?<![A-Za-z-])(\d[\d,]*(?:\.\d+)?\+?[KkMmBb]?)\s*(?={_METRIC_UNIT})",
                            re.IGNORECASE)
_ANY_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?\+?[KkMmBb]?")
_MAGNITUDE = {"k": 1e3, "m": 1e6, "b": 1e9}
_PLACEHOLDER = re.compile(r"\[(?:ADD REAL METRIC|METRIC\b[^\]]*)\]", re.IGNORECASE)


def _to_value(token: str) -> float | None:
    """'1,000+' -> 1000.0, '500K' -> 500000.0, '99.9' -> 99.9. None if unparseable."""
    token = token.strip().replace(",", "").rstrip("+%").strip()
    if not token:
        return None
    mult = 1.0
    if token[-1].lower() in _MAGNITUDE:
        mult = _MAGNITUDE[token[-1].lower()]
        token = token[:-1]
    try:
        return round(float(token) * mult, 4)
    except ValueError:
        return None


# Scale/scope qualifiers that inflate a resume. Written dash-free because both
# sides are compared through _scope_norm (lowercase, dashes -> spaces).
_SCOPE_QUALIFIERS = (
    "multi terabyte", "terabyte", "petabyte", "exabyte",
    "billions", "trillions",
    "enterprise scale", "enterprise wide", "enterprise grade",
    "firm wide", "firmwide", "across the firm",
    "company wide", "companywide", "across the company",
    "org wide", "organization wide",
    "planet scale", "web scale", "internet scale", "global scale",
)


def _scope_norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("-", " ").replace("–", " "))


def _facts_corpus(facts: CareerFacts) -> str:
    """Every phrase career_facts literally contains — the licence pool for
    scope qualifiers."""
    parts: list[str] = [*facts.education, *(c.name for c in facts.certifications)]
    for group in facts.skills_inventory.values():
        parts.extend(group)
    for e in facts.employers:
        parts += [e.company, e.title, e.project_description,
                  *e.real_bullets, *e.real_metrics, *e.real_skills]
    return _scope_norm(" ".join(p for p in parts if p))


def unbanked_scope_words(resume_text: str, facts: CareerFacts) -> list[str]:
    """Scale/scope qualifiers on the face with no literal support in the facts."""
    face = _scope_norm(resume_text)
    corpus = _facts_corpus(facts)
    hits = [q for q in _SCOPE_QUALIFIERS if q in face and q not in corpus]
    # report only the longest form ("multi terabyte", not also "terabyte")
    return [q for q in hits if not any(q != other and q in other for other in hits)]


def _banked_metric_values(facts: CareerFacts) -> set[float]:
    """Every numeric value appearing in the employers' banked real_metrics."""
    values: set[float] = set()
    for e in facts.employers:
        for metric in e.real_metrics:
            for tok in _ANY_NUMBER.findall(metric):
                if (v := _to_value(tok)) is not None:
                    values.add(v)
    return values


class VerifyReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    output_employers: tuple[tuple[str, str, str], ...]
    placeholders: tuple[str, ...]
    placeholders_missing_from_notes: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def _output_employer_triples(resume_text: str) -> list[tuple[str, str, str]]:
    """Reconstruct (company, title, duration) blocks from the output resume.

    Markdown-aware: models wrap labels as ``**Company:** …``, so strip decoration
    before matching the label."""
    triples, role, company = [], None, None
    for raw in resume_text.splitlines():
        line = strip_markdown(raw)
        if m := re.match(r"(?i)^Role:\s*(.+)$", line):
            role = m.group(1)
        elif m := re.match(r"(?i)^Company:\s*(.+)$", line):
            company = m.group(1)
        elif m := re.match(r"(?i)^Duration:\s*(.+)$", line):
            if role is not None and company is not None:
                triples.append((company, role, m.group(1)))
            role = company = None
    return triples


def _employer_problem(company: str, title: str, duration: str, facts: CareerFacts) -> str | None:
    """Validate one output employer block against the fixed facts.

    The company field may carry extra decoration the model appended (e.g. an
    inline location), so a real employer name must be *contained* in it; the title
    must match exactly and the duration must match exactly (dates are fixed)."""
    c, t, d = _norm(company), _norm(title), _norm(duration)
    candidates = [e for e in facts.employers if _norm(e.company) in c]
    if not candidates:
        return f"Unknown employer in output: {company!r}"
    for e in candidates:
        if _norm(e.title) == t and _norm(e.duration) == d:
            return None
    return f"Altered employer identity: {company} / {title} / {duration}"


def _output_certifications(resume_text: str) -> list[str]:
    """Lines printed under the Certifications heading (the strict format's last
    section) — the contiguous block up to the first blank line or a NOTES marker."""
    lines = [strip_markdown(ln) for ln in resume_text.splitlines()]
    try:
        start = next(i for i, ln in enumerate(lines)
                     if _norm(ln).rstrip(":") == "certifications")
    except StopIteration:
        return []
    certs: list[str] = []
    started = False
    for ln in lines[start + 1:]:
        s = ln.strip()
        if not s:
            if started:
                break          # a blank line AFTER content ends the section
            continue           # skip blank lines between the heading and the certs
        if _norm(s).startswith("notes") or _canonical_heading(strip_markdown(s)):
            break
        cert = s.lstrip("-•* ").strip()
        if _norm(cert) in {"none", "n/a", "(none)"}:
            continue
        certs.append(cert)
        started = True
    return certs


def verify_no_drift(result: TailorResult, facts: CareerFacts) -> VerifyReport:
    """Raise :class:`DriftError` on any fabrication; else return a report."""
    problems: list[str] = []

    # 1. Employers: every output (company,title,duration) must exist unchanged.
    out_triples = _output_employer_triples(result.resume_text)
    if not out_triples:
        problems.append("Could not find any employer blocks in the output "
                        "(Role/Company/Duration) — cannot verify against the base resume.")
    for company, title, duration in out_triples:
        if problem := _employer_problem(company, title, duration, facts):
            problems.append(problem)

    # 2. Certifications: only real ones may be printed.
    allowed_certs = facts.certification_names()
    for cert in _output_certifications(result.resume_text):
        if not any(a in _norm(cert) or _norm(cert) in a for a in allowed_certs):
            problems.append(f"Uncredentialed certification printed on resume: {cert!r}")

    # 3. Metrics: every metric number on the face must be a BANKED real metric.
    #    ("Anything not listed stays out" — only the confirmed real_metrics count;
    #    a value present only in a source bullet is not a licence to cite it.)
    allowed_values = _banked_metric_values(facts)
    for match in _OUTPUT_METRIC.finditer(result.resume_text):
        value = _to_value(match.group(1))
        if value is not None and value not in allowed_values:
            problems.append(
                f"Metric {match.group(0).strip()!r} is not among the banked real metrics."
            )

    if problems:
        raise DriftError("No-drift gate failed:\n  - " + "\n  - ".join(problems))

    # 4. Scope qualifiers: "multi-terabyte" / "enterprise-scale" / "across the
    #    firm" style inflation must be literally supported by the career facts.
    #    Raised only when nothing above failed — hard fabrication stays DriftError.
    if scope := unbanked_scope_words(result.resume_text, facts):
        raise ScopeDriftError(
            "Scope-qualifier gate failed (not supported by career facts): "
            + ", ".join(repr(s) for s in scope)
            + ". Remove the qualifier or state the work without inflated scale.")

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
