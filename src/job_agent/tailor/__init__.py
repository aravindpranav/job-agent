"""Slice 2 — resume tailoring.

Pipeline: base resume (.docx) → immutable career facts → JD-tailored resume text
(Sonnet, constrained by the mega prompt) → ATS-safe PDF/.docx, gated by a
no-drift verifier. Personal data (resume, career facts, outputs) stays gitignored.
"""
