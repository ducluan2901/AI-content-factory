"""Two-day script experiment agent: makes comedy videos on a schedule, has a judge model score every script,
rewrites the format guide after day 1, and writes a report. It never approves a final video, exports or posts:
every finished video waits in AWAITING_APPROVAL for the founder.

Day 1 (DAY1_ROUNDS rounds, 3 videos each): hook style x voice rate in a Latin square, the current guide, Sonnet.
After day 1 an optimizer (Opus) rewrites the comedy guide from the scores and critiques (guide v2).
Day 2 (DAY2_ROUNDS rounds, 2 topics each): the same topic three ways: v1 guide + best hook on Sonnet,
v2 guide on Sonnet, v2 guide on Opus. Same topic, so the judge's scores compare like with like.
Defaults: 4 + 3 rounds, 6 h apart, 30 videos, about $1.1 of API calls, done in about 40 hours.

The judge reads scripts only. It cannot hear the voice or see the picture: the voice rate is judged by the
measured duration fit, and the founder listens to the samples the report lists.

Run: scripts/run_experiment.ps1 (its own window; restart it after a reboot and it resumes where it stopped).
"""
import asyncio
import json
import random
import shutil
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import planner
from app.config import ROOT, Settings
from app.main import create_app
from app.providers import AnthropicApiProvider, EdgeTTS, extract_json

HOOKS = {
    "shock": "Open with a blunt, slightly outrageous claim about everyday life, stated with total confidence (no statistics).",
    "split": "Open with a question that splits viewers into two camps they will defend in the comments.",
    "detail": "Open with one oddly specific, concrete detail that makes the viewer think 'wait, what?'.",
}
RATES = ["+0%", "+10%", "+20%"]
SONNET, OPUS = "claude-sonnet-5-5", "claude-opus-5-5"
CAP_USD = 1.20  # founder-approved budget for the whole experiment; the agent stops and reports before crossing it
SAFETY_FOOTER = """
- The humour comes from recognition and exaggeration, never from mocking a real person, group or brand.
- A product may appear naturally inside the situation, but is never pitched or praised.
- No invented statistics, prices, dates or quotes."""
SCORE_KEYS = ["hook", "escalation", "payoff", "relatable", "comments", "readable", "overall"]
APPROVER = "experiment agent (draft script; the founder approves every video)"


def now() -> datetime:
    return datetime.now(timezone.utc)


class Agent:
    def __init__(self, s: Settings, exp_dir: Path, client: TestClient, script_llm, tts, make_llm, *,
                 day1_rounds: int = 4, day2_rounds: int = 3, hours_apart: float = 6, sleep=time.sleep, cap_usd: float = CAP_USD):
        self.s, self.dir, self.c, self.llm, self.tts, self.make_llm = s, exp_dir, client, script_llm, tts, make_llm
        self.day1, self.day2, self.gap, self.sleep, self.cap = day1_rounds, day2_rounds, hours_apart, sleep, cap_usd
        self.state_path = exp_dir / "state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else None

    # ---------------------------------------------------------------- bookkeeping
    def log(self, msg: str) -> None:
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
        print(line, flush=True)
        with (self.dir / "agent.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)  # atomic: a crash mid-write never leaves a half state file

    def spent(self) -> float:
        return round(self.state["llm_cost"] + sum(v.get("cost", 0) for r in self.state["rounds"] for v in r["videos"]), 4)

    def can_spend(self, estimate: float) -> bool:
        if self.spent() + estimate <= self.cap:
            return True
        self.state["stopped"] = f"budget cap ${self.cap} reached at ${self.spent()}"
        self.log(self.state["stopped"])
        return False

    def ask(self, model: str, effort: str, prompt: str, estimate: float) -> dict | None:
        """One judged/planned LLM call with one repair attempt; its cost goes on the experiment's own ledger."""
        if not self.can_spend(estimate):
            return None
        llm = self.make_llm(model, effort)
        for _ in range(2):
            resp = asyncio.run(llm.complete(prompt, timeout_s=self.s.stage_timeout_s))
            self.state["llm_cost"] = round(self.state["llm_cost"] + resp.cost_usd, 5)
            self.save()
            try:
                return extract_json(resp.text)
            except ValueError as e:
                prompt += f"\n\nYour previous answer was not valid JSON ({e}). Return the JSON object only."
        raise RuntimeError("LLM returned invalid JSON twice")

    # ---------------------------------------------------------------- setup
    def start(self) -> None:
        if self.state:
            return
        prompts = self.s.prompt_dir  # this experiment's own copy; production prompts stay untouched
        prompts.mkdir(parents=True, exist_ok=True)
        for f in (ROOT / "app" / "prompts").glob("*.md"):
            shutil.copyfile(f, prompts / f.name)
        ch = next((x for x in self.c.get("/channels").json() if "Entertainment" in x["name"]), None) or self.c.get("/channels").json()[0]
        self.state = {"started_at": now().isoformat(), "channel_id": ch["id"], "channel": f"{ch['name']} | {ch.get('positioning') or ''}",
                      "guide_v1": (prompts / "format_comedy.md").read_text(encoding="utf-8"), "guide_v2": None, "best": None,
                      "rounds": [], "llm_cost": 0.0, "stopped": None, "report": None}
        self.save()
        self.log(f"experiment started in {self.dir}")

    # ---------------------------------------------------------------- one round
    def arms(self, k: int) -> tuple[int, list[dict]]:
        """(topics needed, arms). Each arm has a topic index into the round's topic list."""
        if k < self.day1:
            return 3, [{"topic": i, "hook": h, "rate": RATES[(i + k) % 3], "guide": "v1", "model": SONNET} for i, h in enumerate(HOOKS)]
        b = self.state["best"]
        arms = []
        for t in range(2):
            arms += [{"topic": t, "hook": b["hook"], "rate": b["rate"], "guide": "v1", "model": SONNET},
                     {"topic": t, "hook": None, "rate": b["rate"], "guide": "v2", "model": SONNET},
                     {"topic": t, "hook": None, "rate": b["rate"], "guide": "v2", "model": OPUS}]
        return 2, arms

    def pick_topics(self, n: int) -> list[dict] | None:
        r = self.c.post("/trends/import/google", params={"geo": "VN"})
        self.log(f"google trends import: {r.status_code} {r.text[:120]}")
        since = (now() - timedelta(hours=36)).isoformat()
        trends = [t["title"] for t in self.c.get("/trends", params={"market": "vn"}).json()
                  if t["source"] == "google_trends" and t["observed_at"] >= since][:20]
        used = [t["topic"] for r in self.state["rounds"] for t in r["topics"]]
        out = self.ask(SONNET, "low", f"""TOPIC_PICK. You plan videos for a Vietnamese TikTok comedy channel: {self.state['channel']}.
Audience: Vietnamese viewers aged 18-35 scrolling TikTok, Reels and Shorts.

Today's Google search trends in Vietnam: {json.dumps(trends, ensure_ascii=False)}

Pick {n} different situational-comedy topics. Each is one Vietnamese sentence describing an everyday situation with a
built-in conflict (family, roommates, office, school, couples, group chats, shopping, traffic, weather, food).
You may ride a trend when it suits light comedy (a big match night, the weather, a holiday, a viral product, a show),
seen through an ordinary family or friend group. Never: tragedy, crime, accidents, disasters, politics, religion, health
scares, or real named people. Do not repeat these used topics: {json.dumps(used, ensure_ascii=False)}

Return ONLY JSON: {{"topics": [{{"topic": "...", "trend": "<the trend title it rides, or null>"}}]}}""", 0.02)
        return None if out is None else out["topics"][:n]

    def write_guide(self, arm: dict) -> None:
        if arm["guide"] == "v1":
            text = self.state["guide_v1"].rstrip() + f"\n\nHook style for this video (use it instead of the hook examples above): {HOOKS[arm['hook']]}\n"
        else:
            text = self.state["guide_v2"].rstrip() + "\n" + SAFETY_FOOTER + "\n"
        (self.s.prompt_dir / "format_comedy.md").write_text(text, encoding="utf-8")

    def wait(self, jid: str, timeout_s: int = 1500) -> dict:
        end = time.time() + timeout_s
        while time.time() < end:
            d = self.c.get(f"/jobs/{jid}").json()
            if d["job"]["status"] not in ("QUEUED", "RUNNING"):
                return d
            self.sleep(2)
        raise RuntimeError(f"job {jid} did not finish in {timeout_s}s")

    def make_video(self, topic: str, arm: dict) -> dict:
        self.write_guide(arm)
        self.llm.model, self.tts.rate = arm["model"], arm["rate"]
        jid = self.c.post("/jobs", json={"topic": topic, "format": "comedy", "channel_id": self.state["channel_id"], "language": "vi",
                                         "review_script": False, "burn_subtitles": False}).json()["id"]
        d = self.wait(jid)
        for _ in range(2):  # the free voice endpoint drops out for minutes at a time: wait, then retry from the voice stage
            if d["job"]["status"] == "FAILED" and "VOICE" in (d["job"]["error_summary"] or ""):
                self.log(f"  {jid}: voice failed, retrying in 90 s")
                self.sleep(90)
                self.c.post(f"/jobs/{jid}/run", json={"from_stage": "VOICE"})
                d = self.wait(jid)
        out, job = d["outputs"], d["job"]
        v = {"job_id": jid, "arm": arm, "topic": topic, "status": job["status"], "error": job["error_summary"], "cost": job["total_cost_usd"],
             "voice_failures": sum(1 for r in d["runs"] if r["stage"] == "VOICE" and r["status"] == "FAILED")}
        if "SCRIPT" in out:
            v["script"] = {k: out["SCRIPT"][k] for k in ("title", "caption", "hashtags", "scenes")}
        if "RENDER" in out:
            v |= {"duration_s": out["RENDER"]["duration_s"], "cards": len(out["VISUALS"]["assets"]), "stock_hits": out["VISUALS"]["stock_hits"],
                  "rate_observed": out["VOICE"]["syllables_per_s_observed"],
                  "window_ok": next(c["passed"] for c in out["QUALITY"]["checks"] if c["rule"] == "duration_window"),
                  "video_path": out["RENDER"]["video_path"]}
        self.log(f"  {jid} [{arm['guide']} {arm['hook'] or '-'} {arm['rate']} {arm['model'][7:]}] -> {v['status']} "
                 f"{v.get('duration_s', '-')}s ${v['cost']:.4f}")
        return v

    def judge(self, rnd: dict) -> None:
        vids = [v for v in rnd["videos"] if "script" in v]
        if not vids:
            return
        order = list(range(len(vids)))
        random.Random(rnd["no"]).shuffle(order)  # blind and position-shuffled: the judge never sees the arm
        labels = {f"S{i + 1}": vids[j] for i, j in enumerate(order)}
        body = "\n\n".join(f"### {lab}\nTopic: {v['topic']}\nMeasured length: {v.get('duration_s', 'not rendered')} s (target 20-35 s)\n"
                           f"{json.dumps(v['script'], ensure_ascii=False)}" for lab, v in labels.items())
        out = self.ask(OPUS, "medium", f"""JUDGE. You are a senior short-video editor for Vietnamese TikTok comedy ({self.state['channel']}).
Audience: Vietnamese viewers aged 18-35. Each script is shown one sentence per full-screen card, read by a voice.
Score every script independently, 1-10 per criterion, as a strict editor who has seen thousands of these. A 7 is a
video you would post; a 9 is rare.
- hook: would the first sentence stop a scroll within 2 seconds?
- escalation: does each beat raise the stakes or the absurdity?
- payoff: does the ending land a surprise that recasts what came before?
- relatable: would a Vietnamese viewer say "đúng nhà mình" and send it to someone?
- comments: will people actually comment (pick a side, tag someone), not just like?
- readable: is every sentence short and clear enough to read on its own card in 2 seconds?
- overall: would it hold viewers to the end and get shared?
List any risk (mocking a group, invented facts, platform-policy issues) and the single most useful fix.

{body}

Return ONLY JSON: {{"scores": {{"S1": {{"hook": 0, "escalation": 0, "payoff": 0, "relatable": 0, "comments": 0, "readable": 0,
"overall": 0, "risks": [], "fix": "..."}}}}, "ranking": ["best label first"]}}""", 0.12)
        if out is None:
            return
        for lab, v in labels.items():
            v["judge"] = out["scores"].get(lab)
            v["rank"] = out["ranking"].index(lab) + 1 if lab in out.get("ranking", []) else None
        self.save()

    def run_round(self, k: int) -> bool:
        if k >= len(self.state["rounds"]):
            n, arms = self.arms(k)
            topics = self.pick_topics(n)
            if topics is None:
                return False
            self.state["rounds"].append({"no": k + 1, "day": 1 if k < self.day1 else 2, "topics": topics, "arms": arms, "videos": [], "judged": False})
            self.save()
            self.log(f"round {k + 1}: " + " | ".join(t["topic"] for t in topics))
        rnd = self.state["rounds"][k]
        for arm in rnd["arms"][len(rnd["videos"]):]:  # resumes mid-round after a restart
            if not self.can_spend(0.04 if arm["model"] == OPUS else 0.015):
                return False
            rnd["videos"].append(self.make_video(rnd["topics"][arm["topic"]]["topic"], arm) | {"day": rnd["day"]})
            self.save()
        if not rnd["judged"]:
            self.judge(rnd)
            rnd["judged"] = True
            self.save()
        return not self.state["stopped"]

    # ---------------------------------------------------------------- between the days
    def optimize(self) -> None:
        day1 = [v for r in self.state["rounds"] if r["day"] == 1 for v in r["videos"]]
        by_hook = {h: mean(v["judge"]["overall"] for v in day1 if v["arm"]["hook"] == h and v.get("judge")) for h in HOOKS}
        fit = {r: mean(1.0 if v.get("window_ok") else 0.0 for v in day1 if v["arm"]["rate"] == r and "window_ok" in v) for r in RATES}
        best_rate = max(RATES, key=lambda r: (fit[r] if fit[r] is not None else -1, r == "+10%"))
        rows = [{"hook": v["arm"]["hook"], "scores": v.get("judge"), "narration": [s["narration"] for s in v["script"]["scenes"]]}
                for v in day1 if "script" in v]
        out = self.ask(OPUS, "high", f"""OPTIMIZE. You improve the format guide that a script model follows to write Vietnamese TikTok
comedy for {self.state['channel']}. Below is the current guide and day 1 of an experiment: every script with the hook
style it was told to use, the editor's scores (1-10) and the editor's suggested fix.

Mean overall score by hook style: {json.dumps(by_hook)}

Current guide:
<guide>
{self.state['guide_v1']}
</guide>

Day 1 results:
{json.dumps(rows, ensure_ascii=False)}

Write guide v2: keep what the high scorers did, forbid what the low scorers did, and turn the editor's repeated fixes
into rules. Be concrete (show the shape of a good line, not adjectives). At most 2500 characters, in English, aimed at
the script model. Choose the hook style the guide should use from: {list(HOOKS)}.
Return ONLY JSON: {{"guide": "...", "hook": "shock|split|detail", "why": "two sentences"}}""", 0.15)
        best_hook = max(by_hook, key=lambda h: by_hook[h] or 0)
        if out and isinstance(out.get("guide"), str) and 200 < len(out["guide"]) < 4000:
            self.state["guide_v2"] = out["guide"] + f"\n\nHook style: {HOOKS.get(out.get('hook'), HOOKS[best_hook])}"
            why = out.get("why")
        else:
            self.state["guide_v2"] = self.state["guide_v1"] + f"\n\nHook style: {HOOKS[best_hook]}"
            why = "optimizer gave no usable guide; v2 is v1 with the best hook"
        self.state["best"] = {"hook": best_hook, "rate": best_rate, "by_hook": by_hook, "fit_by_rate": fit, "why": why}
        (self.dir / "format_comedy_v2.md").write_text(self.state["guide_v2"].rstrip() + "\n" + SAFETY_FOOTER + "\n", encoding="utf-8")
        self.save()
        self.log(f"guide v2 written; best hook {best_hook}, best rate {best_rate}")

    # ---------------------------------------------------------------- report
    def report(self) -> Path:
        vids = [v for r in self.state["rounds"] for v in r["videos"]]
        judged = [v for v in vids if v.get("judge")]

        def table(rows, key, title):
            groups = {}
            for v in rows:
                groups.setdefault(key(v), []).append(v)
            lines = [f"| {title} | videos | " + " | ".join(SCORE_KEYS) + " |", "|---|---|" + "---|" * len(SCORE_KEYS)]
            for g, vs in sorted(groups.items(), key=lambda kv: -(mean(v["judge"]["overall"] for v in kv[1]) or 0)):
                lines.append(f"| {g} | {len(vs)} | " + " | ".join(f"{mean(v['judge'][s] for v in vs):.1f}" for s in SCORE_KEYS) + " |")
            return "\n".join(lines)

        rated = [v for v in vids if "window_ok" in v]
        by_rate = "\n".join(f"| {r} | {len(vs)} | {mean(1.0 if v['window_ok'] else 0.0 for v in vs):.0%} | "
                            f"{mean(v['rate_observed'] for v in vs):.2f} | {', '.join(v['job_id'] for v in vs[:2])} |"
                            for r in RATES if (vs := [v for v in rated if v["arm"]["rate"] == r]))
        pairs = []
        for r in self.state["rounds"]:
            if r["day"] == 2:
                for t in range(len(r["topics"])):
                    trio = {f"{v['arm']['guide']}-{v['arm']['model'][7:]}": v for v in r["videos"] if v["arm"]["topic"] == t and v.get("judge")}
                    if len(trio) == 3:
                        pairs.append({k: v["judge"]["overall"] for k, v in trio.items()})
        wins = {k: sum(1 for p in pairs if p[k] == max(p.values())) for k in ("v1-sonnet-5-5", "v2-sonnet-5-5", "v2-opus-5-5")}
        top = sorted(judged, key=lambda v: (-v["judge"]["overall"], v.get("rank") or 9))[:5]
        failures = {"voice_failures": sum(v["voice_failures"] for v in vids), "videos_failed": sum(1 for v in vids if v["status"] == "FAILED"),
                    "outside_window": sum(1 for v in rated if not v["window_ok"])}
        summary = self.ask(OPUS, "medium", f"""SUMMARY. Write the conclusion of a two-day experiment for the founder of a Vietnamese TikTok comedy
channel, in Vietnamese, plain and short: what won, how sure we can be given the sample sizes, what to change in
production now, and what to test next. Mention that scores come from a model editor, not real viewers.
Data: {json.dumps({"best": self.state["best"], "day2_wins": wins, "pairs": pairs, "failures": failures,
                   "mean_overall": mean(v["judge"]["overall"] for v in judged), "spent_usd": self.spent()}, ensure_ascii=False)}
Return ONLY JSON: {{"conclusion": "markdown, at most 250 words"}}""", 0.06) if judged else None
        md = [f"# Thử nghiệm kịch bản 2 ngày\n\nBắt đầu {self.state['started_at']}, {len(vids)} video, chi phí API ${self.spent()} "
              f"(giới hạn ${self.cap}).{' Dừng sớm: ' + self.state['stopped'] if self.state['stopped'] else ''}\n",
              "## Kết luận\n", (summary or {}).get("conclusion", "(không có)"), "\n## Ngày 1: kiểu câu mở\n", table([v for v in judged if v["day"] == 1], lambda v: v["arm"]["hook"], "hook"),
              "\n## Ngày 2: cùng chủ đề, ba phiên bản\n",
              table([v for v in judged if v["day"] == 2], lambda v: f"{v['arm']['guide']} {v['arm']['model'][7:]}", "version"),
              f"\nSố lần thắng theo chủ đề: {json.dumps(wins)}\n",
              "\n## Tốc độ giọng (bạn nghe thử các video mẫu)\n", "| tốc độ | video | vừa khung | âm tiết/giây | nghe thử |\n|---|---|---|---|---|\n" + by_rate,
              "\n## Top 5 video (chờ bạn duyệt trong console)\n",
              "\n".join(f"{i}. **{v['script']['title']}** · {v['judge']['overall']}/10 · `{v['job_id']}` · {v['judge'].get('fix', '')}" for i, v in enumerate(top, 1)),
              f"\n## Lỗi\n\n{json.dumps(failures)}\n",
              "\n## Guide v2\n\nFile `format_comedy_v2.md` trong thư mục thử nghiệm. Chưa áp dụng vào sản xuất: bạn duyệt rồi mới thay.\n"]
        path = self.dir / "report.md"
        path.write_text("\n".join(md), encoding="utf-8")
        self.state["report"] = str(path)
        self.save()
        self.log(f"report written: {path}")
        return path

    # ---------------------------------------------------------------- the schedule
    def run(self) -> Path:
        self.start()
        total = self.day1 + self.day2
        start = datetime.fromisoformat(self.state["started_at"])
        for k in range(total):
            if self.state["stopped"]:
                break
            if k < len(self.state["rounds"]) and self.state["rounds"][k]["judged"]:
                continue
            due = start + timedelta(hours=self.gap * k)
            while (wait := (due - now()).total_seconds()) > 0:
                self.log(f"round {k + 1} due at {due.astimezone().strftime('%Y-%m-%d %H:%M')}, waiting")
                self.sleep(min(wait, 600))
            if k == self.day1 and not self.state["guide_v2"]:
                self.optimize()
            if not self.run_round(k):
                break
        return self.report()


def mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def main() -> None:
    s = Settings()
    exp_id = sys.argv[1] if len(sys.argv) > 1 else "exp-" + datetime.now().strftime("%Y%m%d")
    exp_dir = s.storage_dir.resolve() / "experiments" / exp_id
    exp_dir.mkdir(parents=True, exist_ok=True)
    key = s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None
    s = s.model_copy(update={"app_env": "experiment", "prompt_dir": exp_dir / "prompts"})
    script_llm = AnthropicApiProvider(SONNET, key, s.script_effort)
    tts = EdgeTTS({"vi": s.tts_voice_vi, "en": s.tts_voice_en}, s.tts_rate)
    with TestClient(create_app(s, llm=script_llm, tts=tts)) as c:
        print(Agent(s, exp_dir, c, script_llm, tts, lambda m, e: AnthropicApiProvider(m, key, e)).run())


if __name__ == "__main__":
    main()
