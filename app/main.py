"""FastAPI service: submit, poll, inspect, approve/reject, re-run. Binds to localhost only (no auth in the MVP)."""
import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import pipeline
from app.config import Settings
from app.db import Approval, ContentJob, StageRun, make_sessionmaker, row_dict
from app.providers import build_llm, build_tts


class JobIn(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    product_desc: str | None = Field(default=None, max_length=2000)
    language: str = Field(default="vi", pattern="^(vi|en)$")
    target_duration_s: int = Field(default=55, ge=45, le=60)


class DecisionIn(BaseModel):
    approved_by: str = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class RerunIn(BaseModel):
    from_stage: str | None = None


def create_app(settings: Settings | None = None, llm=None, tts=None) -> FastAPI:
    s = settings or Settings()
    s.check()
    s.storage_dir.mkdir(parents=True, exist_ok=True)
    sm = make_sessionmaker(s.database_url)
    pipe = pipeline.Pipeline(s, sm, llm or build_llm(s), tts or build_tts(s))
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def worker():
        while True:
            job_id = await queue.get()
            try:
                await pipe.run_job(job_id)
            finally:
                queue.task_done()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with sm() as db:  # resume jobs a crash or restart interrupted
            for jid in db.scalars(select(ContentJob.id).where(ContentJob.status.in_(["QUEUED", "RUNNING"])).order_by(ContentJob.created_at)):
                queue.put_nowait(jid)
        task = asyncio.create_task(worker())
        yield
        task.cancel()

    app = FastAPI(title="AI Content Factory", version="0.1.0", lifespan=lifespan)
    app.state.queue, app.state.sm, app.state.settings = queue, sm, s

    def get_job(db, job_id: str) -> ContentJob:
        job = db.get(ContentJob, job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.get("/health")
    def health():
        def which(p):
            return shutil.which(p) or (p if Path(p).is_file() else None)
        checks = {"ffmpeg": which(s.ffmpeg_path), "ffprobe": which(s.ffprobe_path), "font": s.font_path if Path(s.font_path).is_file() else None,
                  "claude_cli": which(s.claude_cli_path) if s.llm_provider == "claude_cli" else "n/a"}
        return {"ok": all(checks.values()), "app_env": s.app_env, "llm_provider": s.llm_provider, "tts_provider": s.tts_provider, "checks": checks, "queue": queue.qsize()}

    @app.post("/jobs", status_code=201)
    async def create_job(body: JobIn):
        with sm() as db:
            job = ContentJob(title=body.topic[:120], input_kind="product" if body.product_desc else "topic", input_topic=body.topic,
                             input_product_desc=body.product_desc, language=body.language, target_duration_s=body.target_duration_s,
                             profile=s.app_env, status="QUEUED")
            db.add(job)
            db.commit()
            await queue.put(job.id)
            return row_dict(job)

    @app.get("/jobs")
    def list_jobs(limit: int = 50):
        with sm() as db:
            return [row_dict(j) for j in db.scalars(select(ContentJob).order_by(ContentJob.created_at.desc()).limit(limit))]

    @app.get("/jobs/{job_id}")
    def job_detail(job_id: str):
        with sm() as db:
            job = get_job(db, job_id)
            runs = db.scalars(select(StageRun).where(StageRun.job_id == job_id).order_by(StageRun.started_at)).all()
            approvals = db.scalars(select(Approval).where(Approval.job_id == job_id).order_by(Approval.created_at)).all()
            outputs = {r.stage: json.loads(r.output_json) for r in runs if r.status == "OK" and r.superseded_by is None}
            return {"job": row_dict(job), "outputs": outputs, "runs": [row_dict(r) for r in runs], "approvals": [row_dict(a) for a in approvals]}

    @app.get("/files/{path:path}")
    def get_file(path: str):
        root = s.storage_dir.resolve()
        p = (root / path).resolve()
        if not p.is_relative_to(root) or not p.is_file():  # no path traversal out of storage/
            raise HTTPException(404, "file not found")
        return FileResponse(p)

    @app.post("/jobs/{job_id}/approve")
    def approve(job_id: str, body: DecisionIn):
        with sm() as db:
            get_job(db, job_id)
            try:
                pipeline.decide(db, job_id, "approved", body.approved_by, body.notes)
                bundle = pipeline.export(db, s, job_id)
            except pipeline.GateError as e:
                raise HTTPException(409, str(e))
            return {"job": row_dict(db.get(ContentJob, job_id)), "export": bundle}

    @app.post("/jobs/{job_id}/reject")
    def reject(job_id: str, body: DecisionIn):
        with sm() as db:
            get_job(db, job_id)
            try:
                return row_dict(pipeline.decide(db, job_id, "rejected", body.approved_by, body.notes))
            except pipeline.GateError as e:
                raise HTTPException(409, str(e))

    @app.post("/jobs/{job_id}/run")
    async def rerun(job_id: str, body: RerunIn):
        with sm() as db:
            get_job(db, job_id)
            try:
                job = pipeline.requeue(db, job_id, body.from_stage)
            except pipeline.GateError as e:
                raise HTTPException(409, str(e))
            await queue.put(job_id)
            return row_dict(job)

    return app


def app() -> FastAPI:  # uvicorn --factory app.main:app
    return create_app()
