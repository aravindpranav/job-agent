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
    """Remove em-dashes and separator lines the model may emit (safety net)."""
    text = text.replace(" — ", ", ").replace("—", ", ").replace("−", "-")
    text = re.sub(r"\s–\s", " - ", text).replace("–", "-")
    kept = [ln for ln in text.splitlines() if not _SEPARATOR.fullmatch(ln.strip())]
    out = "\n".join(kept)
    out = re.sub(r",\s*,", ",", out)
    return re.sub(r"[ \t]{2,}", " ", out)


def normalize_header(resume_text: str, name: str, email: str, phone: str) -> str:
    """Force the header to 'Name' / 'email | phone', dropping any model tagline."""
    lines = resume_text.splitlines()
    idx = next((i for i, ln in enumerate(lines)
                if _canonical_heading(strip_markdown(ln))), 0)
    body = "\n".join(lines[idx:]).lstrip("\n")
    return f"{name}\n{email} | {phone}\n\n{body}"


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("body", fontName=FONT, fontSize=10, leading=13.5)
    return {
        "name": ParagraphStyle("name", parent=base, fontName=FONT_BOLD, fontSize=17, spaceAfter=1),
        "contact": ParagraphStyle("contact", parent=base, fontSize=10, spaceAfter=4),
        "heading": ParagraphStyle("heading", parent=base, fontName=FONT_BOLD, fontSize=11.5,
                                  spaceBefore=9, spaceAfter=1),
        "body": base,
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=14, firstLineIndent=-9,
                                 spaceAfter=1.5),
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
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch, title="Resume",
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
