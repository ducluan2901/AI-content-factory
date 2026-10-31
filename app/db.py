"""SQLAlchemy models for the MVP spine: job, stage_run (append-only audit), approval (append-only).

Stage payloads (script, voice timings, asset licences, render probe) live in stage_run.output_json;
the dedicated tables from DATABASE.md §4.3-4.14 arrive with the stages that need to query them.
"""
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, create_engine, event, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    # time-sortable, filesystem- and URL-safe (DATABASE.md §1.3)
    return f"{prefix}_{int(time.time() * 1000):012x}{secrets.token_hex(4)}"


class Base(DeclarativeBase):
    pass


class Channel(Base):
    """A distribution channel: one market, one language, one positioning (plan §2.1)."""

    __tablename__ = "channel"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: new_id("ch"))
    tenant_id: Mapped[str] = mapped_column(default="local")
    name: Mapped[str]
    market: Mapped[str] = mapped_column(default="vn")  # vn | global
    language: Mapped[str] = mapped_column(default="vi")  # vi | en
    positioning: Mapped[str | None]
    voice_id: Mapped[str | None]  # overrides the per-language TTS voice
    handles_json: Mapped[str] = mapped_column(default="{}")  # {platform: handle}
    status: Mapped[str] = mapped_column(default="active")  # active | paused
    is_sample: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now, onupdate=now)


SCORE_COMPONENTS = ("velocity", "retention_potential", "channel_fit", "monetization_potential", "production_feasibility")


class Trend(Base):
    """Trend Radar entry. Components are 0-100 or NULL when unknown; the score is derived (plan §7.4)."""

    __tablename__ = "trend"
    __table_args__ = tuple(CheckConstraint(f"{c} IS NULL OR {c} BETWEEN 0 AND 100", name=f"{c}_range") for c in SCORE_COMPONENTS) + (
        Index("ix_trend_status_score", "status", "trend_score"),
    )
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: new_id("tr"))
    tenant_id: Mapped[str] = mapped_column(default="local")
    title: Mapped[str]
    summary: Mapped[str | None]
    source: Mapped[str] = mapped_column(default="manual")  # manual | sample | <site name>
    source_url: Mapped[str | None]
    observed_at: Mapped[str] = mapped_column(default=now)
    market: Mapped[str] = mapped_column(default="vn")
    language: Mapped[str] = mapped_column(default="vi")
    format: Mapped[str] = mapped_column(default="facts")  # comedy | facts | gadget
    velocity: Mapped[float | None]
    retention_potential: Mapped[float | None]
    channel_fit: Mapped[float | None]
    monetization_potential: Mapped[float | None]
    production_feasibility: Mapped[float | None]
    trend_score: Mapped[float | None]
    confidence: Mapped[float | None]  # 0-1, share of components actually provided
    is_estimated: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(default="new")  # new | reviewed | selected | rejected
    notes: Mapped[str | None]
    is_sample: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now, onupdate=now)


class ContentIdea(Base):
    """Content Planner entry: a concrete video idea for one channel, optionally born from a trend."""

    __tablename__ = "content_idea"
    __table_args__ = (
        CheckConstraint("viral_score IS NULL OR viral_score BETWEEN 0 AND 100", name="viral_range"),
        CheckConstraint("revenue_score IS NULL OR revenue_score BETWEEN 0 AND 100", name="revenue_range"),
        CheckConstraint("target_duration_s BETWEEN 15 AND 90", name="idea_duration_window"),
    )
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: new_id("idea"))
    tenant_id: Mapped[str] = mapped_column(default="local")
    channel_id: Mapped[str] = mapped_column(ForeignKey("channel.id"))
    trend_id: Mapped[str | None] = mapped_column(ForeignKey("trend.id"))
    title: Mapped[str]
    format: Mapped[str] = mapped_column(default="facts")
    hook: Mapped[str | None]
    premise: Mapped[str | None]
    product_desc: Mapped[str | None]  # gadget format: what to feature, never invented specs
    target_duration_s: Mapped[int] = mapped_column(default=35)
    viral_score: Mapped[float | None]
    revenue_score: Mapped[float | None]
    status: Mapped[str] = mapped_column(default="backlog")  # backlog | selected | scripted | dropped
    notes: Mapped[str | None]
    is_sample: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now, onupdate=now)


class ContentJob(Base):
    __tablename__ = "content_job"
    __table_args__ = (
        # the human-approval wall at the storage layer (PRD D1)
        CheckConstraint("status <> 'EXPORTED' OR approval_id IS NOT NULL", name="exported_requires_approval"),
        # 15 s floor: the 30-day plan targets 20-35 s entertainment clips (plan §4.2)
        CheckConstraint("target_duration_s BETWEEN 15 AND 90", name="duration_window"),
        Index("ix_job_status_created", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: new_id("job"))
    tenant_id: Mapped[str] = mapped_column(default="local")
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("channel.id"))
    idea_id: Mapped[str | None] = mapped_column(ForeignKey("content_idea.id"))
    format: Mapped[str] = mapped_column(default="facts")  # comedy | facts | gadget
    title: Mapped[str | None]
    input_kind: Mapped[str] = mapped_column(default="topic")
    input_topic: Mapped[str | None]
    input_product_desc: Mapped[str | None]
    language: Mapped[str] = mapped_column(default="vi")
    target_platforms: Mapped[str] = mapped_column(default='["tiktok", "youtube_shorts", "facebook_reels"]')
    target_duration_s: Mapped[int] = mapped_column(default=55)
    status: Mapped[str] = mapped_column(default="QUEUED")
    current_stage: Mapped[str | None]
    profile: Mapped[str] = mapped_column(default="dev")
    # shortcut: plain text, not an FK, to avoid the job<->approval FK cycle SQLite cannot create; add when moving to Postgres
    approval_id: Mapped[str | None]
    error_summary: Mapped[str | None]
    total_cost_usd: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now, onupdate=now)
    started_at: Mapped[str | None]
    finished_at: Mapped[str | None]


class StageRun(Base):
    __tablename__ = "stage_run"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", "attempt"),
        Index("ix_run_job_stage", "job_id", "stage", "attempt"),
    )
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: new_id("run"))
    job_id: Mapped[str] = mapped_column(ForeignKey("content_job.id", ondelete="CASCADE"))
    stage: Mapped[str]
    attempt: Mapped[int]
    status: Mapped[str] = mapped_column(default="RUNNING")
    superseded_by: Mapped[str | None]
    input_json: Mapped[str | None]
    output_json: Mapped[str | None]
    provider: Mapped[str | None]
    model: Mapped[str | None]
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    duration_ms: Mapped[int | None]
    error_type: Mapped[str | None]
    error_message: Mapped[str | None]
    warnings_json: Mapped[str | None]
    log_path: Mapped[str | None]
    started_at: Mapped[str] = mapped_column(default=now)
    finished_at: Mapped[str | None]


class Approval(Base):
    __tablename__ = "approval"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: new_id("apr"))
    job_id: Mapped[str] = mapped_column(ForeignKey("content_job.id", ondelete="CASCADE"))
    decision: Mapped[str]  # approved | rejected
    approved_by: Mapped[str]  # NOT NULL: an approval always has a name attached
    notes: Mapped[str | None]
    created_at: Mapped[str] = mapped_column(default=now)


def migrate(database_url: str) -> str:
    """Bring the database to the latest Alembic revision (DATABASE.md §6). Returns the head revision id.

    A database created by create_all before Alembic existed is stamped at head instead of re-created.
    """
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    tables = inspect(engine).get_table_names()
    engine.dispose()
    if "content_job" in tables and "alembic_version" not in tables:
        command.stamp(cfg, "head")
    command.upgrade(cfg, "head")
    return ScriptDirectory.from_config(cfg).get_current_head()


def make_sessionmaker(database_url: str, create: bool = True) -> sessionmaker[Session]:
    """Session factory. create=True builds the schema directly (tests, scripts); the app runs migrate() first instead."""
    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _record):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    if create:
        Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def latest_ok_run(session: Session, job_id: str, stage: str) -> StageRun | None:
    """The resume point: newest OK row for the stage that has not been superseded."""
    stmt = (
        select(StageRun)
        .where(StageRun.job_id == job_id, StageRun.stage == stage, StageRun.status == "OK", StageRun.superseded_by.is_(None))
        .order_by(StageRun.attempt.desc())
    )
    return session.scalars(stmt).first()


def row_dict(row: Base) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}
