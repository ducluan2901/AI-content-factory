"""Run the QA reviewer on finished jobs, or on any video file with a job's script (a hand-fixed re-render).

    .venv\\Scripts\\python scripts\\qa_review.py --job <job id> [--job ...] [--video path\\to\\final.mp4]

Prints the review; writes nothing to the database."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anthropic  # noqa: E402

from app import qa_review  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import latest_ok_run, make_sessionmaker  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", action="append", required=True)
    ap.add_argument("--video", help="review this file instead of the job's render (only with one --job)")
    args = ap.parse_args()
    s = Settings()
    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key.get_secret_value(), max_retries=2)
    total = 0.0
    with make_sessionmaker(s.database_url, create=False)() as db:
        for job_id in args.job:
            up = {n: json.loads(latest_ok_run(db, job_id, n).output_json) for n in ("SCRIPT", "VOICE", "RENDER")}
            video = args.video or str(s.storage_dir / up["RENDER"]["video_path"])
            frames = qa_review.scene_frames(s.ffmpeg_path, video, up["VOICE"]["scenes"])
            data, cost = await qa_review.review(client, s.qa_review_model, up["SCRIPT"]["scenes"], up["SCRIPT"].get("sources", []), frames)
            total += cost
            print(f"== {job_id} ({up['SCRIPT']['title']}) ${cost:.4f}")
            print(json.dumps(qa_review.as_check(data), ensure_ascii=False, indent=1))
            if data:
                print(json.dumps(data, ensure_ascii=False))
    print(f"total ${total:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
