"""Daily YouTube publisher: uploads the approved videos a person queued for a slot, through the n8n workflow.

Only rows of storage/publish_queue.csv are sent ("2026-11-09 11:30,job_01...", Vietnam time; # starts a comment),
so approving a video is never the same as choosing its date. Each run, after the YouTube quota resets:
  - books every queued, EXPORTED, not yet published job whose slot is still ahead, earliest slot first,
    as a scheduled (private, goes public at the slot) YouTube Short, at most QUOTA uploads per quota day;
  - holds a job whose narration promises tomorrow's video ("Mai: …", "Phần 1") until that next video, queued
    the next day at the same time, is approved too, so a teaser never points at nothing;
  - writes the TikTok/Facebook file + caption into dang_bai for the person to post by hand.
Never re-uploads: a Publication row (a placeholder before the POST) marks a job as sent; any unclear n8n answer
keeps the placeholder, prints NEEDS HUMAN CHECK and stops.

    .venv\\Scripts\\python scripts\\auto_publish.py --add "2026-11-09 11:30" job_01...   # queue a job
    .venv\\Scripts\\python scripts\\auto_publish.py --dry-run                            # show what would be sent
    .venv\\Scripts\\python scripts\\auto_publish.py                                      # send
"""
import argparse
import json
import msvcrt
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import select  # noqa: E402

from app.config import ROOT, Settings  # noqa: E402
from app.db import Channel, ContentJob, Publication, latest_ok_run, make_sessionmaker  # noqa: E402
from scripts.publish import facebook_description, tiktok_caption, youtube_description, youtube_title  # noqa: E402

VN = ZoneInfo("Asia/Ho_Chi_Minh")
PACIFIC = ZoneInfo("America/Los_Angeles")  # YouTube's API quota day ends at midnight Pacific (15:00 VN in winter)
QUOTA = 4  # uploads the channel accepted per quota day on 4/11; the 5th got "too many requests"
TAG = "auto_publish"
DANG_BAI = ROOT.parent / "dang_bai"


# ---------------------------------------------------------------- pure helpers (tested)


def read_queue(text: str) -> list[tuple[datetime, str]]:
    """Queue rows as (slot in VN time, job id), earliest first; a malformed row raises instead of being skipped."""
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            slot, job = (x.strip() for x in line.split(","))
            rows.append((datetime.strptime(slot, "%Y-%m-%d %H:%M").replace(tzinfo=VN), job))
        except ValueError as e:
            raise ValueError(f"publish_queue.csv line {n}: {line!r} is not 'YYYY-MM-DD HH:MM,job_id'") from e
    return sorted(rows)


def quota_day_start(at: datetime) -> datetime:
    """Start of the YouTube quota day that contains `at` (midnight Pacific), as an aware datetime."""
    p = at.astimezone(PACIFIC)
    return p.replace(hour=0, minute=0, second=0, microsecond=0)


def promises_next(narration: str) -> bool:
    """The spoken ending names tomorrow's video, so that video must exist before this one goes out. Judged on what is
    said, not the topic note: a topic "Mai: (để trống)" produced no teaser."""
    return bool(re.search(r"\bMai:|Phần 2 mai lên", narration or "", re.I))


def plan(queue, jobs: dict, published: set, used_today: int, at: datetime) -> tuple[list, list]:
    """(to_send [(slot, job_id)], held [(slot, job_id, reason)]). jobs: id -> (status, topic, channel_id), where status
    is "EXPORTED" only for a job that is approved AND ready to send (fresh export, a webhook). Pure, for tests."""
    send, held, seen = [], [], set()
    budget = QUOTA - used_today
    for slot, jid in queue:
        if jid in published:
            continue
        if jid in seen:  # moved to a new slot without removing the old row: never upload one video twice
            held.append((slot, jid, "queued twice: delete the extra row in publish_queue.csv"))
            continue
        seen.add(jid)
        status, topic, channel = jobs.get(jid, ("MISSING", "", None))
        nxt = [j for s, j in queue if s == slot + timedelta(days=1) and j != jid and jobs.get(j, ("", "", None))[2] == channel]
        if status != "EXPORTED":
            held.append((slot, jid, f"not ready ({status})"))
        elif slot <= at + timedelta(minutes=30):
            held.append((slot, jid, "slot has passed or is under 30 min away: pick a new slot"))
        elif promises_next(topic) and not (len(nxt) == 1 and (nxt[0] in published or jobs[nxt[0]][0] == "EXPORTED")):
            held.append((slot, jid, "its 'Mai:'/'Phần 1' video (same channel, next day, same time, exactly one) is not queued and ready yet"))
        elif budget <= 0:
            held.append((slot, jid, "quota for today used up: goes on the next run"))
        else:
            send.append((slot, jid))
            budget -= 1
    return send, held


def slug(title: str) -> str:
    s = unicodedata.normalize("NFKD", title.replace("đ", "d").replace("Đ", "D"))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return "_".join(re.findall(r"[a-z0-9]+", s)[:4]) or "video"


def handoff(dang_bai: Path, job_id: str, export: Path, m: dict, yt_url: str, slot: datetime) -> Path | None:
    """Copy the video and its captions into dang_bai for the hand post; None when this job is already there."""
    dang_bai.mkdir(parents=True, exist_ok=True)
    if any(job_id in f.read_text(encoding="utf-8-sig", errors="replace") for f in dang_bai.glob("*_caption.txt")):
        return None
    n = max((int(x.group(1)) for f in dang_bai.iterdir() if (x := re.match(r"(\d+)_", f.name))), default=0) + 1
    base = f"{n}_{slug(m['title'])}"
    shutil.copyfile(export / "video.mp4", dang_bai / f"{base}.mp4")
    sources = "\n".join(m.get("sources") or [])
    (dang_bai / f"{base}_caption.txt").write_text(
        f"{youtube_title(m)}\n\n--- TikTok ---\n{tiktok_caption(m)}\n\n--- Facebook ---\n{facebook_description(m)}\n\n"
        f"Nguon:\n{sources}\n\nYouTube: {yt_url} (public {slot:%d/%m %H:%M})\njob: {job_id}\n", encoding="utf-8-sig")
    return dang_bai / f"{base}.mp4"


# ---------------------------------------------------------------- side effects


def env_value(name: str) -> str | None:
    """A .env value Settings does not model (the webhook lives in the n8n branch's Settings)."""
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        if line.strip().startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def webhook_for(db, job: ContentJob, default: str | None) -> str | None:
    """The channel's own n8n webhook; a non-Vietnamese channel without one is refused, never sent to the Vietnamese channel."""
    ch = db.get(Channel, job.channel_id) if job.channel_id else None
    own = json.loads(ch.handles_json or "{}").get("n8n_webhook") if ch else None
    if own:
        return own
    return default if (ch is None or ch.language == "vi") else None


def post(url: str, data: dict, video: Path) -> tuple[str, str]:
    """("ok", video_id) | ("not_sent", why) | ("unclear", why). Only a refused connection is sure to have sent nothing."""
    try:
        with video.open("rb") as f:
            r = httpx.post(url, data=data, files={"video": (video.name, f, "video/mp4")}, timeout=600)
    except httpx.ConnectError as e:
        return "not_sent", str(e)
    except Exception as e:  # noqa: BLE001 - a timeout or a dropped stream may still have uploaded
        return "unclear", f"{type(e).__name__}: {e}"
    vid = r.json().get("video_id") if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json") else None
    return ("ok", vid) if vid else ("unclear", f"HTTP {r.status_code}: {r.text[:200]}")


def ready_problem(job_dir: Path) -> str | None:
    """Why an approved job's files cannot be sent, or None. A render newer than the export means a hand fix never exported."""
    video, final = job_dir / "export" / "upload.mp4", job_dir / "render" / "final.mp4"
    if not video.exists() or not (job_dir / "export" / "manifest.json").exists():
        return "no export/upload.mp4: export it first"
    if final.exists() and final.stat().st_mtime > video.stat().st_mtime:
        return "render/final.mp4 is newer than the export (hand-fixed after approval?): re-export first"
    return None


def safe_handoff(s, jid: str, url: str, slot: datetime) -> Path | None:
    """handoff that never stops the run: the YouTube upload already happened, and the next run retries the files."""
    export = s.storage_dir / "jobs" / jid / "export"
    try:
        return handoff(DANG_BAI, jid, export, json.loads((export / "manifest.json").read_text(encoding="utf-8")), url, slot)
    except Exception as e:  # noqa: BLE001
        print(f"handoff failed for {jid} ({type(e).__name__}: {e}); retried on the next run")
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--add", nargs=2, metavar=("'YYYY-MM-DD HH:MM'", "JOB_ID"), help="append a job to the queue and exit")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    s = Settings()
    qfile = s.storage_dir / "publish_queue.csv"
    if args.add:
        read_queue(f"{args.add[0]},{args.add[1]}")  # validate before writing
        with qfile.open("a", encoding="utf-8") as f:
            f.write(f"{args.add[0]},{args.add[1]}\n")
        print(f"queued {args.add[1]} for {args.add[0]}")
        return 0
    lock = (s.storage_dir / "auto_publish.lock").open("a+")
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)  # released by Windows if this process dies
    except OSError:
        print("another auto_publish run holds the lock; exiting")
        return 0
    queue = read_queue(qfile.read_text(encoding="utf-8")) if qfile.exists() else []
    at = datetime.now(VN)
    start_utc = quota_day_start(at).astimezone(timezone.utc).isoformat(timespec="milliseconds")  # same format as db.now()
    default = env_value("N8N_WEBHOOK_URL")
    with make_sessionmaker(s.database_url, create=False)() as db:
        ids = [j for _, j in queue]
        jobs, hooks = {}, {}
        for j in db.scalars(select(ContentJob).where(ContentJob.id.in_(ids))):
            status = j.status
            if status == "EXPORTED":  # ready checks run here, before planning, so a teaser is never sent while its follow-up cannot go
                hooks[j.id] = webhook_for(db, j, default)
                status = ready_problem(s.storage_dir / "jobs" / j.id) or ("no n8n webhook for this channel" if not hooks[j.id] else "EXPORTED")
            script = latest_ok_run(db, j.id, "SCRIPT")
            ending = json.loads(script.output_json)["scenes"][-1]["narration"] if script else j.input_topic
            jobs[j.id] = (status, ending, j.channel_id)
        pubs = list(db.scalars(select(Publication).where(Publication.platform == "youtube_shorts")))
        published = {p.job_id for p in pubs}
        for p in pubs:
            if p.url is None:
                print(f"NEEDS HUMAN CHECK: {p.job_id} upload state unknown ({p.notes}); look in YouTube Studio, then set the url or delete the row")
        if not args.dry_run:  # a hand-off that failed on an earlier run is retried (handoff skips jobs already in dang_bai)
            for p in pubs:
                if p.url and p.notes and p.notes.startswith(f"{TAG}: scheduled"):
                    safe_handoff(s, p.job_id, p.url, datetime.strptime(p.notes[-19:-3], "%Y-%m-%d %H:%M").replace(tzinfo=VN))
        used = sum(1 for p in pubs if p.notes and p.notes.startswith((TAG, "posted by scripts/publish_n8n.py")) and p.created_at >= start_utc)
        send, held = plan(queue, jobs, published, used, at)
        for slot, jid, why in held:
            print(f"held {slot:%d/%m %H:%M} {jid}: {why}")
        if args.dry_run or not send:
            for slot, jid in send:
                print(f"would send {jid} for {slot:%d/%m %H:%M}")
            return 0
        for slot, jid in send:
            job = db.get(ContentJob, jid)
            export = s.storage_dir / "jobs" / jid / "export"
            m = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
            when = slot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            row = Publication(job_id=jid, channel_id=job.channel_id, platform="youtube_shorts", url=None, published_at=when,
                              notes=f"{TAG}: uploading for {slot:%Y-%m-%d %H:%M} VN")
            db.add(row)
            db.commit()  # the placeholder is on disk before any byte leaves, so a crash can never cause a second upload
            state, info = post(hooks[jid], {"title": youtube_title(m), "description": youtube_description(m), "job_id": jid, "publish_at": when},
                               export / "upload.mp4")
            if state != "ok":
                if state == "not_sent":
                    db.delete(row)
                else:
                    row.notes = f"{TAG}: NEEDS HUMAN CHECK, {info}"[:500]
                db.commit()
                # stop the whole run: a later teaser must not go out while this video (maybe its follow-up) is stuck
                print(f"{'not sent' if state == 'not_sent' else 'NEEDS HUMAN CHECK'} {jid}: {info}; stopping this run")
                return 1
            row.url, row.notes = f"https://youtube.com/shorts/{info}", f"{TAG}: scheduled {slot:%Y-%m-%d %H:%M} VN"
            db.commit()
            out = safe_handoff(s, jid, row.url, slot)
            print(f"sent {jid} {row.url} public {slot:%d/%m %H:%M}" + (f"; TikTok/Facebook file: {out.name}" if out else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
