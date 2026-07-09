"""The local dashboard app: FastAPI over the CLI's own functions.

LOCAL ONLY by design — this serves personal data (application history, career
facts). The CLI command binds it to 127.0.0.1 and there is no auth layer, so
it must never be exposed beyond localhost. No secrets reach the frontend: the
backend reads ``.env`` exactly as the CLI does.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from job_agent.dashboard import service
from job_agent.store import load_job_record

_STATIC = Path(__file__).resolve().parent / "static"


class SearchRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


class JobRequest(BaseModel):
    job_id: str = Field(min_length=1)


def create_app(*, data_dir: Path = Path("data"),
               profile_path: Path = Path("search_profile.yaml"),
               facts_path: Path | None = None,
               out_dir: Path | None = None,
               searcher=None, tailorer=None, previewer=None) -> FastAPI:
    """Build the app. ``searcher``/``tailorer``/``previewer`` are injectable for
    tests; the defaults call the real service functions (i.e. the CLI paths)."""
    data_dir = Path(data_dir)
    facts_path = Path(facts_path or data_dir / "career_facts.yaml")
    out_dir = Path(out_dir or data_dir / "output")
    searcher = searcher or (lambda days: service.run_search_cli(profile_path, days))
    tailorer = tailorer or (lambda job_id: service.run_tailor_cli(
        job_id, data_dir=data_dir, facts_path=facts_path, out_dir=out_dir))
    previewer = previewer or (lambda job_id: service.run_apply_preview(
        job_id, data_dir=data_dir, facts_path=facts_path, out_dir=out_dir))

    app = FastAPI(title="job-agent dashboard", docs_url=None, redoc_url=None)

    def _require_job(job_id: str) -> dict:
        record = load_job_record(data_dir / "last_search.json", job_id)
        if record is None:
            raise HTTPException(404, f"job id {job_id!r} not in the last search")
        return record

    @app.get("/api/applications")
    def applications() -> dict:
        return service.applications_view(data_dir / "applications.json")

    @app.get("/api/jobs")
    def jobs() -> dict:
        return service.jobs_view(data_dir / "last_search.json")

    @app.post("/api/search")
    def run_search(req: SearchRequest) -> dict:
        result = searcher(req.days)
        # whatever the run printed, the table shows what's now on disk
        return {**result, **service.jobs_view(data_dir / "last_search.json")}

    @app.post("/api/tailor")
    def run_tailor(req: JobRequest) -> dict:
        _require_job(req.job_id)
        return tailorer(req.job_id)

    @app.post("/api/apply/preview")
    def apply_preview(req: JobRequest) -> dict:
        _require_job(req.job_id)
        return previewer(req.job_id)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    return app
