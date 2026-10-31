"""SQLAlchemy models for the MVP spine: job, stage_run (append-only audit), approval (append-only).

Stage payloads (script, voice timings, asset licences, render probe) live in stage_run.output_json;
the dedicated tables from DATABASE.md §4.3-4.14 arrive with the stages that need to query them.
"""
import secrets
import time
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    # time-sortable, filesystem- and URL-safe (DATABASE.md §1.3)
    return f"{prefix}_{int(time.time() * 1000):012x}{secrets.token_hex(4)}"


class Base(DeclarativeBase):
    pass


class ContentJob(Base):
    __tablename__ = "content_job"
    __table_args__ = (
        # the human-approval wall at the storage layer (PRD D1)
        CheckConstraint("status <> 'EXPORTED' OR approval_id IS NOT NULL", name="exported_requires_approval"),
        CheckConstraint("target_duration_s BETWEEN 30 AND 90", name="duration_window"),
        Index("ix_job_status_created", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: new_id("job"))
    tenant_id: Mapped[str] = mapped_column(default="local")
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


def make_sessionmaker(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _record):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    # shortcut: create_all instead of Alembic; add Alembic with the first schema change (DATABASE.md §6)
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
