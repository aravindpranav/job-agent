"""Render tailored resume text into a clean, ATS-safe PDF and matching .docx.

Format (mirrors a clean single-column reference resume):
- Header: Name on line 1; "email | phone" on line 2. No tagline.
- CAPS section headings (PROFESSIONAL SUMMARY, …) each under a thin rule.
- Technical Skills as "Category: value, value" lines (no tables/pipes).
- Experience blocks: Role/Company/Project Description/Duration, then bullets.
- Real "•" bullets that EXTRACT as text — achieved with an embedded Unicode font
  (DejaVu Sans, bundled) since reportlab's base-14 Helvetica can't map "•" for
  extraction. No em-dashes, no separator lines, black text on white.

The PDF's text is verified (verify.py) to be selectable with sections in order.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from job_agent.tailor.textnorm import strip_markdown

_ASSETS = Path(__file__).resolve().parent / "assets"
FONT, FONT_BOLD = "DejaVuSans", "DejaVuSans-Bold"
_FONTS_READY = False


def _ensure_fonts() -> None:
    global _FONTS_READY
    if not _FONTS_READY:
        pdfmetrics.registerFont(TTFont(FONT, str(_ASSETS / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(_ASSETS / "DejaVuSans-Bold.ttf")))
        # Register as a family so inline <b>…</b> maps to the embedded bold face
        # (renders bold AND still extracts as text).
        pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD,
                                      italic=FONT, boldItalic=FONT_BOLD)
        _FONTS_READY = True


# Sub-labels whose VALUE should be bold (company name, role title).
_BOLD_VALUE_LABELS = {"role", "company"}


CANONICAL_HEADINGS = [
    "Professional Summary",
    "Technical Skills",
    "Professional Experience",
    "Education",
    "Certifications",
]
_HEADING_LOOKUP = {h.lower(): h for h in CANONICAL_HEADINGS}
_SUBLABELS = re.compile(
    r"^(Role|Company|Project Description|Duration|Responsibilities|Achievements):\s*(.*)$",
    re.IGNORECASE,
)
_CATEGORY = re.compile(r"^([A-Za-z][A-Za-z0-9 &/+\-]{1,44}):\s+(\S.*)$")
_SEPARATOR = re.compile(r"^[\-_.=|*·•\s]+$")  # a line that is only rule/separator chars


def _canonical_heading(line: str) -> str | None:
    return _HEADING_LOOKUP.get(line.strip().rstrip(":").strip().lower())


def clean_resume_text(text: str) -> str:
    """Remove em-dashes and separator lines, and tighten blank-line padding the
    model adds between bullets (so the résumé doesn't run long)."""
    text = text.replace(" — ", ", ").replace("—", ", ").replace("−", "-")
    text = re.sub(r"\s–\s", " - ", text).replace("–", "-")
    lines = [ln for ln in text.splitlines() if not _SEPARATOR.fullmatch(ln.strip())]

    tight: list[str] = []
    for i, ln in enumerate(lines):
        if not ln.strip():
            prev = tight[-1].strip() if tight else ""
            nxt = next((s.strip() for s in lines[i + 1:] if s.strip()), "")
            if not prev:
                continue                                   # collapse consecutive/leading blanks
            if prev[:1] in "-•*" or nxt[:1] in "-•*":
                continue                                   # no blank line around bullets
        tight.append(ln)

    out = "\n".join(tight)
    out = re.sub(r",\s*,", ",", out)
    return re.sub(r"[ \t]{2,}", " ", out)


_RESP_FLOOR = 2  # never trim a role below this many responsibility bullets when fitting pages


def drop_last_responsibility(resume_text: str) -> str:
    """Drop one responsibility bullet — the last (least JD-relevant) one of the
    role that currently has the most — without touching achievements. Used to fit
    the résumé to two pages. Returns the text unchanged if nothing is droppable."""
    lines = resume_text.splitlines()
    roles: list[list[int]] = []
    role_idx, section = -1, None
    for i, raw in enumerate(lines):
        line = strip_markdown(raw)
        if re.match(r"(?i)^Role:", line):
            role_idx += 1
            roles.append([])
            section = None
        elif re.match(r"(?i)^Responsibilities:", line):
            section = "resp"
        elif re.match(r"(?i)^Achievements:", line) or _canonical_heading(line):
            section = None
        elif section == "resp" and raw.strip()[:1] in "-•*":
            roles[role_idx].append(i)

    candidates = [(len(idx), ri) for ri, idx in enumerate(roles) if len(idx) > _RESP_FLOOR]
    if not candidates:
        return resume_text
    _, ri = max(candidates)
    drop = roles[ri][-1]
    return "\n".join(ln for j, ln in enumerate(lines) if j != drop)


def trim_to_caps(resume_text: str) -> str:
    """Enforce per-role bullet caps by keeping the first N bullets of each section.

    The model is instructed to rank bullets strongest-first, so keeping the first N
    keeps the most JD-relevant ones. Caps: most-recent role 6 responsibilities,
    older roles 4; achievements 3 per role. Only drops bullets — never edits or
    reorders — so it can't affect the no-drift or banked-metric checks beyond
    removing surplus lines.
    """
    role_idx = -1
    section: str | None = None
    kept = 0
    cap = 0
    out: list[str] = []
    for raw in resume_text.splitlines():
        line = strip_markdown(raw)
        if re.match(r"(?i)^Role:", line):
            role_idx += 1
            section = None
        elif re.match(r"(?i)^Responsibilities:", line):
            section, kept, cap = "resp", 0, (6 if role_idx == 0 else 4)
        elif re.match(r"(?i)^Achievements:", line):
            section, kept, cap = "ach", 0, 3
        elif _canonical_heading(line):
            section = None
        elif section and raw.strip()[:1] in "-•*":
            if kept >= cap:
                continue  # drop surplus bullet
            kept += 1
        out.append(raw)
    return "\n".join(out)


def normalize_header(resume_text: str, name: str, email: str, phone: str) -> str:
    """Force the header to 'Name' / 'email | phone', dropping any model tagline."""
    lines = resume_text.splitlines()
    idx = next((i for i, ln in enumerate(lines)
                if _canonical_heading(strip_markdown(ln))), 0)
    body = "\n".join(lines[idx:]).lstrip("\n")
    return f"{name}\n{email} | {phone}\n\n{body}"


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("body", fontName=FONT, fontSize=9.5, leading=12)
    return {
        "name": ParagraphStyle("name", parent=base, fontName=FONT_BOLD, fontSize=15, spaceAfter=1),
        "contact": ParagraphStyle("contact", parent=base, fontSize=9.5, spaceAfter=3),
        "heading": ParagraphStyle("heading", parent=base, fontName=FONT_BOLD, fontSize=11,
                                  spaceBefore=6, spaceAfter=1),
        "body": base,
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=13, firstLineIndent=-9,
                                 spaceAfter=1),
    }


def _parse_lines(resume_text: str):
    """Yield (kind, payload) describing each line, tracking the current section."""
    meaningful = 0
    section = None
    for raw in resume_text.splitlines():
        line = strip_markdown(raw)
        if not line:
            yield ("blank", "")
            continue
        meaningful += 1
        if meaningful == 1:
            yield ("name", line)
        elif meaningful == 2:
            yield ("contact", line)
        elif (heading := _canonical_heading(line)):
            section = heading
            yield ("heading", heading.upper())
        elif line[0] in "-•*":
            yield ("bullet", line[1:].strip())
        elif (m := _SUBLABELS.match(line)):
            yield ("label", (m.group(1), m.group(2)))
        elif section == "Technical Skills" and (m := _CATEGORY.match(line)):
            yield ("category", (m.group(1), m.group(2)))
        else:
            yield ("body", line)


def render_pdf(resume_text: str, out_path: str | Path) -> Path:
    """Write the ATS-safe single-column PDF. Returns the path."""
    _ensure_fonts()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    flow = []
    for kind, payload in _parse_lines(resume_text):
        if kind == "blank":
            flow.append(Spacer(1, 4))
        elif kind == "name":
            flow.append(Paragraph(escape(payload), styles["name"]))
        elif kind == "contact":
            flow.append(Paragraph(escape(payload), styles["contact"]))
        elif kind == "heading":
            flow.append(Paragraph(escape(payload), styles["heading"]))
            flow.append(HRFlowable(width="100%", thickness=0.6, color=colors.grey,
                                   spaceBefore=1, spaceAfter=4))
        elif kind == "bullet":
            flow.append(Paragraph("• " + escape(payload), styles["bullet"]))
        elif kind == "label":
            label, rest = payload
            if label.lower() in _BOLD_VALUE_LABELS:  # bold company name / role title
                flow.append(Paragraph(f"<b>{escape(label)}: {escape(rest)}</b>", styles["body"]))
            else:
                flow.append(Paragraph(f"<b>{escape(label)}:</b> {escape(rest)}", styles["body"]))
        elif kind == "category":
            label, values = payload
            flow.append(Paragraph(f"<b>{escape(label)}:</b> {escape(values)}", styles["body"]))
        else:
            flow.append(Paragraph(escape(payload), styles["body"]))

    SimpleDocTemplate(
        str(out_path), pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch, title="Resume",
    ).build(flow)
    return out_path


def render_docx(resume_text: str, out_path: str | Path) -> Path:
    """Write an editable single-column .docx mirroring the PDF. Returns the path."""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)

    def heading(text: str) -> None:
        p = doc.add_paragraph()
        run = p.add_run(text)
        run.bold = True
        run.font.size = Pt(11.5)
        # thin rule = bottom border on the heading paragraph
        pPr = p._p.get_or_add_pPr()
        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "888888")):
            bottom.set(qn(k), v)
        borders.append(bottom)
        pPr.append(borders)

    for kind, payload in _parse_lines(resume_text):
        if kind == "blank":
            continue
        if kind == "name":
            p = doc.add_paragraph()
            r = p.add_run(payload); r.bold = True; r.font.size = Pt(17)
        elif kind == "contact":
            doc.add_paragraph(payload)
        elif kind == "heading":
            heading(payload)
        elif kind == "bullet":
            doc.add_paragraph(f"• {payload}")
        elif kind in ("label", "category"):
            label, rest = payload
            p = doc.add_paragraph()
            p.add_run(f"{label}: ").bold = True
            # bold the value too for company name / role title
            p.add_run(rest).bold = label.lower() in _BOLD_VALUE_LABELS
        else:
            doc.add_paragraph(payload)

    doc.save(str(out_path))
    return out_path
