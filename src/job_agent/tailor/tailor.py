"""Tailoring: mega prompt + career facts + JD → resume text + NOTES.

The mega prompt is the system instruction; the user turn carries the immutable
career facts (the constraints) and the target job description. The model returns
the strict-format resume followed by a separate NOTES block, which we split out.

The LLM call is injectable (``generate`` / ``stub_response``) so demo mode and
tests run with no key and no network.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from job_agent.config import Settings
from job_agent.tailor.career_facts import CareerFacts

# Tailoring quality model — Sonnet-class, confirmed from the Anthropic docs.
TAILOR_MODEL = "claude-sonnet-4-6"

MEGAPROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "tailor_megaprompt.txt"

# Appended to the base mega prompt. This is authoritative and supersedes any
# conflicting instruction above it (notably the base prompt's [METRIC …]
# placeholder rule — the resume FACE must never carry a bracket).
POLICY_ADDENDUM = """=== OUTPUT & METRIC POLICY (OVERRIDES ANY CONFLICTING INSTRUCTION ABOVE) ===

FORMAT:
- Header: line 1 is my Name only; line 2 is exactly "email | phone". No title line, no tagline, no marketing subtitle.
- Section headings in ALL CAPS, exactly: PROFESSIONAL SUMMARY, TECHNICAL SKILLS, PROFESSIONAL EXPERIENCE, EDUCATION, CERTIFICATIONS. No other sections, no reordering.
- TECHNICAL SKILLS: render as category lines "Category: value, value, value" (comma-separated). Do NOT use tables, pipe characters (|), or bullet lists for skills.
- PROFESSIONAL EXPERIENCE: each role has Role:, Company:, Project Description:, Duration:, then Responsibilities: bullets and Achievements: bullets, each bullet starting with a real bullet character. Write each company name and title EXACTLY as given in my career facts.
- Project Description describes the PROJECT, not the company. The Company: line already names the employer, so the Project Description must NOT restate or describe the company (never write "<Company> is a ..." or a company blurb). Write 1-2 sentences on the actual project/system: what was built, the domain/problem it solved, and the scale/environment — drawn only from my real responsibilities. Reweight it toward what THIS JD cares about. Do not invent a project or add scope I did not work on.
- STRICT JD ADHERENCE: parse the JD's core responsibilities, required/preferred tools, domain signals, and outcomes, and reweight the Summary, Technical Skills, Responsibilities, and Achievements to foreground what THIS JD asks for. Mirror the JD's exact vocabulary ONLY where my real experience backs it. Never add a skill, tool, or claim just because the JD wants it — if it is not in my career facts it does not go on the resume; flag it in NOTES as a gap instead. Two different JDs must produce visibly different resumes from the same facts.
- SUMMARY LEADS WITH THE JD: the FIRST line of the Professional Summary must directly address the JD's top requirements (see JD KEY THEMES in the user message), naming the JD's domain and its most-emphasized tools where my real experience backs them. A generic summary that ignores the JD's themes is REJECTED by an automated check.
- SKILLS ORDERED BY THE JD: within TECHNICAL SKILLS, put the categories and tools this JD emphasizes FIRST; JD-peripheral categories go last or are dropped. Never add a skill that is not in my career facts.
- BULLETS ORDERED BY JD RELEVANCE: within each role, order Responsibilities most-JD-relevant FIRST (any trimming to fit 2 pages removes from the END, so the last bullet must always be the least JD-relevant). A bullet with no connection to any JD theme should be dropped in favor of a relevant one; every role is kept, only its emphasis changes.
- Do NOT use em-dashes or en-dashes anywhere. Use commas, periods, or parentheses. Dates use "Mon YYYY - Mon YYYY" or "Mon YYYY - Present".
- Do NOT output separator lines (e.g. "- --", "----"). Separate sections with a blank line only.
- Bullets are factual statements of what was built or done. Do NOT editorialize or add flattery: never write phrases like "directly applicable to <product>", "mirroring the <company> philosophy", "demonstrating the <X> required", or "matching the role's <Y>". State the work, not why it fits.

LENGTH AND SELECTIVITY (tailor hard to THIS JD; target 2 pages):
- Rank every responsibility by relevance to this specific JD and keep only the strongest. Include a bullet because the JD calls for it, not because it is true. Drop the rest.
- Responsibilities per role are a HARD LIMIT enforced by an automated check that REJECTS the resume if exceeded: the most-recent (first) role lists AT MOST 6 responsibility bullets; every older role lists AT MOST 4. Before you finish, count the bullets under each "Responsibilities:" heading and delete the least JD-relevant ones until each role is within its limit.
- Professional Summary: 3-4 lines maximum — no more than about 55 words in a single short paragraph — targeted to this JD. Do not exceed 55 words.
- Technical Skills: keep only the categories this JD actually cares about; drop irrelevant groups. Aim for about 5-7 skill lines, not more.
- If the resume would run past 2 pages, cut the least JD-relevant bullets first. Never trim a real metric or a required-skill match to save space.
- Keep bullets tight and factual. Remove filler and hedging.

METRICS (STRICT):
- The resume FACE must contain ZERO placeholders. Never print a bracket such as [METRIC ...] or [ADD REAL METRIC] on the resume.
- Use ONLY the real metrics listed under each employer's "Real metrics". Weave them naturally into Achievements.
- METRIC-MECHANISM COHERENCE: state every metric WITH the mechanism that actually produced it, and only in a bullet whose action plausibly causes that outcome. An infrastructure-cost reduction belongs with resource right-sizing, serving/inference optimization, or capacity work, NEVER attached to fine-tuning or model-quality work. An accuracy figure belongs with the modeling/evaluation work that produced it. If the producing mechanism is not in my career facts, attach the metric to the closest real activity or leave it out.
- EACH METRIC APPEARS EXACTLY ONCE on the entire resume (an automated check REJECTS the resume if a banked number appears twice). Choose its single strongest placement, normally an Achievements bullet. Never restate a Responsibilities bullet's number in Achievements or vice versa. Achievements must be DISTINCT outcomes, not reworded copies of Responsibilities bullets.
- SCOPE HONESTY: never use scale/scope qualifiers (multi-terabyte, petabyte, billions, enterprise-scale, firm-wide, company-wide, across the firm, web-scale, and the like) unless that exact phrase appears in my career facts. An automated check REJECTS the resume if an unsupported qualifier appears.
- If a role has no real metric, write a strong, specific QUALITATIVE achievement instead (what was delivered and its real impact) with no number and no bracket.
- Keep Achievements to 2-3 per role, high-impact. Do not over-stuff numbers.
- Never fabricate a number. If a real number would strengthen a bullet, do NOT put it on the resume; add ONE question to NOTES instead.

CERTIFICATIONS:
- Print exactly the certifications listed in my career facts, always. Never print "None" and never invent one. A JD-valued cert I do not hold goes under NOTES as "suggested to obtain" only.

NOTES (a separate block after the resume, beginning with the word NOTES):
- Real gaps versus the JD.
- At most 2-3 questions asking me to supply a true figure (each naming the role and the metric).
- Suggested certifications to obtain that I do not already hold.
"""

# Case-insensitive start of the trailing NOTES block.
_NOTES_RE = re.compile(r"(?im)^[#*\s]*NOTES\b.*$")


class TailorResult(BaseModel):
    """The parsed tailoring output."""

    model_config = ConfigDict(frozen=True)

    resume_text: str
    notes: str
    raw: str


def load_megaprompt(path: str | Path = MEGAPROMPT_PATH) -> str:
    return Path(path).read_text().strip()


def build_facts_block(facts: CareerFacts) -> str:
    """Serialize the immutable career facts into the constraint block."""
    lines: list[str] = [
        "=== MY CAREER FACTS (IMMUTABLE — obey exactly) ===",
        f"Name: {facts.name}",
        f"Role: {facts.role}",
        f"Email: {facts.email}",
        f"Phone: {facts.phone}",
    ]
    if facts.location:
        lines.append(f"Location: {facts.location}")
    lines.append("\nEducation:")
    lines += [f"- {e}" for e in facts.education]

    lines.append("\nCertifications I actually hold (print ONLY these; if none, print none):")
    if facts.certifications:
        lines += [f"- {c.name}" + (f" ({c.issuer})" if c.issuer else "") for c in facts.certifications]
    else:
        lines.append("- (none)")

    lines.append("\nReal skills inventory (use only these, in real contexts):")
    for category, skills in facts.skills_inventory.items():
        lines.append(f"- {category}: " + "; ".join(skills))

    lines.append("\nEmployers (company / title / duration are FIXED — never change/add/remove):")
    for e in facts.employers:
        lines.append(f"\n>>> {e.company} | {e.title} | {e.location} | {e.duration}")
        if e.project_description:
            lines.append(f"Company background (CONTEXT ONLY — do NOT restate the company on the "
                         f"resume; write a PROJECT-focused Project Description from the "
                         f"responsibilities below): {e.project_description}")
        lines.append("Real responsibilities (rewrite for the JD using only these facts):")
        lines += [f"  - {b}" for b in e.real_bullets]
        lines.append("Real metrics (the ONLY numbers you may cite for this role):")
        lines += ([f"  - {m}" for m in e.real_metrics] or ["  - (none — use a JD-aware [METRIC — …?] placeholder)"])
        lines.append("Real tools available in this engagement (era-appropriate; no anachronisms):")
        lines.append("  " + "; ".join(e.real_skills))
    return "\n".join(lines)


# Words that carry no JD signal (job-ad boilerplate + English stopwords).
_THEME_NOISE = frozenset("""
a an and are as at be been build building by can do end etc experience for from
had has have in into is it its of on or our over per role s such team teams that
the their this to we will with without work working year years you your ability
strong looking hands skills including required preferred plus responsibilities
about across more most other own need new large modern production productionize
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]{1,}")


def jd_themes(jd_text: str, top: int = 12) -> list[str]:
    """The JD's most salient terms, most-emphasized first (frequency-ranked).

    Deterministic and dependency-free: tokenize, drop boilerplate/stopwords,
    rank by count (ties keep first-appearance order). These drive the summary
    check and the skills reordering — nothing here adds content.
    """
    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    for i, tok in enumerate(_WORD.findall(jd_text or "")):
        w = tok.lower().strip("./-")
        if len(w) < 2 or w in _THEME_NOISE:
            continue
        counts[w] = counts.get(w, 0) + 1
        order.setdefault(w, i)
    ranked = sorted(counts, key=lambda w: (-counts[w], order[w]))
    return ranked[:top]


def reorder_skills(resume_text: str, jd_text: str) -> str:
    """Reorder TECHNICAL SKILLS category lines by JD emphasis (most hits first).

    Pure line reordering within the section — no line is added, removed, or
    edited, so the no-drift/format gates are unaffected.
    """
    themes = jd_themes(jd_text, top=20)
    lines = resume_text.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        s = ln.strip().upper()
        if s == "TECHNICAL SKILLS":
            start = i + 1
        elif start is not None and s in ("PROFESSIONAL EXPERIENCE", "EDUCATION",
                                         "PROFESSIONAL SUMMARY", "CERTIFICATIONS"):
            end = i
            break
    if start is None or end is None:
        return resume_text
    block = lines[start:end]
    skill_idx = [i for i, ln in enumerate(block) if ":" in ln and ln.strip()]
    if len(skill_idx) < 2:
        return resume_text

    def score(line: str) -> int:
        low = line.lower()
        return sum(low.count(t) for t in themes)

    reordered = sorted((block[i] for i in skill_idx), key=score, reverse=True)  # stable
    for i, new_line in zip(skill_idx, reordered):
        block[i] = new_line
    return "\n".join(lines[:start] + block + lines[end:])


def build_user_message(facts: CareerFacts, jd_text: str) -> str:
    themes = ", ".join(jd_themes(jd_text))
    return (
        f"{build_facts_block(facts)}\n\n"
        "=== TARGET JOB DESCRIPTION ===\n"
        f"{jd_text.strip()}\n\n"
        f"=== JD KEY THEMES (extracted; most-emphasized first) ===\n{themes}\n\n"
        "Produce the tailored resume in the strict format, then the separate NOTES block."
    )


def split_notes(raw: str) -> tuple[str, str]:
    """Split model output into (resume_text, notes) on the NOTES delimiter."""
    match = _NOTES_RE.search(raw)
    if not match:
        return raw.strip(), ""
    return raw[: match.start()].strip(), raw[match.start():].strip()


def _default_generate(system: str, user: str, settings: Settings) -> str:
    """Real Sonnet call (imported lazily so demo/tests need no SDK/key)."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=TAILOR_MODEL,
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "max_tokens":
        raise ValueError("Tailoring output hit max_tokens (truncated). Increase max_tokens.")
    return "".join(b.text for b in resp.content if b.type == "text")


def tailor_resume(
    facts: CareerFacts,
    jd_text: str,
    *,
    settings: Settings | None = None,
    megaprompt: str | None = None,
    generate=None,
    stub_response: str | None = None,
    extra_instruction: str | None = None,
) -> TailorResult:
    """Tailor the resume to ``jd_text``.

    * ``stub_response`` — use canned text instead of calling the model (demo/tests).
    * ``generate`` — inject a custom ``(system, user, settings) -> str`` (tests).
    * ``extra_instruction`` — appended to the user turn (used to correct a failed
      format check on a retry).
    * otherwise a real Sonnet call is made using ``settings``.
    """
    base = megaprompt if megaprompt is not None else load_megaprompt()
    system = f"{base}\n\n{POLICY_ADDENDUM}"
    user = build_user_message(facts, jd_text)
    if extra_instruction:
        user = f"{user}\n\n{extra_instruction}"

    if stub_response is not None:
        raw = stub_response
    elif generate is not None:
        raw = generate(system, user, settings)
    else:
        if settings is None or not settings.anthropic_api_key:
            raise ValueError("A real tailoring run needs Settings with an ANTHROPIC_API_KEY.")
        raw = _default_generate(system, user, settings)

    resume_text, notes = split_notes(raw)
    return TailorResult(resume_text=resume_text, notes=notes, raw=raw)
