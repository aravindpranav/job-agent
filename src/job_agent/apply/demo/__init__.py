"""Committed, network-free demo assets for `job_agent apply --demo`.

  form.html          a local static application form (served via file://)
  demo_answers.yaml  a fake answer bank
  demo_resume.pdf    a fake resume to upload

Paired with the fake career facts in ``tailor/demo`` so the entire assisted-apply
flow — read, fill, review, simulated approval, submit — runs against a local page
with no real person and no employer.
"""

from __future__ import annotations

from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
FORM_HTML = DEMO_DIR / "form.html"
DEMO_ANSWERS = DEMO_DIR / "demo_answers.yaml"
DEMO_RESUME = DEMO_DIR / "demo_resume.pdf"
