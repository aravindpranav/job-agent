"""Local API for the Chrome extension: detected fields in, resolved values out.

The extension's content script runs the SAME form scan as the CLI's
``form_reader`` and posts the raw control descriptors here. This endpoint
classifies them (``fields_from_scan``) and resolves values with the EXISTING
``build_fill_plan`` — answer bank, career facts, EEO wording rules — so every
safety property of the CLI flow holds by construction:

  * consent/legal fields come back UNFILLED, tagged ``[PAUSED: consent]`` —
    there is no code path that puts a value on them;
  * credential fields likewise pause;
  * the endpoint only resolves values — filling happens in the user's own
    browser, and submission is always the human clicking the real button.

LOCAL ONLY: served by the dashboard app, which binds 127.0.0.1. CORS admits
chrome-extension:// origins only (pin one ID with ``JOB_AGENT_EXTENSION_ID``).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

MAX_FIELDS = 300          # a real application form is well under this
_TEXT_CAP = 2000          # labels/selectors from an untrusted page — cap them

#: CORS for the extension. A pinned ID (set ``JOB_AGENT_EXTENSION_ID`` after
#: loading unpacked) allows exactly that extension; otherwise any Chrome
#: extension origin is admitted — web pages NEVER are, which is what keeps a
#: malicious site from reading profile data off localhost.
_EXTENSION_ORIGIN_REGEX = r"^chrome-extension://[a-p]{32}$"


def cors_config() -> dict:
    """Keyword arguments for ``CORSMiddleware`` — extension origins only."""
    ext_id = os.environ.get("JOB_AGENT_EXTENSION_ID", "").strip()
    scope = ({"allow_origins": [f"chrome-extension://{ext_id}"]} if ext_id
             else {"allow_origin_regex": _EXTENSION_ORIGIN_REGEX})
    return {"allow_methods": ["GET", "POST"],
            "allow_headers": ["content-type"], **scope}


class DetectedControl(BaseModel):
    """One raw control descriptor, exactly as the shared scan JS emits it."""

    tag: str = Field(max_length=_TEXT_CAP)
    type: str = Field(default="", max_length=_TEXT_CAP)
    name: str = Field(default="", max_length=_TEXT_CAP)
    label: str = Field(default="", max_length=_TEXT_CAP)
    groupLabel: str = Field(default="", max_length=_TEXT_CAP)
    required: bool = False
    maxlength: int | str | None = None
    options: list[str] = Field(default_factory=list, max_length=200)
    selector: str = Field(default="", max_length=_TEXT_CAP)


class FillValuesRequest(BaseModel):
    fields: list[DetectedControl] = Field(min_length=1, max_length=MAX_FIELDS)
    # Optional page context. When present (and an API key is configured), the
    # EXISTING screening drafter runs over free-text questions — same
    # grounding, same needs-input honesty rules as the CLI/dashboard flows.
    company: str = Field(default="", max_length=200)
    jd: str = Field(default="", max_length=30_000)   # page text, for grounding


def _slug_tokens(text: str) -> list[str]:
    return [t for t in "".join(c if c.isalnum() else " " for c in text.lower()).split()
            if len(t) > 1]


def recommend_resume(out_dir: Path, company: str) -> dict | None:
    """The tailored PDF the human should attach: prefer a filename mentioning
    the company, else the most recently tailored one. None if nothing exists.
    (Browsers block extensions from setting file inputs — this is a HINT for a
    human click, never an upload.)"""
    pdfs = sorted(out_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdfs:
        return None
    tokens = _slug_tokens(company)
    match = next((p for p in pdfs
                  if tokens and all(t in p.name.lower() for t in tokens)), None)
    pick = match or pdfs[0]
    return {"name": pick.name, "path": str(pick.resolve()),
            "matched_company": match is not None}


def _with_extension_wording(payload: dict, by_selector: dict) -> dict:
    """Adapt the shared plan serialization for the extension's UI.

    The resolver's unfilled reasons speak CLI ("pass --resume") — in the
    extension a file field is the human's click by browser design, so those
    entries get a ``kind`` marker and plain wording. Everything else is
    untouched. Immutable: returns new dicts.
    """
    from job_agent.apply.fields import FieldType

    def adapt(entry: dict) -> dict:
        field = by_selector.get(entry.get("selector"))
        if field is not None and field.field_type == FieldType.FILE:
            return {**entry, "kind": "file",
                    "reason": "attach the file yourself on the page — browsers "
                              "don't let extensions do this part"}
        return entry

    return {**payload, "unfilled": [adapt(u) for u in payload["unfilled"]]}


class FillReport(BaseModel):
    """Counts only — no form values ever travel back through this channel."""

    job_id: str = Field(min_length=1, max_length=200)
    filled: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    missing_required: int = Field(default=0, ge=0)


def create_extension_router(*, data_dir: Path, facts_path: Path,
                            out_dir: Path,
                            pending_task: dict | None = None,
                            fill_reports: dict | None = None) -> APIRouter:
    """The extension's endpoints, bound to this app's data locations.

    ``pending_task`` / ``fill_reports`` are the app's shared in-memory stores
    for the real-browser apply path: /api/apply/open registers a task here, the
    extension pulls it (its existing pull model — it still only acts on the
    human's popup click) and reports fill counts back.
    """
    router = APIRouter(prefix="/api/extension")
    pending_task = pending_task if pending_task is not None else {}
    fill_reports = fill_reports if fill_reports is not None else {}

    @router.get("/ping")
    def ping() -> dict:
        return {"ok": True, "app": "job-agent"}

    @router.get("/pending-task")
    def get_pending_task() -> dict:
        return {"task": pending_task.get("task")}

    @router.post("/fill-report")
    def fill_report(req: FillReport) -> dict:
        from datetime import datetime, timezone

        fill_reports[req.job_id] = {**req.model_dump(),
                                    "received_at": datetime.now(timezone.utc).isoformat()}
        task = pending_task.get("task")
        if task and task.get("job_id") == req.job_id:
            pending_task["task"] = None       # one-shot: consumed once reported on
        return {"ok": True}

    @router.post("/fill-values")
    def fill_values(req: FillValuesRequest) -> dict:
        from job_agent.apply.answer_bank import load_answer_bank, resolve_contact
        from job_agent.apply.filler import build_fill_plan
        from job_agent.apply.form_reader import fields_from_scan
        from job_agent.config import load_settings
        from job_agent.dashboard.service import serialize_plan
        from job_agent.tailor.career_facts import load_career_facts

        try:
            facts = load_career_facts(facts_path)
            bank = load_answer_bank(data_dir / "answer_bank.yaml")
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        contact = resolve_contact(facts, bank)
        fields = fields_from_scan([c.model_dump() for c in req.fields])
        # resume_path=None: browsers block extensions from setting file
        # inputs, so a FILE field always pauses for the human — the response's
        # ``resume`` hint tells them which file to attach themselves.
        plan = build_fill_plan(fields, bank, contact, resume_path=None)

        # Grounded yes/no: factual possession questions career_facts settles
        # explicitly get a [GROUNDED] answer with the fact shown. The extension
        # treats the tag like a draft — reviewed and inserted by the human,
        # never auto-entered. EEO/consent/visa/salary/notice/free-text always
        # pause (see apply.grounded).
        from job_agent.apply.grounded import apply_grounded_answers

        plan = apply_grounded_answers(plan, facts)

        # Free-text drafting: the EXISTING screening drafter, page context as
        # the JD. Drafts come back tagged ([AI-DRAFT]/[NEEDS-INPUT]) — the
        # extension shows them for review and NEVER enters one unreviewed.
        # The popup needs to know WHY drafting didn't run, not just that it
        # didn't — report the state explicitly.
        settings = load_settings()
        if not settings.anthropic_api_key:
            drafting = {"enabled": False,
                        "reason": "no ANTHROPIC_API_KEY loaded in the backend — "
                                  "add it to .env and restart the dashboard"}
        elif not (req.company or req.jd):
            drafting = {"enabled": False,
                        "reason": "the page sent no company/JD context to ground a draft"}
        else:
            from job_agent.apply import screening

            drafter = screening.make_drafter(
                screening.make_llm_generate(settings), facts, bank,
                jd=req.jd, company=req.company,
                cache_path=data_dir / "answers_cache.json")
            plan = screening.apply_drafts(plan, drafter)
            drafting = {"enabled": True, "reason": ""}

        by_selector = {f.selector: f for f in fields}
        return {**_with_extension_wording(serialize_plan(plan), by_selector),
                "resume": recommend_resume(out_dir, req.company),
                "drafting": drafting}

    return router
