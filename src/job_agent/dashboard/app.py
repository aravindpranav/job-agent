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

from job_agent.apply.tracker import Status, upsert_job_state
from job_agent.dashboard import service
from job_agent.store import load_job_record

_STATIC = Path(__file__).resolve().parent / "static"


class SearchRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


class JobRequest(BaseModel):
    job_id: str = Field(min_length=1)


class SessionRequest(BaseModel):
    session_id: str = Field(min_length=1)


class EditRequest(SessionRequest):
    selector: str = Field(min_length=1)
    value: str
    mark_done: bool = False


class TrackRequest(BaseModel):
    """User-managed state for one job: status pipeline, notes, follow-up date."""

    job_id: str = Field(min_length=1)
    company: str = ""
    title: str = ""
    source: str = ""
    status: Status | None = None
    notes: str | None = None
    follow_up: str | None = Field(default=None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")


def create_app(*, data_dir: Path = Path("data"),
               profile_path: Path = Path("search_profile.yaml"),
               facts_path: Path | None = None,
               out_dir: Path | None = None,
               searcher=None, tailorer=None, previewer=None,
               apply_session_factory=None) -> FastAPI:
    """Build the app. ``searcher``/``tailorer``/``previewer``/
    ``apply_session_factory`` are injectable for tests; the defaults call the
    real service functions (i.e. the CLI paths)."""
    data_dir = Path(data_dir)
    facts_path = Path(facts_path or data_dir / "career_facts.yaml")
    out_dir = Path(out_dir or data_dir / "output")
    searcher = searcher or (lambda days: service.run_search_cli(profile_path, days))
    tailorer = tailorer or (lambda job_id: service.run_tailor_cli(
        job_id, data_dir=data_dir, facts_path=facts_path, out_dir=out_dir))
    previewer = previewer or (lambda job_id: service.run_apply_preview(
        job_id, data_dir=data_dir, facts_path=facts_path, out_dir=out_dir))
    apply_session_factory = apply_session_factory or (
        lambda job_id: service.start_apply_session(
            job_id, data_dir=data_dir, facts_path=facts_path, out_dir=out_dir))

    app = FastAPI(title="job-agent dashboard", docs_url=None, redoc_url=None)
    sessions: dict[str, object] = {}   # live apply sessions, one browser each

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
        return service.jobs_view(data_dir / "last_search.json",
                                 data_dir / "applications.json")

    @app.post("/api/search")
    def run_search(req: SearchRequest) -> dict:
        result = searcher(req.days)
        # whatever the run printed, the table shows what's now on disk
        return {**result, **service.jobs_view(data_dir / "last_search.json",
                                              data_dir / "applications.json")}

    @app.post("/api/track")
    def track(req: TrackRequest) -> dict:
        record = upsert_job_state(
            data_dir / "applications.json", job_id=req.job_id, company=req.company,
            title=req.title, source=req.source, status=req.status,
            notes=req.notes, follow_up=req.follow_up)
        return {**record.model_dump(),
                "needs_follow_up": service._needs_follow_up(record)}

    @app.get("/api/resume/{job_id}")
    def resume(job_id: str) -> FileResponse:
        record = _require_job(job_id)
        pdf = service.resume_pdf_path(record, facts_path=facts_path, out_dir=out_dir)
        if pdf is None:
            raise HTTPException(404, "no tailored resume for this job — run Tailor first")
        return FileResponse(pdf, media_type="application/pdf")

    @app.post("/api/tailor")
    def run_tailor(req: JobRequest) -> dict:
        _require_job(req.job_id)
        return tailorer(req.job_id)

    @app.post("/api/apply/preview")
    def apply_preview(req: JobRequest) -> dict:
        _require_job(req.job_id)
        return previewer(req.job_id)

    # --- live apply sessions: the CLI's review gate, in the UI -------------

    def _session(session_id: str):
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "no such apply session (it may have finished)")
        return session

    @app.post("/api/apply/start")
    def apply_start(req: JobRequest) -> dict:
        from uuid import uuid4

        _require_job(req.job_id)
        session = apply_session_factory(req.job_id)
        session_id = uuid4().hex
        sessions[session_id] = session
        try:
            state = session.start()
        except Exception:
            sessions.pop(session_id, None)
            raise
        return {"session_id": session_id, **state}

    @app.post("/api/apply/rescan")
    def apply_rescan(req: SessionRequest) -> dict:
        return {"session_id": req.session_id, **_session(req.session_id).rescan()}

    @app.post("/api/apply/edit")
    def apply_edit(req: EditRequest) -> dict:
        state = _session(req.session_id).edit(req.selector, req.value,
                                              mark_done=req.mark_done)
        return {"session_id": req.session_id, **state}

    @app.post("/api/apply/submit")
    def apply_submit(req: SessionRequest) -> dict:
        from job_agent.dashboard.apply_session import SubmitBlocked

        session = _session(req.session_id)
        try:
            result = session.submit()      # the ONLY path to a real submission
        except SubmitBlocked as exc:
            raise HTTPException(409, str(exc)) from exc
        sessions.pop(req.session_id, None)
        return result

    @app.post("/api/apply/cancel")
    def apply_cancel(req: SessionRequest) -> dict:
        session = sessions.pop(req.session_id, None)
        if session is None:
            raise HTTPException(404, "no such apply session (it may have finished)")
        return session.cancel()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    return app
