"""Command-line entry point: run a search and print a ranked table.

    python -m job_agent --demo      # no key, no network — bundled mock jobs
    python -m job_agent             # real run from search_profile.yaml
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from job_agent import search
from job_agent.config import load_profile, load_settings
from job_agent.demo_data import DemoSource, demo_profile, score_demo
from job_agent.models import ScoredJob
from job_agent.scoring import score_jobs
from job_agent.search import SearchOutcome
from job_agent.seen_cache import SeenCache

_VERDICT_STYLE = {
    "strong": "bold green",
    "possible": "yellow",
    "skip": "dim",
    "unscored": "red",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job_agent",
        description="Discover fresh ATS jobs and score their fit with an LLM.",
    )
    parser.add_argument("--demo", action="store_true",
                        help="Run against bundled mock jobs (no API key, no network).")
    parser.add_argument("--profile", default="search_profile.yaml",
                        help="Path to the search profile YAML (real runs only).")
    parser.add_argument("--max-age-hours", type=int, default=24,
                        help="Freshness window in hours (default: 24).")
    parser.add_argument("--method", choices=["structured", "tool"], default="structured",
                        help="LLM output method for scoring (default: structured).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only display the top N scored jobs.")
    return parser


def _print_pipeline_summary(console: Console, outcome: SearchOutcome, age_hours: int) -> None:
    c = outcome.counts
    console.print(
        f"[bold]Pipeline:[/bold] fetched {c.fetched} → keyword {c.after_keyword} "
        f"→ {age_hours}h {c.after_fresh} → location {c.after_location} → dedup {c.after_dedup}"
    )
    if outcome.per_source:
        per = ", ".join(f"{k}: {v}" for k, v in outcome.per_source.items())
        console.print(f"[dim]Sources: {per}[/dim]")
    for warning in outcome.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


def _print_ranked_table(console: Console, scored: list[ScoredJob], limit: int | None) -> None:
    ranked = sorted(scored, key=lambda s: s.sort_key, reverse=True)
    if limit is not None:
        ranked = ranked[:limit]

    if not ranked:
        console.print("[dim]No jobs to show. Nothing matched the last 24h + your filters.[/dim]")
        return

    table = Table(title="Ranked job matches", show_lines=False, header_style="bold")
    table.add_column("#", justify="right", width=3)
    table.add_column("Score", justify="right", width=5)
    table.add_column("Verdict", width=9)
    table.add_column("Title", overflow="fold", max_width=40)
    table.add_column("Company", max_width=18)
    table.add_column("Location", max_width=22)
    table.add_column("Src", width=13)

    for i, s in enumerate(ranked, 1):
        style = _VERDICT_STYLE.get(s.verdict, "")
        score_txt = "—" if s.score is None else str(s.score)
        title = f"[link={s.job.url}]{s.job.title}[/link]" if s.job.url else s.job.title
        table.add_row(
            str(i), score_txt, f"[{style}]{s.verdict}[/{style}]",
            title, s.job.company, s.job.location, s.job.source,
        )
    console.print(table)

    # Show why the top matches ranked well.
    for s in ranked:
        if s.verdict == "strong" and s.reasons:
            console.print(f"[green]★ {s.job.title} @ {s.job.company}[/green] — "
                          + "; ".join(s.reasons))


def run_demo(console: Console, args: argparse.Namespace) -> int:
    console.print("[bold cyan]job-agent — demo mode[/bold cyan] (mock data, no network, no key)\n")
    profile = demo_profile()
    now = datetime.now(timezone.utc)
    cache = SeenCache(Path(tempfile.gettempdir()) / "job_agent_demo_seen.json")
    outcome = search.run(
        profile,
        seen_cache=cache,
        now=now,
        fresh_window=timedelta(hours=args.max_age_hours),
        source_factory=lambda ats, board: DemoSource(board, now),
    )
    _print_pipeline_summary(console, outcome, args.max_age_hours)
    scored = score_demo(outcome.jobs, profile)
    _print_ranked_table(console, scored, args.limit)
    return 0


def run_real(console: Console, args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY is not set.[/red] Copy .env.example to "
                      ".env and add your key, or run with --demo.")
        return 2
    try:
        profile = load_profile(args.profile)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    console.print(f"[bold cyan]job-agent[/bold cyan] — scoring with {settings.model}\n")
    cache = SeenCache(settings.data_dir / "seen.json")
    outcome = search.run(profile, seen_cache=cache,
                         fresh_window=timedelta(hours=args.max_age_hours))
    _print_pipeline_summary(console, outcome, args.max_age_hours)
    if not outcome.jobs:
        _print_ranked_table(console, [], args.limit)
        return 0
    console.print(f"[dim]Scoring {len(outcome.jobs)} job(s) with the LLM…[/dim]")
    scored = score_jobs(outcome.jobs, settings, profile, method=args.method)
    _print_ranked_table(console, scored, args.limit)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    console = Console()
    try:
        return run_demo(console, args) if args.demo else run_real(console, args)
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
