"""Build a file of topics one job at a time through the running API, stopping at a cost cap.

One topic per line (blank lines and # comments skipped). Jobs run with the .env defaults and end at
AWAITING_APPROVAL (or NEEDS_REVISION): a person still reviews and approves every video in the console.
A ledger next to the topics file (<file>.jobs.json) maps each topic to its job, so a re-run skips what was sent.

    .venv\\Scripts\\python scripts\\build_batch.py topics.txt --cap 2.00
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000"


def topics_from(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def wait(c: httpx.Client, jid: str, timeout_s: int = 1500) -> dict:
    end = time.time() + timeout_s
    while time.time() < end:
        d = c.get(f"/jobs/{jid}").json()
        if d["job"]["status"] not in ("QUEUED", "RUNNING"):
            return d
        time.sleep(5)
    raise TimeoutError(f"{jid} still running after {timeout_s}s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("topics", type=Path)
    ap.add_argument("--cap", type=float, default=2.0, help="stop starting new jobs once this many USD are spent (default 2.00)")
    ap.add_argument("--per-job", type=float, default=0.25, help="USD to keep free for the next job before starting it")
    args = ap.parse_args()
    ledger_path = args.topics.with_suffix(".jobs.json")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    spent = 0.0
    with httpx.Client(base_url=API, timeout=30) as c:
        try:
            c.get("/health").raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"API not reachable at {API} ({e}); start it with scripts\\run_api.ps1")
            return 1
        # the cap covers the whole topics file across re-runs, not each run
        spent = sum(c.get(f"/jobs/{jid}").json()["job"].get("total_cost_usd") or 0.0 for jid in ledger.values())
        if ledger:
            print(f"${spent:.2f} already spent on {len(ledger)} job(s) from this file")
        for topic in topics_from(args.topics.read_text(encoding="utf-8")):
            if topic in ledger:
                print(f"skip (already job {ledger[topic]}): {topic[:60]}")
                continue
            if spent + args.per_job > args.cap:
                print(f"cap reached: ${spent:.2f} spent of ${args.cap:.2f}; stopping before: {topic[:60]}")
                break
            r = c.post("/jobs", json={"topic": topic, "format": "facts", "review_script": False})
            r.raise_for_status()
            jid = r.json()["id"]
            ledger[topic] = jid
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")  # saved before waiting: a crash never re-sends
            try:
                d = wait(c, jid)
            except TimeoutError as e:  # still running on the worker: its cost is unknown, so start nothing more
                print(f"{e}; stopping, re-run later to continue")
                break
            cost = d["job"].get("total_cost_usd") or 0.0
            spent += cost
            qa = next((x for x in d["outputs"].get("QUALITY", {}).get("checks", []) if x["rule"] == "qa_review"), {})
            print(f"{d['job']['status']} {jid} ${cost:.3f} | {d['outputs'].get('SCRIPT', {}).get('title', '')} | QA {qa.get('actual', '-')}")
    print(f"total ${spent:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
