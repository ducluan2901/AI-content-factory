"""Run one job to its next gate inside this process, with this shell's environment, instead of the API worker.

    python scripts/run_job_local.py job_xxx            # a FAILED job is re-queued first; QUEUED/SCRIPT-approved jobs just run
    python scripts/run_job_local.py job_xxx SCRIPT     # re-run from a stage (FAILED / NEEDS_REVISION / REJECTED jobs)

Needed when the API runs under Task Scheduler, whose processes do not see the Claude CLI login (LLM_PROVIDER=claude_cli).
Do not run it on a job the API is currently running.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import pipeline  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import ContentJob, make_sessionmaker, migrate  # noqa: E402
from app.providers import build_llm, build_stock, build_tts  # noqa: E402

s = Settings()
migrate(s.database_url)
sm = make_sessionmaker(s.database_url, create=False)
job_id = sys.argv[1]
with sm() as db:
    job = db.get(ContentJob, job_id)
    from_stage = sys.argv[2] if len(sys.argv) > 2 else None
    if job.status in pipeline.RERUNNABLE:
        pipeline.requeue(db, job_id, from_stage)
    elif job.status != "QUEUED":
        sys.exit(f"{job_id} is {job.status}; this runs QUEUED, FAILED, NEEDS_REVISION or REJECTED jobs")
print(job_id, "->", asyncio.run(pipeline.Pipeline(s, sm, build_llm(s), build_tts(s), build_stock(s)).run_job(job_id)))
