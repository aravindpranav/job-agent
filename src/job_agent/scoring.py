"""LLM fit scoring.

Turns each surviving :class:`Job` into a :class:`ScoredJob` using a low-cost
Haiku-class model. The public entry point is :func:`score_jobs`; the LLM call is
isolated behind :func:`score_one` so it can be swapped or mocked.

Reliability: the model is asked for strict JSON matching a fixed schema. We parse
defensively — on malformed output we retry once, and if it still fails the job is
marked ``verdict="unscored"`` rather than crashing the run.

NOTE (verification gate): the exact request shape (structured `output_config.format`
vs. tool-use) is confirmed by a one-off smoke test before this module is relied
on — see scripts/verify note in the README. Both request shapes are implemented
here; ``method`` selects which, defaulting to the verified path.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from job_agent.config import SearchProfile, Settings
from job_agent.models import Job, ScoredJob

# The fixed output contract. Structured outputs disallow numeric min/max, so the
# 0-100 range is validated/clamped client-side.
SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["strong", "possible", "skip"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "missing_requirements": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "verdict", "reasons", "missing_requirements"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a precise technical recruiter. Given a candidate summary and a job "
    "posting, judge how well the job fits the candidate. Be honest and specific. "
    "Respond ONLY with the requested JSON — no prose."
)

Method = Literal["structured", "tool"]


def build_user_prompt(job: Job, profile: SearchProfile) -> str:
    """The per-job scoring prompt (Prompt A)."""
    return (
        f"CANDIDATE:\n{profile.candidate_summary.strip()}\n\n"
        f"JOB:\n"
        f"- Title: {job.title}\n"
        f"- Company: {job.company}\n"
        f"- Location: {job.location} (remote={job.remote}, country={job.country})\n"
        f"- Description: {job.description or '(no description available)'}\n\n"
        "Return JSON with: score (integer 0-100), verdict "
        "(strong|possible|skip), reasons (short strings), and "
        "missing_requirements (short strings)."
    )


def _coerce(job: Job, data: dict[str, Any]) -> ScoredJob:
    """Validate/clamp raw model output into a ScoredJob."""
    score = data.get("score")
    if isinstance(score, bool) or not isinstance(score, int):
        raise ValueError("score is not an integer")
    score = max(0, min(100, score))
    verdict = data.get("verdict")
    if verdict not in {"strong", "possible", "skip"}:
        raise ValueError(f"bad verdict: {verdict!r}")
    reasons = tuple(str(r) for r in data.get("reasons", []))
    missing = tuple(str(r) for r in data.get("missing_requirements", []))
    return ScoredJob(job=job, score=score, verdict=verdict,
                     reasons=reasons, missing_requirements=missing)


def _call_structured(client, model: str, job: Job, profile: SearchProfile) -> str:
    """One call using structured outputs (output_config.format). Returns JSON text."""
    resp = client.messages.create(
        model=model,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": SCORE_SCHEMA}},
        messages=[{"role": "user", "content": build_user_prompt(job, profile)}],
    )
    return next((b.text for b in resp.content if b.type == "text"), "")


def _call_tool(client, model: str, job: Job, profile: SearchProfile) -> str:
    """Fallback: force a tool call whose input IS the JSON we want."""
    resp = client.messages.create(
        model=model,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        tools=[{
            "name": "record_fit",
            "description": "Record the fit assessment for this job.",
            "input_schema": SCORE_SCHEMA,
        }],
        tool_choice={"type": "tool", "name": "record_fit"},
        messages=[{"role": "user", "content": build_user_prompt(job, profile)}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return json.dumps(block.input)
    return ""


def score_one(
    client,
    model: str,
    job: Job,
    profile: SearchProfile,
    *,
    method: Method = "structured",
) -> ScoredJob:
    """Score a single job, retrying once on unparseable output before giving up."""
    call = _call_structured if method == "structured" else _call_tool
    for attempt in (1, 2):
        try:
            raw = call(client, model, job, profile)
            return _coerce(job, json.loads(raw))
        except Exception:
            if attempt == 2:
                # Give up gracefully: keep the job, mark it unscored.
                return ScoredJob(job=job, verdict="unscored",
                                 reasons=("LLM output could not be parsed",))
    # Unreachable, but keeps type-checkers happy.
    return ScoredJob(job=job, verdict="unscored")


def score_jobs(
    jobs: list[Job],
    settings: Settings,
    profile: SearchProfile,
    *,
    method: Method = "structured",
    client=None,
) -> list[ScoredJob]:
    """Score every job. ``client`` is injectable for tests (defaults to the real
    Anthropic client, constructed lazily so importing this module needs no key)."""
    if client is None:
        import anthropic  # imported lazily so --demo / tests need no SDK key
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return [score_one(client, settings.model, job, profile, method=method) for job in jobs]
