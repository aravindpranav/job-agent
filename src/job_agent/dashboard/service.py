"""Dashboard services: thin adapters over the SAME functions the CLI uses.

Nothing here reimplements pipeline logic. Search and tailor literally invoke
the CLI command functions with a recording console (same gates, same retries,
same files); the apply preview reuses the runner's building blocks
(``read_form`` → ``build_fill_plan`` → ``apply_drafts``) READ-ONLY — it never
fills the page and never submits. Real submission stays where it was: the
interactive ``job_agent apply --job <id> --submit`` flow with its human gate.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from rich.console import Console

from job_agent.apply.tracker import load_applications

_VERDICT_RANK = {"strong": 3, "possible": 2, "skip": 1, "unscored": 0}


# --- section 1: application pipeline -------------------------------------------------

def _needs_follow_up(record) -> bool:
    """True when the record's follow-up date has passed (ISO dates compare
    lexicographically, so no parsing is needed)."""
    from datetime import date

    return bool(record.follow_up) and record.follow_up <= date.today().isoformat()


def applications_view(log_path: str | Path) -> dict:
    """All tracked applications, newest first, plus summary counts."""
    records = sorted(load_applications(log_path), key=lambda r: r.date, reverse=True)
    counts = {"total": len(records)}
    for status in ("submitted", "paused", "failed"):
        counts[status] = sum(1 for r in records if r.status == status)
    counts["needs_follow_up"] = sum(1 for r in records if _needs_follow_up(r))
    return {
        "records": [{**r.model_dump(), "needs_follow_up": _needs_follow_up(r)}
                    for r in records],
        "counts": counts,
    }


# --- section 2: search + browse -------------------------------------------------------

def jobs_view(last_search_path: str | Path,
              tracker_path: str | Path | None = None) -> dict:
    """The latest saved search, ranked like the CLI table, with each job's
    tracked state (status / notes / follow-up) joined in by job id."""
    import json

    path = Path(last_search_path)
    if not path.exists():
        return {"generated_at": None, "jobs": []}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"generated_at": None, "jobs": []}
    jobs = list(data.get("jobs", {}).values())
    jobs.sort(key=lambda j: (_VERDICT_RANK.get(j.get("verdict"), 0),
                             j.get("score") if j.get("score") is not None else -1),
              reverse=True)
    tracked: dict[str, dict] = {}
    markers = {"ids": set(), "pairs": set()}
    if tracker_path is not None:
        from job_agent.apply.tracker import applied_markers

        for r in load_applications(tracker_path):     # later records win
            if r.job_id:
                tracked[r.job_id] = {**r.model_dump(),
                                     "needs_follow_up": _needs_follow_up(r)}
        markers = applied_markers(tracker_path)
    from job_agent.apply.tracker import is_already_applied

    for j in jobs:
        j["tracked"] = tracked.get(str(j.get("id")))
        j["already_applied"] = is_already_applied(
            markers, job_id=str(j.get("id")), company=j.get("company", ""),
            title=j.get("title", ""))
    return {"generated_at": data.get("generated_at"), "jobs": jobs}


def _run_cli(handler, ns: Namespace) -> dict:
    """Run one CLI command function with a recording console (same code path)."""
    console = Console(record=True, width=120)
    try:
        code = handler(console, ns)
    except Exception as exc:  # surfaced to the UI, never a bare 500
        return {"ok": False, "exit_code": 1,
                "output": console.export_text() + f"\nerror: {exc}"}
    return {"ok": code == 0, "exit_code": code, "output": console.export_text()}


def run_search_cli(profile_path: str | Path, days: int = 7) -> dict:
    """Run the real search+score pipeline via the CLI's own command function."""
    from job_agent.cli import cmd_search

    ns = Namespace(demo=False, profile=str(profile_path), days=days,
                   max_age_hours=None, limit=None, method="structured")
    return _run_cli(cmd_search, ns)


# --- section 3: tailor + apply preview -------------------------------------------------

def run_tailor_cli(job_id: str, *, data_dir: Path, facts_path: Path,
                   out_dir: Path) -> dict:
    """Tailor one job via the CLI's own command function; report the PDF path."""
    from job_agent.cli import _resume_filename, cmd_tailor
    from job_agent.store import load_job_record
    from job_agent.tailor.career_facts import load_career_facts

    ns = Namespace(demo=False, job=job_id, facts=str(facts_path), jd=None,
                   out_dir=str(out_dir))
    result = _run_cli(cmd_tailor, ns)
    if result["ok"]:
        record = load_job_record(data_dir / "last_search.json", job_id) or {}
        try:
            facts = load_career_facts(facts_path)
            filename = _resume_filename(facts.name.split()[0],
                                        record.get("title", ""), record.get("company", ""))
            pdf = Path(out_dir) / f"{filename}.pdf"
            if pdf.exists():
                result["pdf"] = str(pdf)
        except (FileNotFoundError, ValueError):
            pass
    return result


def resume_pdf_path(record: dict, *, facts_path: Path, out_dir: Path) -> Path | None:
    """The tailored PDF for this job, or None. NEVER escapes ``out_dir``:
    the filename is derived (slugged) from the stored record — no user path —
    and the resolved location is verified to sit inside the output directory."""
    from job_agent.cli import _resume_filename
    from job_agent.tailor.career_facts import load_career_facts

    try:
        facts = load_career_facts(facts_path)
    except (FileNotFoundError, ValueError):
        return None
    filename = _resume_filename(facts.name.split()[0],
                                record.get("title", ""), record.get("company", ""))
    out_dir = Path(out_dir).resolve()
    pdf = (out_dir / f"{filename}.pdf").resolve()
    if pdf.parent != out_dir or not pdf.exists():
        return None
    return pdf


def serialize_plan(plan) -> dict:
    """A FillPlan as JSON for the UI, tagged exactly like the CLI review."""
    from job_agent.apply.review import _unfilled_tag, source_tag

    def name_of(field) -> str:
        return field.label or field.name or field.selector

    return {
        "planned": [
            {"label": name_of(p.field), "selector": p.field.selector,
             "value": p.value, "source": p.source, "tag": source_tag(p.source),
             "required": p.field.required}
            for p in plan.planned
        ],
        "unfilled": [
            {"label": name_of(u.field), "selector": u.field.selector,
             "reason": u.reason, "tag": _unfilled_tag(u.reason),
             "required": u.field.required}
            for u in plan.unfilled
        ],
        "missing_required": len(plan.missing_required()),
    }


def build_apply_preview(record: dict, bank, contact, *, resume_path: Path | None,
                        drafter, browser_factory=None) -> dict:
    """Read the live form and return the tagged fill plan — WITHOUT filling it.

    Same building blocks as the runner (read_form → build_fill_plan →
    apply_drafts) and the same review tags the CLI shows. The page is only
    navigated and scanned; no value is entered and nothing is submitted — the
    returned ``submit_command`` is the human-gated CLI flow.
    """
    from job_agent.apply.browser import open_browser
    from job_agent.apply.filler import build_fill_plan
    from job_agent.apply.form_reader import read_form
    from job_agent.apply.screening import apply_drafts

    factory = browser_factory or open_browser
    with factory(headless=True) as page:
        page.goto(record.get("apply_url") or record.get("url"), wait_until="load")
        fields = read_form(page)

    plan = build_fill_plan(fields, bank, contact, resume_path)
    if drafter is not None:
        plan = apply_drafts(plan, drafter)

    return {
        "job": {"id": record.get("id"), "title": record.get("title"),
                "company": record.get("company")},
        **serialize_plan(plan),
        "submit_command": f"python -m job_agent apply --job {record.get('id')} --submit",
    }


def _assemble_apply_inputs(job_id: str, *, data_dir: Path, facts_path: Path,
                           out_dir: Path) -> dict:
    """The real inputs every apply flavour needs: record, bank, contact,
    tailored resume, and (with an API key) the grounded screening drafter."""
    from job_agent.apply.answer_bank import load_answer_bank, resolve_contact
    from job_agent.cli import _find_tailored_resume
    from job_agent.config import load_settings
    from job_agent.store import load_job_record
    from job_agent.tailor.career_facts import load_career_facts

    settings = load_settings()
    record = load_job_record(data_dir / "last_search.json", job_id)
    if record is None:
        raise KeyError(job_id)
    facts = load_career_facts(facts_path)
    bank = load_answer_bank(data_dir / "answer_bank.yaml")
    contact = resolve_contact(facts, bank)
    resume = _find_tailored_resume(None, Path(out_dir), facts, record)

    drafter = None
    if settings.anthropic_api_key:
        from job_agent.apply.screening import make_drafter, make_llm_generate
        drafter = make_drafter(
            make_llm_generate(settings), facts, bank,
            jd=record.get("description") or "", company=record.get("company", ""),
            cache_path=data_dir / "answers_cache.json")
    return {"record": record, "bank": bank, "contact": contact,
            "resume_path": resume, "drafter": drafter}


def run_apply_preview(job_id: str, *, data_dir: Path, facts_path: Path,
                      out_dir: Path) -> dict:
    """Assemble the real inputs (record, bank, facts, resume, drafter) and preview."""
    inputs = _assemble_apply_inputs(job_id, data_dir=data_dir,
                                    facts_path=facts_path, out_dir=out_dir)
    record = inputs.pop("record")
    return build_apply_preview(record, inputs.pop("bank"), inputs.pop("contact"),
                               **inputs)


def start_apply_session(job_id: str, *, data_dir: Path, facts_path: Path,
                        out_dir: Path):
    """Build a live, human-gated apply session (visible browser) for one job."""
    from job_agent.dashboard.apply_session import ApplySession

    inputs = _assemble_apply_inputs(job_id, data_dir=data_dir,
                                    facts_path=facts_path, out_dir=out_dir)
    return ApplySession(tracker_path=data_dir / "applications.json",
                        out_dir=data_dir / "apply", **inputs)
