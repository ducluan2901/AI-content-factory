"""Post approved videos to YouTube through the n8n workflow "NDL - Dang YouTube Short" (scheduled or at once).

Only EXPORTED jobs (a person approved them in the console) are sent, YouTube once per job; a Publication row is
recorded with the URL n8n returns. The video goes inside the webhook request (export/upload.mp4, under 10 MB), so
nothing on this computer is exposed to the internet.

    python scripts/publish_n8n.py --at 19:30                  # every pending video, scheduled for 19:30 today (Vietnam time)
    python scripts/publish_n8n.py --job job_01... --at 19:30   # one job
    python scripts/publish_n8n.py --job job_01...              # publish at once

N8N_WEBHOOK_URL in .env: the workflow's production webhook URL.
"""
import argparse
import json
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Settings  # noqa: E402
from app.db import Publication, make_sessionmaker, now  # noqa: E402
from scripts.publish import pending, youtube_description, youtube_title  # noqa: E402

VN = timezone(timedelta(hours=7))


def publish_at(hhmm: str | None) -> str | None:
    """Today at hh:mm Vietnam time as RFC 3339 UTC; tomorrow if that moment has passed."""
    if not hhmm:
        return None
    h, m = map(int, hhmm.split(":"))
    at = datetime.combine(datetime.now(VN).date(), time(h, m), VN)
    if at <= datetime.now(VN):
        at += timedelta(days=1)
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--job", action="append", help="only this job (repeatable)")
    ap.add_argument("--at", help="hh:mm Vietnam time to publish; omitted = public at once")
    args = ap.parse_args()
    s = Settings()
    if not s.n8n_webhook_url:
        ap.error("N8N_WEBHOOK_URL is not set in .env")
    when = publish_at(args.at)
    failed = 0
    with make_sessionmaker(s.database_url, create=False)() as db:
        for job, _ in pending(db, ["youtube_shorts"], args.job):
            export = s.storage_dir / "jobs" / job.id / "export"
            m = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
            video = export / "upload.mp4"
            data = {"title": youtube_title(m), "description": youtube_description(m), "job_id": job.id}
            if when:
                data["publish_at"] = when
            try:
                with video.open("rb") as f:
                    r = httpx.post(s.n8n_webhook_url, data=data, files={"video": (video.name, f, "video/mp4")}, timeout=600)
                r.raise_for_status()
                url = r.json().get("url")
            except Exception as e:  # noqa: BLE001 - one failure must not stop the others; nothing is recorded for it
                failed += 1
                print(f"FAILED {job.id}: {e}")
                continue
            db.add(Publication(job_id=job.id, channel_id=job.channel_id, platform="youtube_shorts", url=url, published_at=when or now(),
                               notes="posted by scripts/publish_n8n.py" + (f", scheduled {args.at}" if when else "")))
            db.commit()
            print(f"sent {job.id} {url} {'at ' + when if when else 'now'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
