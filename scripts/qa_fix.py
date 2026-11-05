"""Auto-fix the scenes the QA reviewer flags: new clips for those scenes only, then re-render through the API.

For each job: run the QA reviewer on the current render; for every flagged scene, search stock again with the
reviewer's suggested query (never a clip this video already uses, nor an over-used one), and swap that scene's
clips in place: same timing, so voice and subtitles still line up, and the good scenes keep their clips. The
swapped VISUALS is saved as a new stage run, and the job re-runs from SUBTITLES on the running API worker
(render, quality, QA again), ending at AWAITING_APPROVAL. Nothing is approved; a person still decides.

    .venv\\Scripts\\python scripts\\qa_fix.py --job job_01... [--job ...] [--dry-run]

One round per run (~$0.01-0.02 QA + Haiku clip checks); run it again for a second round if QA still flags scenes.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anthropic  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app import qa_review  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import ContentJob, StageRun, latest_ok_run, make_sessionmaker  # noqa: E402
from app.pipeline import STAGE_NAMES, rel, supersede_from  # noqa: E402
from app.providers import build_stock  # noqa: E402

API = "http://127.0.0.1:8000"
OVERUSED = {"131824"}  # chow by a waterfall: Pixabay returns it for half the dog queries; viewers see it in every video


def swap_scene(assets: list[dict], scene_index: int, clips: list, stock, s) -> list[dict]:
    """The scene's shots keep their timing and order; each gets one of the new clips, cycling. Pure apart from rel()."""
    out, k = [], 0
    for a in assets:
        if a["scene_index"] != scene_index or a.get("kind") != "stock_video":
            out.append(a)
            continue
        clip = clips[k % len(clips)]
        step_s = (a["end_ms"] - a["start_ms"]) / 1000
        out.append(a | {"source_provider": stock.name, "source_url": clip.source_url, "source_id": clip.source_id, "author": clip.author,
                        "licence": stock.licence, "licence_url": stock.licence_url, "attribution_text": stock.attribution(clip),
                        "path": rel(s, clip.path), "clip_offset_s": round(min(0.5 + k // len(clips) * step_s, max(0.0, (clip.duration_s or 0) - step_s)), 2)})
        k += 1
    return out


def drop_overused(assets: list[dict], scene_index: int) -> list[dict] | None:
    """The scene's over-used shots re-pointed at its other clips; None when the scene has no over-used shot or no other clip."""
    shots = [a for a in assets if a["scene_index"] == scene_index and a.get("kind") == "stock_video"]
    others = list({a["source_id"]: a for a in shots if str(a["source_id"]) not in OVERUSED}.values())
    if not others or len(others) == len(shots):
        return None
    k, out = 0, []
    for a in assets:
        if a in shots and str(a["source_id"]) in OVERUSED:
            o = others[k % len(others)]
            out.append(a | {x: o[x] for x in ("source_provider", "source_url", "source_id", "author", "licence", "licence_url", "attribution_text", "path")}
                       | {"clip_offset_s": round(o.get("clip_offset_s", 0.5) + 2.0, 2)})  # a later part of the clip, so it does not look repeated
            k += 1
        else:
            out.append(a)
    return out


async def fix(job_id: str, s: Settings, sm, client, dry: bool, manual: list[dict] | None = None) -> str:
    with sm() as db:
        up = {n: latest_ok_run(db, job_id, n) for n in ("SCRIPT", "VOICE", "VISUALS", "RENDER")}
        if not all(up.values()):
            return f"{job_id}: not rendered yet"
        out = {n: json.loads(r.output_json) for n, r in up.items()}
    if manual:  # a person named the scenes: no reviewer call (works while the API is unavailable)
        check, cost, flagged = {"actual": "manual"}, 0.0, list(manual)
    else:
        frames = await asyncio.to_thread(qa_review.scene_frames, s.ffmpeg_path, str(s.storage_dir / out["RENDER"]["video_path"]), out["VOICE"]["scenes"])
        data, cost = await qa_review.review(client, s.qa_review_model, out["SCRIPT"]["scenes"], out["SCRIPT"].get("sources", []), frames)
        check = qa_review.as_check(data)
        flagged = check.get("scenes") or []
    # rule, not model judgement: an over-used clip is replaced whatever the reviewer says (it once scored such a video 9/10)
    for i in sorted({a["scene_index"] for a in out["VISUALS"]["assets"] if str(a.get("source_id")) in OVERUSED}):
        if all(f["scene"] != i + 1 for f in flagged):
            flagged.append({"scene": i + 1, "issue": "over-used clip", "query": ""})
    if not flagged:
        return f"{job_id}: QA {check['actual']} - no scene to fix (${cost:.3f})"
    stock = build_stock(s)
    if stock is None or not hasattr(stock, "search_videos"):
        return f"{job_id}: no stock video provider configured"
    stock.used |= {str(a.get("source_id")) for a in out["VISUALS"]["assets"] if a.get("source_id")} | OVERUSED
    assets, notes = out["VISUALS"]["assets"], []
    for f in flagged:
        i = f["scene"] - 1
        if not 0 <= i < len(out["SCRIPT"]["scenes"]):
            continue
        sc = out["SCRIPT"]["scenes"][i]
        clips, query = [], ""
        for query in dict.fromkeys(q for q in (f["query"], sc.get("visual_query") or sc["on_screen_text"]) if q):  # reviewer's query, then the writer's
            if clips := await stock.search_videos(query):
                break
        if clips:
            assets = swap_scene(assets, i, clips, stock, s)
            notes.append(f"scene {f['scene']}: '{query}' -> {', '.join(c.source_id for c in clips)}")
        elif keep := drop_overused(assets, i):
            assets = keep
            notes.append(f"scene {f['scene']}: nothing new on stock; over-used clip replaced by the scene's other clips")
        else:
            notes.append(f"scene {f['scene']}: nothing new for '{query}', kept")
    check_cost = getattr(getattr(stock, "check", None), "cost_usd", 0.0)
    if assets == out["VISUALS"]["assets"]:
        return f"{job_id}: QA flagged {len(flagged)} scene(s) but stock had nothing new; fix by hand. " + "; ".join(notes)
    if dry:
        return f"{job_id}: would swap " + "; ".join(notes)
    with sm() as db:
        job = db.get(ContentJob, job_id)
        if job.status not in ("AWAITING_APPROVAL", "NEEDS_REVISION"):
            return f"{job_id}: is {job.status}, not touched"
        now_ids = {n: getattr(latest_ok_run(db, job_id, n), "id", None) for n in up}
        if now_ids != {n: r.id for n, r in up.items()}:  # re-run by someone while we searched: our swap is based on old runs
            return f"{job_id}: changed since the review started, not touched; run qa_fix again"
        old = latest_ok_run(db, job_id, "VISUALS")
        token = f"qa_fix@{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
        supersede_from(db, job_id, "VISUALS", token)
        attempt = (db.scalar(select(func.max(StageRun.attempt)).where(StageRun.job_id == job_id, StageRun.stage == "VISUALS")) or 0) + 1
        db.add(StageRun(job_id=job_id, stage="VISUALS", attempt=attempt, status="OK", input_json=old.input_json,
                        output_json=json.dumps(out["VISUALS"] | {"assets": assets, "qa_fix": notes}, ensure_ascii=False),
                        provider=old.provider, model=old.model, cost_usd=round(cost + check_cost, 6)))
        job.total_cost_usd = (job.total_cost_usd or 0) + cost + check_cost
        job.status = "NEEDS_REVISION"  # the only way back into the queue; the API run below sets it to QUEUED
        db.commit()
    try:
        httpx.post(f"{API}/jobs/{job_id}/run", json={"from_stage": "SUBTITLES"}, timeout=30).raise_for_status()
    except httpx.HTTPError as e:  # the swap is saved and the job waits as NEEDS_REVISION; one click finishes it
        return f"{job_id}: clips swapped, but the re-render request failed ({e}); re-run it from SUBTITLES in the console. " + "; ".join(notes)
    return f"{job_id}: re-rendering from SUBTITLES; " + "; ".join(notes)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--job", action="append", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scene", type=int, action="append", help="fix this scene (1-based) without the QA reviewer; pair with --query")
    ap.add_argument("--query", action="append", help="English stock search for the matching --scene")
    args = ap.parse_args()
    if (args.scene or args.query) and (len(args.job) != 1 or len(args.scene or []) != len(args.query or [])):
        ap.error("--scene/--query come in pairs and need exactly one --job")
    manual = [{"scene": n, "issue": "chosen by hand", "query": q} for n, q in zip(args.scene or [], args.query or [])]
    s = Settings()
    assert STAGE_NAMES.index("VISUALS") < STAGE_NAMES.index("SUBTITLES")
    sm = make_sessionmaker(s.database_url, create=False)
    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key.get_secret_value(), max_retries=2)
    if not args.dry_run:
        try:  # fail before touching the DB if the worker that re-renders is not up
            httpx.get(f"{API}/health", timeout=5).raise_for_status()
        except httpx.HTTPError as e:
            print(f"API not reachable at {API} ({e}); start it with scripts\\run_api.ps1, nothing was changed")
            return 1
    for jid in args.job:
        try:
            print(await fix(jid, s, sm, client, args.dry_run, manual))
        except Exception as e:  # noqa: BLE001 - one broken job must not stop the others
            print(f"{jid}: failed ({type(e).__name__}: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
