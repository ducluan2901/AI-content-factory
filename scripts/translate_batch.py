"""Queue translations of finished jobs for the global channels, through the running API (scripts/run_api.ps1).

    python scripts/translate_batch.py --jobs job_a job_b --channels ch_es ch_de
    python scripts/translate_batch.py --jobs job_a --notes "Next: your cat" --no-review   # skip the script gate

Without --channels every active channel whose language is not vi or en gets one. Each translation stops at
SCRIPT_REVIEW (unless --no-review) and then AWAITING_APPROVAL like any job; nothing is posted.
"""
import argparse

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--jobs", nargs="+", required=True, help="finished source job ids (script approved, video rendered)")
ap.add_argument("--channels", nargs="*", help="target channel ids; default: every active channel not in vi/en")
ap.add_argument("--notes", help="passed to the translator, e.g. 'Next: your cat' for the closing line")
ap.add_argument("--no-review", action="store_true", help="do not pause at SCRIPT_REVIEW (the fidelity check still shows in the job)")
ap.add_argument("--api", default="http://127.0.0.1:8000")
a = ap.parse_args()

with httpx.Client(base_url=a.api, timeout=30) as c:
    channels = a.channels or [ch["id"] for ch in c.get("/channels").json() if ch["status"] == "active" and ch["language"] not in ("vi", "en")]
    for job in a.jobs:
        for ch in channels:
            r = c.post("/jobs", json={"source_job_id": job, "channel_id": ch, "notes": a.notes, "review_script": not a.no_review})
            print(job, ch, r.json()["id"] if r.status_code == 201 else f"{r.status_code} {r.text}")
