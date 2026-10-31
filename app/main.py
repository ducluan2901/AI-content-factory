"""FastAPI service: submit, poll, inspect, approve/reject, re-run. Binds to localhost only (no auth in the MVP)."""
import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app import pipeline, planner
from app.config import Settings
from app.db import SCORE_COMPONENTS, Approval, Channel, ContentIdea, ContentJob, StageRun, Trend, make_sessionmaker, migrate, row_dict
from app.providers import build_llm, build_stock, build_tts

FORMAT_RE = "^(comedy|facts|gadget)$"
Score = Field(default=None, ge=0, le=100)


class JobIn(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    product_desc: str | None = Field(default=None, max_length=2000)
    language: str = Field(default="vi", pattern="^(vi|en)$")
    format: str = Field(default="facts", pattern=FORMAT_RE)
    channel_id: str | None = None
    target_duration_s: int | None = Field(default=None, ge=15, le=90)  # None: the format's default
    review_script: bool = True  # pause at SCRIPT_REVIEW before voice and render
    burn_subtitles: bool = False  # hard-code the subtitles into the video (SRT/VTT are always exported)


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    market: str = Field(default="vn", pattern="^(vn|global)$")
    language: str = Field(default="vi", pattern="^(vi|en)$")
    positioning: str | None = Field(default=None, max_length=500)
    voice_id: str | None = Field(default=None, max_length=100)
    handles: dict[str, str] = {}


class TrendIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    source: str = Field(default="manual", max_length=100)
    source_url: str | None = Field(default=None, max_length=1000)
    observed_at: str | None = None
    market: str = Field(default="vn", pattern="^(vn|global)$")
    language: str = Field(default="vi", pattern="^(vi|en)$")
    format: str = Field(default="facts", pattern=FORMAT_RE)
    velocity: float | None = Score
    retention_potential: float | None = Score
    channel_fit: float | None = Score
    monetization_potential: float | None = Score
    production_feasibility: float | None = Score
    notes: str | None = Field(default=None, max_length=2000)


class TrendPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(new|reviewed|selected|rejected)$")
    velocity: float | None = Score
    retention_potential: float | None = Score
    channel_fit: float | None = Score
    monetization_potential: float | None = Score
    production_feasibility: float | None = Score
    notes: str | None = Field(default=None, max_length=2000)


class IdeaIn(BaseModel):
    channel_id: str
    trend_id: str | None = None
    title: str = Field(min_length=3, max_length=300)
    format: str = Field(default="facts", pattern=FORMAT_RE)
    hook: str | None = Field(default=None, max_length=500)
    premise: str | None = Field(default=None, max_length=2000)
    product_desc: str | None = Field(default=None, max_length=2000)
    target_duration_s: int | None = Field(default=None, ge=15, le=90)
    viral_score: float | None = Score
    revenue_score: float | None = Score
    notes: str | None = Field(default=None, max_length=2000)


class IdeaPatch(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    status: str | None = Field(default=None, pattern="^(backlog|selected|scripted|dropped)$")
    hook: str | None = Field(default=None, max_length=500)
    premise: str | None = Field(default=None, max_length=2000)
    product_desc: str | None = Field(default=None, max_length=2000)
    target_duration_s: int | None = Field(default=None, ge=15, le=90)
    viral_score: float | None = Score
    revenue_score: float | None = Score
    notes: str | None = Field(default=None, max_length=2000)


class DecisionIn(BaseModel):
    approved_by: str = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class RerunIn(BaseModel):
    from_stage: str | None = None


class ScriptEditIn(BaseModel):
    edited_by: str = Field(min_length=1, max_length=100)
    script: dict  # validated by pipeline.ScriptOut: title, caption, hashtags, scenes


class RegenerateIn(BaseModel):
    requested_by: str = Field(min_length=1, max_length=100)
    notes: str = Field(min_length=3, max_length=2000)


class BatchIn(BaseModel):
    idea_ids: list[str] = Field(min_length=1, max_length=20)


def create_app(settings: Settings | None = None, llm=None, tts=None, stock=None) -> FastAPI:
    s = settings or Settings()
    s.check()
    s.storage_dir.mkdir(parents=True, exist_ok=True)
    if s.app_env != "test":  # tests build throwaway schemas directly; real databases go through Alembic
        migrate(s.database_url)
    sm = make_sessionmaker(s.database_url, create=s.app_env == "test")
    pipe = pipeline.Pipeline(s, sm, llm or build_llm(s), tts or build_tts(s), stock if stock is not None else build_stock(s))
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
                  "claude_cli": which(s.claude_cli_path) if s.llm_provider == "claude_cli" else "n/a",
                  # presence only: the key value never leaves the process
                  "anthropic_api_key": bool(s.anthropic_api_key and s.anthropic_api_key.get_secret_value()) if s.llm_provider == "anthropic_api" else "n/a"}
        return {"ok": all(checks.values()), "app_env": s.app_env, "llm_provider": s.llm_provider, "tts_provider": s.tts_provider, "checks": checks, "queue": queue.qsize()}

    def required_or_422(body: BaseModel, required: tuple[str, ...]) -> dict:
        """Fields the client sent; null is allowed only where the column may be empty."""
        data = body.model_dump(exclude_unset=True)
        if nulls := [k for k in required if k in data and data[k] is None]:
            raise HTTPException(422, f"these fields cannot be null: {nulls}")
        return data

    def get_or_404(db, model, id_: str):
        row = db.get(model, id_)
        if not row:
            raise HTTPException(404, f"{model.__tablename__} not found")
        return row

    @app.post("/jobs", status_code=201)
    async def create_job(body: JobIn):
        with sm() as db:
            if body.channel_id:
                get_or_404(db, Channel, body.channel_id)
            job = ContentJob(title=body.topic[:120], input_kind="product" if body.product_desc else "topic", input_topic=body.topic,
                             input_product_desc=body.product_desc, language=body.language, format=body.format, channel_id=body.channel_id,
                             target_duration_s=body.target_duration_s or planner.default_duration(body.format), review_script=body.review_script, burn_subtitles=body.burn_subtitles,
                             profile=s.app_env, status="QUEUED")
            db.add(job)
            db.commit()
            await queue.put(job.id)
            return row_dict(job)

    # ---------------------------------------------------------------- channels, trends, ideas (Sprint 1)

    @app.get("/formats")
    def formats():
        return {k: {"label": v["label"], "window": v["window"]} for k, v in planner.FORMATS.items()}

    @app.get("/channels")
    def list_channels():
        with sm() as db:
            return [row_dict(c) for c in db.scalars(select(Channel).order_by(Channel.created_at))]

    @app.post("/channels", status_code=201)
    def create_channel(body: ChannelIn):
        with sm() as db:
            ch = Channel(**body.model_dump(exclude={"handles"}), handles_json=json.dumps(body.handles))
            db.add(ch)
            db.commit()
            return row_dict(ch)

    def trend_out(t: Trend) -> dict:
        return row_dict(t) | {"breakdown": planner.trend_score({c: getattr(t, c) for c in SCORE_COMPONENTS})["breakdown"]}

    @app.get("/trends")
    def list_trends(status: str | None = None, market: str | None = None, format: str | None = None, limit: int = 200):
        stmt = select(Trend)
        for col, val in ((Trend.status, status), (Trend.market, market), (Trend.format, format)):
            if val:
                stmt = stmt.where(col == val)
        with sm() as db:
            return [trend_out(t) for t in db.scalars(stmt.order_by(Trend.trend_score.desc().nullslast(), Trend.observed_at.desc()).limit(limit))]

    @app.post("/trends", status_code=201)
    def create_trend(body: TrendIn):
        with sm() as db:
            t = Trend(**body.model_dump(exclude_none=True))
            planner.rescore(t)
            db.add(t)
            db.commit()
            return trend_out(t)

    @app.get("/trends/{trend_id}")
    def get_trend(trend_id: str):
        with sm() as db:
            return trend_out(get_or_404(db, Trend, trend_id))

    @app.patch("/trends/{trend_id}")
    def patch_trend(trend_id: str, body: TrendPatch):
        with sm() as db:
            t = get_or_404(db, Trend, trend_id)
            for k, v in required_or_422(body, ("status",)).items():
                setattr(t, k, v)
            planner.rescore(t)
            db.commit()
            return trend_out(t)

    @app.get("/ideas")
    def list_ideas(status: str | None = None, channel_id: str | None = None, format: str | None = None, limit: int = 200):
        stmt = select(ContentIdea)
        for col, val in ((ContentIdea.status, status), (ContentIdea.channel_id, channel_id), (ContentIdea.format, format)):
            if val:
                stmt = stmt.where(col == val)
        with sm() as db:
            return [row_dict(i) for i in db.scalars(stmt.order_by(ContentIdea.created_at.desc()).limit(limit))]

    @app.post("/ideas", status_code=201)
    def create_idea(body: IdeaIn):
        with sm() as db:
            get_or_404(db, Channel, body.channel_id)
            if body.trend_id:
                get_or_404(db, Trend, body.trend_id)
            data = body.model_dump(exclude_none=True)
            data.setdefault("target_duration_s", planner.default_duration(body.format))
            idea = ContentIdea(**data)
            db.add(idea)
            db.commit()
            return row_dict(idea)

    @app.patch("/ideas/{idea_id}")
    def patch_idea(idea_id: str, body: IdeaPatch):
        with sm() as db:
            idea = get_or_404(db, ContentIdea, idea_id)
            for k, v in required_or_422(body, ("title", "status", "target_duration_s")).items():
                setattr(idea, k, v)
            db.commit()
            return row_dict(idea)

    @app.post("/ideas/{idea_id}/jobs", status_code=201)
    async def job_from_idea(idea_id: str):
        with sm() as db:
            idea = get_or_404(db, ContentIdea, idea_id)
            if idea.status == "dropped":
                raise HTTPException(409, "idea is dropped")
            job = planner.job_from_idea(db, idea, s.app_env)
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
            job = get_job(db, job_id)
            try:
                if job.status != "APPROVED":  # APPROVED here means an earlier export failed: retry only the export
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

    # ---------------------------------------------------------------- script gate (Sprint 2)

    @app.put("/jobs/{job_id}/script")
    def edit_script(job_id: str, body: ScriptEditIn):
        with sm() as db:
            get_job(db, job_id)
            try:
                return pipeline.save_script(db, s, job_id, body.script, body.edited_by)
            except pipeline.GateError as e:
                raise HTTPException(409, str(e))
            except ValidationError as e:
                raise HTTPException(422, e.errors(include_url=False))

    @app.post("/jobs/{job_id}/script/approve")
    async def approve_script(job_id: str, body: DecisionIn):
        with sm() as db:
            get_job(db, job_id)
            try:
                job = pipeline.approve_script(db, job_id, body.approved_by, body.notes)
            except pipeline.GateError as e:
                raise HTTPException(409, str(e))
            await queue.put(job_id)  # continue from VOICE; SCRIPT already has an OK run
            return row_dict(job)

    @app.post("/jobs/{job_id}/script/regenerate")
    async def regenerate_script(job_id: str, body: RegenerateIn):
        with sm() as db:
            get_job(db, job_id)
            try:
                job = pipeline.regenerate_script(db, job_id, body.notes, body.requested_by)
            except pipeline.GateError as e:
                raise HTTPException(409, str(e))
            await queue.put(job_id)
            return row_dict(job)

    @app.post("/ideas/batch-jobs", status_code=201)
    async def batch_jobs(body: BatchIn):
        """Queue several ideas at once (plan: 10 scripts per batch). Jobs stop at SCRIPT_REVIEW, so nothing is rendered unreviewed."""
        with sm() as db:
            ideas = [get_or_404(db, ContentIdea, i) for i in dict.fromkeys(body.idea_ids)]  # same id twice = one job
            if dropped := [i.id for i in ideas if i.status == "dropped"]:
                raise HTTPException(409, f"dropped ideas: {dropped}")
            jobs = [planner.job_from_idea(db, i, s.app_env) for i in ideas]
            for j in jobs:
                await queue.put(j.id)
            return [row_dict(j) for j in jobs]

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
