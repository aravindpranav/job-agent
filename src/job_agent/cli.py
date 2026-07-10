"""Command-line entry point.

    python -m job_agent search --demo     # Slice 1: discover & score (no key/network)
    python -m job_agent search            # real search (writes data/last_search.json)
    python -m job_agent tailor --demo     # Slice 2: tailor a fake resume, sample PDF
    python -m job_agent tailor --job <id> # tailor your resume to a searched job

For back-compat, a bare invocation with no subcommand defaults to `search`
(so `python -m job_agent --demo` still runs the search demo).
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from job_agent import search
from job_agent.config import load_profile, load_settings
from job_agent.demo_data import DemoSource, demo_profile, score_demo
from job_agent.models import Job, ScoredJob
from job_agent.scoring import score_jobs
from job_agent.search import SearchOutcome
from job_agent.seen_cache import SeenCache
from job_agent.store import load_job_record, resolve_apply_url, save_search
from job_agent.tailor.career_facts import load_career_facts
from job_agent.tailor.jd_fetch import get_full_jd
from job_agent.tailor.render_pdf import (
    clean_resume_text,
    docx_to_pdf,
    drop_last_responsibility,
    find_soffice,
    normalize_header,
    render_docx,
    render_pdf,
    trim_to_caps,
)
from job_agent.tailor.tailor import (
    TAILOR_MODEL,
    TailorResult,
    load_megaprompt,
    reorder_skills,
    tailor_resume,
)
from job_agent.tailor.verify import (
    DriftError,
    FormatError,
    PdfVerifyError,
    ScopeDriftError,
    pdf_page_count,
    verify_artifact,
    verify_format,
    verify_no_drift,
    verify_pdf,
)

SUBCOMMANDS = {"search", "tailor", "apply", "applications", "dashboard"}
DEMO_DIR = Path(__file__).resolve().parent / "tailor" / "demo"

_VERDICT_STYLE = {"strong": "bold green", "possible": "yellow", "skip": "dim", "unscored": "red"}


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60] or "resume"


def _resume_filename(first_name: str, role: str, company: str) -> str:
    """{first}_{role}_{company}: spaces -> underscores, punctuation stripped.
    The role is trimmed at the first comma/paren (e.g. drop ', CustomerLake (ML/LLM)')."""
    role_main = re.split(r"[,(]", role)[0]
    return "_".join(p for p in (_slug(first_name), _slug(role_main), _slug(company)) if p)


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #

def _window_label(hours: int) -> str:
    """A human recency-window label: whole days as '7d', otherwise '36h'."""
    return f"{hours // 24}d" if hours % 24 == 0 else f"{hours}h"


def _print_pipeline_summary(console: Console, outcome: SearchOutcome, age_hours: int) -> None:
    c = outcome.counts
    console.print(
        f"[bold]Pipeline:[/bold] fetched {c.fetched} → keyword {c.after_keyword} "
        f"→ recency({_window_label(age_hours)}) {c.after_fresh} → location {c.after_location} "
        f"→ seniority {c.after_seniority} → dedup {c.after_dedup} "
        f"→ experience {c.after_experience}"
    )
    if outcome.per_source:
        console.print("[dim]Sources: " + ", ".join(f"{k}: {v}" for k, v in outcome.per_source.items()) + "[/dim]")
    for warning in outcome.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


def _print_ranked_table(console: Console, scored: list[ScoredJob], limit: int | None) -> None:
    ranked = sorted(scored, key=lambda s: s.sort_key, reverse=True)
    if limit is not None:
        ranked = ranked[:limit]
    if not ranked:
        console.print("[dim]No jobs to show. Nothing matched the last 24h + your filters.[/dim]")
        return
    table = Table(title="Ranked job matches", header_style="bold")
    for col, kw in [("#", {"justify": "right", "width": 3}), ("Score", {"justify": "right", "width": 5}),
                    ("Verdict", {"width": 9}), ("ID", {"width": 12}), ("Title", {"max_width": 34}),
                    ("Company", {"max_width": 16}), ("Location", {"max_width": 18}), ("Src", {"width": 13})]:
        table.add_column(col, **kw)
    for i, s in enumerate(ranked, 1):
        style = _VERDICT_STYLE.get(s.verdict, "")
        title = f"[link={s.job.url}]{s.job.title}[/link]" if s.job.url else s.job.title
        table.add_row(str(i), "—" if s.score is None else str(s.score),
                      f"[{style}]{s.verdict}[/{style}]", str(s.job.id), title,
                      s.job.company, s.job.location, s.job.source)
    console.print(table)


def _split_applied(scored: list[ScoredJob], markers: dict) -> tuple[list[ScoredJob], list[ScoredJob]]:
    """(fresh, already_applied): jobs with an in-flight application split out so
    the table doesn't resurface roles you already applied to."""
    from job_agent.apply.tracker import is_already_applied

    fresh, hidden = [], []
    for s in scored:
        applied = is_already_applied(markers, job_id=s.job.id,
                                     company=s.job.company, title=s.job.title)
        (hidden if applied else fresh).append(s)
    return fresh, hidden


def cmd_search(console: Console, args: argparse.Namespace) -> int:
    # --days is the primary recency knob (default 7); --max-age-hours, when
    # given explicitly, overrides it for sub-day windows.
    hours = args.max_age_hours if args.max_age_hours is not None else args.days * 24
    window = timedelta(hours=hours)
    args.max_age_hours = hours  # downstream prints use the effective window
    if args.demo:
        console.print("[bold cyan]job-agent search — demo mode[/bold cyan] (mock data, no key)\n")
        profile = demo_profile()
        now = datetime.now(timezone.utc)
        cache = SeenCache(Path(tempfile.gettempdir()) / "job_agent_demo_seen.json")
        outcome = search.run(profile, seen_cache=cache, now=now, fresh_window=window,
                             source_factory=lambda ats, board: DemoSource(board, now))
        _print_pipeline_summary(console, outcome, args.max_age_hours)
        _print_ranked_table(console, score_demo(outcome.jobs, profile), args.limit)
        return 0

    settings = load_settings()
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set.[/red] Add it to .env or use --demo.")
        return 2
    try:
        profile = load_profile(args.profile)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    console.print(f"[bold cyan]job-agent search[/bold cyan] — scoring with {settings.model}\n")
    outcome = search.run(profile, seen_cache=SeenCache(settings.data_dir / "seen.json"),
                         fresh_window=window)
    _print_pipeline_summary(console, outcome, args.max_age_hours)
    if not outcome.jobs:
        _print_ranked_table(console, [], args.limit)
        return 0
    console.print(f"[dim]Scoring {len(outcome.jobs)} job(s) with the LLM…[/dim]")
    scored = score_jobs(outcome.jobs, settings, profile, method=args.method)
    saved = save_search(scored, outcome.boards, settings.data_dir / "last_search.json")
    # Hide roles with an in-flight application (everything is still SAVED above;
    # this is a render-level filter — --include-applied overrides it). getattr:
    # programmatic callers (the dashboard) build their own Namespace.
    shown, hidden = scored, []
    if not getattr(args, "include_applied", False):
        from job_agent.apply.tracker import applied_markers
        shown, hidden = _split_applied(
            scored, applied_markers(settings.data_dir / "applications.json"))
    _print_ranked_table(console, shown, args.limit)
    if hidden:
        console.print(f"[dim]Hid {len(hidden)} job(s) you already applied to "
                      f"({', '.join(s.job.company for s in hidden[:5])}"
                      f"{'…' if len(hidden) > 5 else ''}) — use --include-applied "
                      f"to show them.[/dim]")
    console.print(f"[dim]Saved {len(scored)} job(s) to {saved} (use `tailor --job <ID>`).[/dim]")
    return 0


# --------------------------------------------------------------------------- #
# tailor
# --------------------------------------------------------------------------- #

def _megaprompt() -> str:
    try:
        return load_megaprompt()
    except OSError:
        return "(mega prompt unavailable; using stub)"


# Appended to a retry when the first attempt overshoots the bullet caps.
_CAP_CORRECTION = (
    "Your previous draft exceeded the responsibility-bullet limits. Regenerate it and, under "
    "each 'Responsibilities:' heading, count the bullets: the most-recent (first) role must have "
    "at most 6, and every older role at most 4. Delete the least JD-relevant bullets to comply. "
    "Keep everything else (metrics, certifications, format) the same."
)


def _gate(facts, result, jd: str | None = None) -> TailorResult:
    """Force the header, reorder skills by JD emphasis, and run the no-drift +
    format gates on the face (incl. the JD-summary check when ``jd`` is given).
    Returns the checked (face) result, or raises DriftError / FormatError."""
    face = trim_to_caps(clean_resume_text(
        normalize_header(result.resume_text, facts.name, facts.email, facts.phone)))
    if jd:
        face = reorder_skills(face, jd)   # deterministic: JD-emphasized lines first
    checked = TailorResult(resume_text=face, notes=result.notes, raw=result.raw)
    verify_no_drift(checked, facts)
    verify_format(checked, facts, jd)
    return checked


def _write(console: Console, checked: TailorResult, out_dir: Path, filename: str,
           facts=None) -> int:
    """Render the PDF + docx, run the PDF gate, print NOTES + page count.

    With ``facts`` given, also runs the final-artifact scope check — an unbanked
    scale qualifier in the RENDERED text raises ScopeDriftError (caller may
    regenerate once)."""
    console.print("[green]✓ No-drift + format gates passed[/green]")
    face = checked.resume_text
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"{filename}.pdf"
    docx_path = out_dir / f"{filename}.docx"
    use_soffice = find_soffice() is not None
    engine = "LibreOffice (PDF matches .docx)" if use_soffice else "reportlab (LibreOffice not found)"

    def render(face_text: str) -> Path:
        """Write the .docx (source of truth) and produce the PDF from it."""
        render_docx(face_text, docx_path)
        if use_soffice and (pdf := docx_to_pdf(docx_path, out_dir)):
            return pdf
        render_pdf(face_text, pdf_path)  # fallback
        return pdf_path

    # Fit to 2 pages on the ACTUAL output engine: render, and while it runs long
    # drop the least-relevant responsibility bullet (never an achievement/metric).
    pdf_out = render(face)
    pages = pdf_page_count(pdf_out)
    dropped = 0
    for _ in range(30):
        if pages <= 2:
            break
        trimmed = drop_last_responsibility(face)
        if trimmed == face:
            break  # at the floor; can't trim further
        face, dropped = trimmed, dropped + 1
        pdf_out = render(face)
        pages = pdf_page_count(pdf_out)
    (out_dir / f"{filename}.face.txt").write_text(face)  # for offline re-rendering
    console.print(f"[dim]PDF engine: {engine}."
                  + (f" Trimmed {dropped} least-relevant bullet(s) to fit 2 pages." if dropped else ""))

    try:
        sections = verify_pdf(pdf_out)
    except PdfVerifyError as exc:
        console.print(f"[red]PDF verification failed:[/red] {exc}")
        return 1
    if facts is not None:
        verify_artifact(pdf_out, facts)   # ScopeDriftError propagates to the caller
    console.print(f"[green]✓ ATS check passed[/green] — sections in order: {', '.join(sections)} "
                  f"([bold]{pages} page{'s' if pages != 1 else ''}[/bold])")
    console.print(f"[bold]PDF:[/bold] {pdf_out}\n[bold]DOCX:[/bold] {docx_path}")
    console.print(Panel(checked.notes or "(no notes returned)",
                        title="NOTES — review before applying", border_style="yellow"))
    return 0


def cmd_tailor(console: Console, args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)

    if args.demo:
        console.print("[bold cyan]job-agent tailor — demo mode[/bold cyan] (fake resume + JD, no key)\n")
        facts = load_career_facts(DEMO_DIR / "demo_career_facts.yaml")
        jd = (DEMO_DIR / "demo_jd.txt").read_text()
        stub = (DEMO_DIR / "demo_response.txt").read_text()
        result = tailor_resume(facts, jd, megaprompt=_megaprompt(), stub_response=stub)
        try:
            checked = _gate(facts, result, jd)
        except (DriftError, ScopeDriftError, FormatError) as exc:
            console.print(f"[red]Gate FAILED — refusing to write PDF:[/red]\n{exc}")
            return 1
        filename = _resume_filename(facts.name.split()[0], facts.role, "Demo")
        return _write(console, checked, out_dir, filename, facts)

    # real run
    if not args.job:
        console.print("[red]Provide --job <id>[/red] (from a prior `search`) or use --demo.")
        return 2
    settings = load_settings()
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set.[/red] Add it to .env.")
        return 2
    try:
        facts = load_career_facts(args.facts)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    record = load_job_record(settings.data_dir / "last_search.json", args.job)
    if not record:
        console.print(f"[red]Job id {args.job!r} not found in data/last_search.json.[/red] "
                      "Run `search` first, then pass an ID from the table.")
        return 2
    job = Job.model_validate(record)

    if args.jd:
        jd = Path(args.jd).read_text()
    else:
        console.print(f"[dim]Re-fetching full JD for {job.company} — {job.title}…[/dim]")
        jd = get_full_jd(job.source, record.get("board", ""), job.id) or job.description
        if not jd:
            console.print("[yellow]warning:[/yellow] could not fetch a JD; tailoring on title only.")

    console.print(f"[bold cyan]job-agent tailor[/bold cyan] — tailoring with {TAILOR_MODEL}\n")
    filename = _resume_filename(facts.name.split()[0], job.title, job.company)

    # Tailor, then gate. Format issues and unsupported scope qualifiers get ONE
    # regeneration with the gate's message as a correction; hard fabrication
    # (DriftError) is never retried.
    # (megaprompt=None so tailor_resume loads the base prompt + policy addendum.)
    checked = None
    correction = None
    for attempt in (1, 2):
        result = tailor_resume(facts, jd, settings=settings, extra_instruction=correction)
        try:
            checked = _gate(facts, result, jd)
            break
        except DriftError as exc:  # fabrication — never retry, fail loudly
            console.print(f"[red]No-drift gate FAILED — refusing to write PDF:[/red]\n{exc}")
            return 1
        except (FormatError, ScopeDriftError) as exc:
            if attempt == 2:
                console.print(f"[red]Gate FAILED after retry — refusing to write PDF:[/red]\n{exc}")
                return 1
            console.print(f"[yellow]Gate issue, retrying once:[/yellow] {exc}")
            correction = (f"{_CAP_CORRECTION}\nYour previous draft was REJECTED by an "
                          f"automated check:\n{exc}\nRegenerate and fix exactly this.")
    try:
        return _write(console, checked, out_dir, filename, facts)
    except ScopeDriftError as exc:
        # An unbanked scale qualifier survived to the RENDERED artifact —
        # regenerate once with the violation as a correction, then give up loudly.
        console.print(f"[yellow]Artifact scope check failed, regenerating once:[/yellow] {exc}")
        result = tailor_resume(facts, jd, settings=settings,
                               extra_instruction=f"Your previous draft was REJECTED:\n{exc}\n"
                                                 "Remove the unsupported scale wording.")
        try:
            checked = _gate(facts, result, jd)
            return _write(console, checked, out_dir, filename, facts)
        except (DriftError, ScopeDriftError, FormatError) as exc2:
            console.print(f"[red]Gate FAILED after artifact retry — refusing to write PDF:[/red]\n{exc2}")
            return 1


# --------------------------------------------------------------------------- #
#  apply  (Slice 4 — assisted apply in a visible browser)
# --------------------------------------------------------------------------- #

def _find_tailored_resume(explicit: str | None, tailor_out: Path,
                          facts, record: dict) -> Path | None:
    """Locate the tailored PDF for this job, or None (review will then pause)."""
    if explicit:
        return Path(explicit)
    fname = _resume_filename(facts.name.split()[0], record.get("title", ""),
                             record.get("company", ""))
    candidate = tailor_out / f"{fname}.pdf"
    return candidate if candidate.exists() else None


def _print_apply_result(console: Console, result) -> None:
    color = {"submitted": "bold green", "dry_run": "yellow", "skipped": "dim"}.get(
        result.status, "white")
    console.print(f"\n[{color}]● {result.status.upper()}[/{color}] — {result.reason}")
    if result.screenshot:
        console.print(f"  screenshot: {result.screenshot}")
    console.print(f"  job: {result.job_label}")


def cmd_apply(console: Console, args: argparse.Namespace) -> int:
    from job_agent.apply.answer_bank import load_answer_bank, resolve_contact
    from job_agent.apply.browser import PlaywrightNotInstalled
    from job_agent.apply.demo_apply import run_demo
    from job_agent.apply.prompt_io import PromptIO
    from job_agent.apply.runner import ApplyConfig, run_apply

    io = PromptIO()  # plain input/print — the review text is not rich markup

    try:
        if args.demo:
            console.print("[bold]apply --demo[/bold] — local fake form, zero network, "
                          "no real employer.\n")
            result = run_demo(io, headless=not args.headed)
            _print_apply_result(console, result)
            return 0

        if not args.job:
            console.print("[red]apply needs --job <id> (from a prior search) or --demo.[/red]")
            return 2

        settings = load_settings()
        record = load_job_record(settings.data_dir / "last_search.json", args.job)
        if not record:
            console.print(f"[red]Job id {args.job!r} not found in data/last_search.json.[/red] "
                          "Run a search first.")
            return 1
        apply_url = resolve_apply_url(record)
        if not apply_url:
            console.print("[red]That job record has no apply/URL to open.[/red]")
            return 1

        facts = load_career_facts(args.facts)
        try:
            bank = load_answer_bank(args.answers)
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/red]")
            return 1
        contact = resolve_contact(facts, bank)
        resume = _find_tailored_resume(args.resume, Path(args.tailor_out), facts, record)
        if resume is None:
            console.print("[yellow]No tailored resume found[/yellow] — the resume upload will "
                          "be left empty and paused on. Pass --resume PATH or run `tailor` first.")

        if not args.submit:
            console.print("[yellow]DRY RUN[/yellow] — preview + fill only. Nothing is submitted. "
                          "Add [bold]--submit[/bold] to enable real submission (still gated by "
                          "your explicit approval).\n")

        # Screening-question drafting: grounded Haiku drafts, shown as
        # [AI-DRAFT] at the review gate. Off without an API key.
        drafter = None
        if settings.anthropic_api_key:
            from job_agent.apply.screening import make_drafter, make_llm_generate
            drafter = make_drafter(
                make_llm_generate(settings), facts, bank,
                jd=record.get("description") or "",
                company=record.get("company", ""),
                cache_path=settings.data_dir / "answers_cache.json",
            )
        else:
            console.print("[dim]No ANTHROPIC_API_KEY — screening questions will pause "
                          "instead of being AI-drafted.[/dim]")

        cfg = ApplyConfig(
            apply_url=apply_url, bank=bank, contact=contact, resume_path=resume,
            submit_flag=args.submit, headless=False, out_dir=Path(args.out_dir),
            job_label=f"{record.get('company', '?')} — {record.get('title', '?')}",
            drafter=drafter,
            answer_cache=settings.data_dir / "answers_cache.json",
            company=record.get("company", ""),
            job_id=record.get("id", ""),
            job_title=record.get("title", ""),
            source=record.get("source", ""),
            applications_log=settings.data_dir / "applications.json",
        )
        result = run_apply(cfg, io=io)
        _print_apply_result(console, result)
        return 0
    except PlaywrightNotInstalled as exc:
        console.print(f"[red]{exc}[/red]")
        return 1


# --------------------------------------------------------------------------- #
#  applications  (the gitignored apply-attempt log, as a table)
# --------------------------------------------------------------------------- #

def cmd_applications(console: Console, args: argparse.Namespace) -> int:
    from job_agent.apply.tracker import load_applications

    records = load_applications(Path(args.log))
    if not records:
        console.print("[dim]No applications logged yet — run `job_agent apply`.[/dim]")
        return 0
    table = Table(title=f"Applications ({len(records)})")
    for col in ("Date", "Company", "Role", "Status", "Source", "Job id"):
        table.add_column(col)
    status_style = {"submitted": "bold green", "paused": "yellow", "failed": "red",
                    "saved": "dim", "applied": "cyan", "interviewing": "magenta",
                    "offer": "bold green", "rejected": "red"}
    for r in sorted(records, key=lambda r: r.date, reverse=True):
        style = status_style.get(r.status, "white")
        table.add_row(r.date[:16].replace("T", " "), r.company, r.title,
                      f"[{style}]{r.status}[/{style}]", r.source, r.job_id)
    console.print(table)
    return 0


# --------------------------------------------------------------------------- #
#  dashboard  (local web UI over the same functions — 127.0.0.1 ONLY)
# --------------------------------------------------------------------------- #

def cmd_dashboard(console: Console, args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        console.print("[red]The dashboard needs fastapi + uvicorn:[/red] "
                      "pip install 'job-agent[dashboard]' (or pip install fastapi uvicorn)")
        return 1
    from job_agent.dashboard.app import create_app

    settings = load_settings()
    app = create_app(data_dir=settings.data_dir, profile_path=Path(args.profile))
    console.print(f"[bold cyan]job-agent dashboard[/bold cyan] — "
                  f"http://127.0.0.1:{args.port}  (local only; Ctrl-C to stop)")
    # 127.0.0.1 is deliberate and not configurable: this UI serves personal
    # data (application history, career facts) with no auth layer.
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job_agent",
                                     description="Discover, score, and tailor to jobs.")
    sub = parser.add_subparsers(dest="command")

    s = sub.add_parser("search", help="Discover fresh jobs and score their fit.")
    s.add_argument("--demo", action="store_true", help="Bundled mock jobs (no key/network).")
    s.add_argument("--profile", default="search_profile.yaml")
    s.add_argument("--days", type=int, default=7,
                   help="Recency window in days (default 7).")
    s.add_argument("--max-age-hours", type=int, default=None,
                   help="Recency window in hours; overrides --days when given.")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--method", choices=["structured", "tool"], default="structured")
    s.add_argument("--include-applied", action="store_true",
                   help="Also show jobs you already applied to (hidden by default).")

    t = sub.add_parser("tailor", help="Tailor your resume to a searched job.")
    t.add_argument("--demo", action="store_true", help="Fake resume + JD end-to-end (no key).")
    t.add_argument("--job", help="Job id from a prior search (data/last_search.json).")
    t.add_argument("--facts", default="data/career_facts.yaml", help="Career facts YAML.")
    t.add_argument("--jd", help="Use this JD text file instead of re-fetching.")
    t.add_argument("--out-dir", default="data/output", help="Where to write the PDF/DOCX.")

    a = sub.add_parser("apply", help="Assisted apply in a visible browser (human-gated).")
    a.add_argument("--demo", action="store_true",
                   help="Run the whole flow against a local fake form (no key/network).")
    a.add_argument("--job", help="Job id from a prior search (opens its apply URL).")
    a.add_argument("--submit", action="store_true",
                   help="Enable REAL submission (still gated by your in-session approval).")
    a.add_argument("--answers", default="data/answer_bank.yaml", help="Answer bank YAML.")
    a.add_argument("--facts", default="data/career_facts.yaml", help="Career facts YAML.")
    a.add_argument("--resume", default=None, help="Tailored resume PDF to upload.")
    a.add_argument("--tailor-out", default="data/output", dest="tailor_out",
                   help="Where tailored resumes are written (for auto-detect).")
    a.add_argument("--out-dir", default="data/apply", help="Where to write logs/screenshots.")
    a.add_argument("--headed", action="store_true",
                   help="Demo only: show the browser window (demo defaults to headless).")

    ap = sub.add_parser("applications", help="Show the log of every apply attempt.")
    ap.add_argument("--log", default="data/applications.json",
                    help="Path to the (gitignored) applications log.")

    d = sub.add_parser("dashboard", help="Local web dashboard (binds 127.0.0.1 only).")
    d.add_argument("--port", type=int, default=8642)
    d.add_argument("--profile", default="search_profile.yaml")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Back-compat: no subcommand (or a leading flag) means `search`.
    if not argv or (argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["search"] + argv
    args = _build_parser().parse_args(argv)
    console = Console()
    dispatch = {"tailor": cmd_tailor, "apply": cmd_apply,
                "applications": cmd_applications, "dashboard": cmd_dashboard}
    handler = dispatch.get(args.command, cmd_search)
    try:
        return handler(console, args)
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
