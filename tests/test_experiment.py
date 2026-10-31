"""Experiment agent, offline: fake LLM routed by prompt, fixture voice, real FFmpeg. One round per day."""
import json
import os
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import planner
from app.config import Settings
from app.experiment import OPUS, SONNET, Agent
from app.main import create_app
from app.providers import FixtureTTS, LLMResponse

FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")
SCRIPT = (Path(__file__).parent / "fixtures" / "llm" / "script_short.json").read_text(encoding="utf-8")


class FakeLLM:
    name = "fake"

    def __init__(self, model="claude-sonnet-5-5", calls=None):
        self.model, self.calls = model, calls if calls is not None else []

    async def complete(self, prompt, timeout_s=300):
        self.calls.append((self.model, prompt[:12]))
        if prompt.startswith("TOPIC_PICK"):
            text = json.dumps({"topics": [{"topic": f"Chủ đề {i} về remote điều hoà", "trend": None} for i in range(3)]})
        elif prompt.startswith("JUDGE"):
            labels = sorted(set(re.findall(r"### (S\d+)", prompt)))
            text = json.dumps({"scores": {l: {"hook": 6, "escalation": 6, "payoff": 5, "relatable": 7, "comments": 6, "readable": 8,
                                              "overall": 5 + i % 3, "risks": [], "fix": "sharper twist"} for i, l in enumerate(labels)},
                               "ranking": labels})
        elif prompt.startswith("OPTIMIZE"):
            text = "not json at first" if not any(c[1].startswith("OPTIMIZE") for c in self.calls[:-1]) else json.dumps(
                {"guide": "Format: comedy v2. " + "Write short punchy lines. " * 20, "hook": "split", "why": "split scored best"})
        elif prompt.startswith("SUMMARY"):
            text = json.dumps({"conclusion": "Kết luận thử."})
        else:
            text = SCRIPT
        return LLMResponse(text=text, provider="fake", model=self.model, cost_usd=0.001)


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="FFmpeg not found")
def test_two_day_experiment_runs_judges_optimizes_and_reports(tmp_path, monkeypatch):
    exp = tmp_path / "experiments" / "e1"
    s = Settings(_env_file=None, app_env="experiment", llm_provider="fixture", tts_provider="fixture", stock_provider="none",
                 ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE, storage_dir=tmp_path, prompt_dir=exp / "prompts",
                 database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    monkeypatch.setattr(planner, "fetch_google_trends", lambda geo: [{"title": "trận cầu đêm nay", "summary": None, "source": "google_trends",
                                                                      "source_url": None, "velocity": 60.0}])
    calls = []
    script_llm, tts = FakeLLM(calls=calls), FixtureTTS(FFMPEG)
    tts.rate = "+0%"
    with TestClient(create_app(s, llm=script_llm, tts=tts)) as c:
        c.post("/channels", json={"name": "Viral Entertainment VN", "positioning": "hài"})
        agent = Agent(s, exp, c, script_llm, tts, lambda m, e: FakeLLM(m, calls), day1_rounds=1, day2_rounds=1, hours_apart=0,
                      sleep=lambda x: None)
        report = agent.run()
        st = json.loads((exp / "state.json").read_text(encoding="utf-8"))

        # day 1: three hook styles, three rates, all on Sonnet with guide v1; day 2: a topic three ways
        d1, d2 = st["rounds"][0]["videos"], st["rounds"][1]["videos"]
        assert sorted(v["arm"]["hook"] for v in d1) == ["detail", "shock", "split"] and len({v["arm"]["rate"] for v in d1}) == 3
        assert len(d2) == 6 and {(v["arm"]["guide"], v["arm"]["model"]) for v in d2} == {("v1", SONNET), ("v2", SONNET), ("v2", OPUS)}
        assert all(v.get("judge") and v["status"] in ("AWAITING_APPROVAL", "NEEDS_REVISION") for v in d1 + d2)
        # the optimizer's bad first answer was repaired; v2 carries the safety footer; production prompts untouched
        assert st["best"]["hook"] in ("shock", "split", "detail") and "Write short punchy lines" in st["guide_v2"]
        assert "never from mocking a real person" in (exp / "format_comedy_v2.md").read_text(encoding="utf-8")
        assert "Format: comedy v2" not in (Path(__file__).parent.parent / "app" / "prompts" / "format_comedy.md").read_text(encoding="utf-8")
        # the script prompt really used the experiment's guide: v1 jobs carry the hook line, v2 jobs the new guide
        jobs = {v["job_id"]: v for v in d1 + d2}
        for jid, v in jobs.items():
            run = next(r for r in c.get(f"/jobs/{jid}").json()["runs"] if r["stage"] == "SCRIPT")
            assert run["prompt_version"]
        # nothing was approved or exported by the agent, and the budget ledger adds up
        assert not any(c.get(f"/jobs/{j}").json()["job"]["status"] in ("APPROVED", "EXPORTED") for j in jobs)
        assert agent.spent() > 0 and "Kết luận thử." in report.read_text(encoding="utf-8")
        shortlist = (exp / "shortlist.md").read_text(encoding="utf-8")
        assert shortlist.count("## ") == 9 and d1[0]["job_id"] in shortlist

        # a restart resumes without redoing anything; the cap stops new spending
        again = Agent(s, exp, c, script_llm, tts, lambda m, e: FakeLLM(m, calls), day1_rounds=1, day2_rounds=1, hours_apart=0,
                      sleep=lambda x: None)
        n = len(calls)
        again.run()
        assert len([x for x in calls[n:] if not x[1].startswith("SUMMARY")]) == 0
        capped = Agent(s, tmp_path / "experiments" / "e2", c, script_llm, tts, lambda m, e: FakeLLM(m, calls), day1_rounds=1,
                       day2_rounds=1, hours_apart=0, sleep=lambda x: None, cap_usd=0.0)
        capped.s = s.model_copy(update={"prompt_dir": tmp_path / "experiments" / "e2" / "prompts"})
        capped.dir.mkdir(parents=True)
        capped.run()
        assert capped.state["stopped"] and capped.state["rounds"] == []
