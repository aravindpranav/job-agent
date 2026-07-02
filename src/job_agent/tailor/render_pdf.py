"""Render tailored resume text into an ATS-safe PDF (and an editable .docx).

ATS constraints enforced here: single column (one text frame, no tables / text
boxes / images / icons), a standard built-in font (Helvetica) with real
selectable text, the exact standard section headings, real bullet characters,
sane margins, black text on white. The output is verified by re-extracting its
text in verify.py — a PDF that fails extraction is a failed build.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

# The five standard headings, in the order ATS parsers expect them.
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


# Built-in Helvetica can't render some Unicode punctuation; map it to ASCII so the
# PDF keeps standard-font, selectable text. Only affects the rendered file, never
# the resume_text the no-drift gate inspects.
_ASCII_MAP = {
    "→": "->", "⟶": "->", "⇒": "=>", "←": "<-", "↔": "<->",
    "✓": "[x]", "✗": "[ ]", "★": "*", "☆": "*",
    "…": "...", " ": " ", "‘": "'", "’": "'",
    "“": '"', "”": '"',
}


def _sanitize(text: str) -> str:
    for uni, ascii_ in _ASCII_MAP.items():
        text = text.replace(uni, ascii_)
    return text


def _canonical_heading(line: str) -> str | None:
    return _HEADING_LOOKUP.get(line.strip().rstrip(":").strip().lower())


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=13.5)
    return {
        "name": ParagraphStyle("name", parent=base, fontName="Helvetica-Bold",
                               fontSize=16, spaceAfter=2),
        "contact": ParagraphStyle("contact", parent=base, fontSize=10, spaceAfter=6),
        "heading": ParagraphStyle("heading", parent=base, fontName="Helvetica-Bold",
                                  fontSize=12, spaceBefore=10, spaceAfter=3),
        "body": base,
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=14,
                                 firstLineIndent=-8, spaceAfter=1),
    }


def _parse_lines(resume_text: str):
    """Yield (kind, text) tuples describing each line for rendering."""
    meaningful = 0
    for raw in resume_text.splitlines():
        line = raw.strip()
        if not line:
            yield ("blank", "")
            continue
        meaningful += 1
        if meaningful == 1:
            yield ("name", line)
        elif meaningful == 2:
            yield ("contact", line)
        elif (heading := _canonical_heading(line)):
            yield ("heading", heading)
        elif line[0] in "-•*":
            yield ("bullet", line[1:].strip())
        elif (m := _SUBLABELS.match(line)):
            yield ("label", f"{m.group(1)}:\t{m.group(2)}")
        else:
            yield ("body", line)


def render_pdf(resume_text: str, out_path: str | Path) -> Path:
    """Write an ATS-safe single-column PDF. Returns the path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    flow = []
    for kind, text in _parse_lines(resume_text):
        text = _sanitize(text)
        if kind == "blank":
            flow.append(Spacer(1, 5))
        elif kind == "name":
            flow.append(Paragraph(escape(text), styles["name"]))
        elif kind == "contact":
            flow.append(Paragraph(escape(text), styles["contact"]))
        elif kind == "heading":
            flow.append(Paragraph(escape(text), styles["heading"]))
        elif kind == "bullet":
            flow.append(Paragraph("&bull;&nbsp;" + escape(text), styles["bullet"]))
        elif kind == "label":
            label, _, rest = text.partition("\t")
            flow.append(Paragraph(f"<b>{escape(label)}</b> {escape(rest)}", styles["body"]))
        else:
            flow.append(Paragraph(escape(text), styles["body"]))

    doc = SimpleDocTemplate(
        str(out_path), pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="Resume",
    )
    doc.build(flow)
    return out_path


def render_docx(resume_text: str, out_path: str | Path) -> Path:
    """Write an editable single-column .docx mirroring the PDF. Returns the path."""
    from docx import Document
    from docx.shared import Pt

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)

    for kind, text in _parse_lines(resume_text):
        if kind == "blank":
            continue
        if kind == "name":
            p = doc.add_paragraph()
            run = p.add_run(text)
            run.bold = True
            run.font.size = Pt(16)
        elif kind == "contact":
            doc.add_paragraph(text)
        elif kind == "heading":
            p = doc.add_paragraph()
            p.add_run(text).bold = True
            p.runs[0].font.size = Pt(12)
        elif kind == "bullet":
            doc.add_paragraph(f"• {text}")
        elif kind == "label":
            label, _, rest = text.partition("\t")
            p = doc.add_paragraph()
            p.add_run(label + " ").bold = True
            p.add_run(rest)
        else:
            doc.add_paragraph(text)

    doc.save(str(out_path))
    return out_path
