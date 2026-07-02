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
from job_agent.store import load_job_record, save_search
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
from job_agent.tailor.tailor import TAILOR_MODEL, TailorResult, load_megaprompt, tailor_resume
from job_agent.tailor.verify import (
    DriftError,
    FormatError,
    PdfVerifyError,
    pdf_page_count,
    verify_format,
    verify_no_drift,
    verify_pdf,
)

SUBCOMMANDS = {"search", "tailor"}
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

def _print_pipeline_summary(console: Console, outcome: SearchOutcome, age_hours: int) -> None:
    c = outcome.counts
    console.print(
        f"[bold]Pipeline:[/bold] fetched {c.fetched} → keyword {c.after_keyword} "
        f"→ {age_hours}h {c.after_fresh} → location {c.after_location} → dedup {c.after_dedup}"
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


def cmd_search(console: Console, args: argparse.Namespace) -> int:
    window = timedelta(hours=args.max_age_hours)
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
    _print_ranked_table(console, scored, args.limit)
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


def _gate(facts, result) -> TailorResult:
    """Force the header and run the no-drift + format gates on the face.
    Returns the checked (face) result, or raises DriftError / FormatError."""
    face = trim_to_caps(clean_resume_text(
        normalize_header(result.resume_text, facts.name, facts.email, facts.phone)))
    checked = TailorResult(resume_text=face, notes=result.notes, raw=result.raw)
    verify_no_drift(checked, facts)
    verify_format(checked, facts)
    return checked


def _write(console: Console, checked: TailorResult, out_dir: Path, filename: str) -> int:
    """Render the PDF + docx, run the PDF gate, print NOTES + page count."""
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
    console.print(f"[dim]PDF engine: {engine}."
                  + (f" Trimmed {dropped} least-relevant bullet(s) to fit 2 pages." if dropped else ""))

    try:
        sections = verify_pdf(pdf_out)
    except PdfVerifyError as exc:
        console.print(f"[red]PDF verification failed:[/red] {exc}")
        return 1
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
            checked = _gate(facts, result)
        except (DriftError, FormatError) as exc:
            console.print(f"[red]Gate FAILED — refusing to write PDF:[/red]\n{exc}")
            return 1
        filename = _resume_filename(facts.name.split()[0], facts.role, "Demo")
        return _write(console, checked, out_dir, filename)

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

    # Tailor, then gate. On a format-cap overshoot, retry once with a correction.
    # (megaprompt=None so tailor_resume loads the base prompt + policy addendum.)
    checked = None
    for attempt in (1, 2):
        correction = None if attempt == 1 else _CAP_CORRECTION
        result = tailor_resume(facts, jd, settings=settings, extra_instruction=correction)
        try:
            checked = _gate(facts, result)
            break
        except DriftError as exc:  # fabrication — never retry, fail loudly
            console.print(f"[red]No-drift gate FAILED — refusing to write PDF:[/red]\n{exc}")
            return 1
        except FormatError as exc:
            if attempt == 2:
                console.print(f"[red]Format gate FAILED after retry — refusing to write PDF:[/red]\n{exc}")
                return 1
            console.print(f"[yellow]Format issue, retrying once:[/yellow] {exc}")
    return _write(console, checked, out_dir, filename)


# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job_agent",
                                     description="Discover, score, and tailor to jobs.")
    sub = parser.add_subparsers(dest="command")

    s = sub.add_parser("search", help="Discover fresh jobs and score their fit.")
    s.add_argument("--demo", action="store_true", help="Bundled mock jobs (no key/network).")
    s.add_argument("--profile", default="search_profile.yaml")
    s.add_argument("--max-age-hours", type=int, default=24, help="Freshness window (default 24).")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--method", choices=["structured", "tool"], default="structured")

    t = sub.add_parser("tailor", help="Tailor your resume to a searched job.")
    t.add_argument("--demo", action="store_true", help="Fake resume + JD end-to-end (no key).")
    t.add_argument("--job", help="Job id from a prior search (data/last_search.json).")
    t.add_argument("--facts", default="data/career_facts.yaml", help="Career facts YAML.")
    t.add_argument("--jd", help="Use this JD text file instead of re-fetching.")
    t.add_argument("--out-dir", default="data/output", help="Where to write the PDF/DOCX.")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Back-compat: no subcommand (or a leading flag) means `search`.
    if not argv or (argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["search"] + argv
    args = _build_parser().parse_args(argv)
    console = Console()
    try:
        return cmd_tailor(console, args) if args.command == "tailor" else cmd_search(console, args)
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
