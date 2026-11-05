"""Sprint 2: script review gate, human edits, regeneration with feedback, prompt versions, batch jobs. Offline."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers import FixtureLLM, FixtureTTS, LLMResponse

FIX = Path(__file__).parent / "fixtures" / "llm"


class RecordingLLM(FixtureLLM):
    """Fixture replay that keeps every prompt it was sent."""

    def __init__(self, path):
        super().__init__(path)
        self.prompts: list[str] = []

    async def complete(self, prompt: str, timeout_s: int = 300) -> LLMResponse:
        self.prompts.append(prompt)
        return await super().complete(prompt, timeout_s)


def wait(c: TestClient, job_id: str, timeout_s: float = 60) -> dict:
    end = time.time() + timeout_s
    while time.time() < end:
        d = c.get(f"/jobs/{job_id}").json()
        if d["job"]["status"] not in ("QUEUED", "RUNNING"):
            return d
        time.sleep(0.2)
    raise AssertionError("job did not settle")


@pytest.fixture
def env(tmp_path):
    s = Settings(_env_file=None, app_env="test", llm_provider="fixture", tts_provider="fixture", storage_dir=tmp_path,
                 database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    llm = RecordingLLM(FIX / "script_vi.json")
    with TestClient(create_app(s, llm=llm, tts=FixtureTTS("ffmpeg"))) as c:
        yield c, llm, tmp_path


def test_script_review_pause_records_prompt_version_and_checks(env):
    c, llm, _ = env
    ch = c.post("/channels", json={"name": "A"}).json()["id"]
    idea = c.post("/ideas", json={"channel_id": ch, "title": "Mèo sợ dưa chuột", "format": "facts"}).json()
    job = c.post(f"/ideas/{idea['id']}/jobs").json()
    d = wait(c, job["id"])
    assert d["job"]["status"] == "SCRIPT_REVIEW" and set(d["outputs"]) == {"SCRIPT"}
    run = next(r for r in d["runs"] if r["stage"] == "SCRIPT")
    assert run["prompt_version"].startswith("script@") and "format_facts@" in run["prompt_version"]
    assert "numbered facts list" in llm.prompts[0] and "AI & Technology" not in llm.prompts[0]
    rules = {x["rule"]: x for x in d["outputs"]["SCRIPT"]["checks"]}
    assert {"estimated_duration", "scene_count", "hook_length", "numbers_to_verify"} <= set(rules)
    assert rules["numbers_to_verify"]["passed"] is False  # the fixture narration says "Năm 2026"


def test_format_prompt_differs_per_format(env):
    c, llm, _ = env
    for fmt, marker in (("comedy", "situational comedy"), ("gadget", "product discovery")):
        job = c.post("/jobs", json={"topic": "x y z", "format": fmt, "product_desc": "Đèn ngủ cảm biến" if fmt == "gadget" else None}).json()
        wait(c, job["id"])
        assert marker in llm.prompts[-1]
    assert "Product to feature: Đèn ngủ cảm biến" in llm.prompts[-1]


def test_human_edit_creates_new_run_and_supersedes_old(env):
    c, _, tmp = env
    job_id = c.post("/jobs", json={"topic": "Mèo"}).json()["id"]
    d = wait(c, job_id)
    script = {k: d["outputs"]["SCRIPT"][k] for k in ("title", "caption", "hashtags", "scenes")}
    script["scenes"][0]["narration"] = "Bạn có biết vì sao mèo nhảy dựng lên khi thấy quả dưa chuột không?"
    assert c.put(f"/jobs/{job_id}/script", json={"edited_by": "editor", "script": {"title": "x"}}).status_code == 422
    r = c.put(f"/jobs/{job_id}/script", json={"edited_by": "editor", "script": script})
    assert r.status_code == 200, r.text
    d = c.get(f"/jobs/{job_id}").json()
    runs = [x for x in d["runs"] if x["stage"] == "SCRIPT"]
    assert [x["provider"] for x in runs] == ["fixture", "human"]
    assert runs[0]["superseded_by"] and runs[1]["superseded_by"] is None
    assert d["outputs"]["SCRIPT"]["scenes"][0]["narration"].startswith("Bạn có biết")
    assert (tmp / "jobs" / job_id / "script" / "script-v2.json").is_file()
    assert c.post(f"/jobs/{job_id}/script/approve", json={"approved_by": "founder"}).status_code == 200
    assert c.put(f"/jobs/{job_id}/script", json={"edited_by": "editor", "script": script}).status_code == 409  # locked once approved


def test_regenerate_feeds_notes_and_previous_draft(env):
    c, llm, _ = env
    job_id = c.post("/jobs", json={"topic": "Mèo"}).json()["id"]
    wait(c, job_id)
    assert c.post(f"/jobs/{job_id}/script/regenerate", json={"requested_by": "founder", "notes": "x"}).status_code == 422
    r = c.post(f"/jobs/{job_id}/script/regenerate", json={"requested_by": "founder", "notes": "Hook mạnh hơn, bỏ số liệu năm"})
    assert r.status_code == 200, r.text
    d = wait(c, job_id)
    assert d["job"]["status"] == "SCRIPT_REVIEW"
    assert "Hook mạnh hơn, bỏ số liệu năm" in llm.prompts[-1] and "Previous draft (JSON)" in llm.prompts[-1]
    assert [a["decision"] for a in d["approvals"]] == ["script_rejected"]
    assert len([x for x in d["runs"] if x["stage"] == "SCRIPT"]) == 2
    job = c.post(f"/jobs/{job_id}/script/approve", json={"approved_by": "founder"}).json()
    assert job["operator_notes"] is None  # settled feedback must not steer later drafts


def test_batch_jobs_from_ideas(env):
    c, _, _ = env
    ch = c.post("/channels", json={"name": "A"}).json()["id"]
    ids = [c.post("/ideas", json={"channel_id": ch, "title": f"Ý tưởng {n}", "format": "comedy"}).json()["id"] for n in range(3)]
    r = c.post("/ideas/batch-jobs", json={"idea_ids": ids})
    assert r.status_code == 201, r.text
    jobs = r.json()
    assert [j["idea_id"] for j in jobs] == ids
    assert all(wait(c, j["id"])["job"]["status"] == "SCRIPT_REVIEW" for j in jobs)
    assert c.post("/ideas/batch-jobs", json={"idea_ids": ["idea_missing"]}).status_code == 404
    assert len(c.post("/ideas/batch-jobs", json={"idea_ids": [ids[0], ids[0]]}).json()) == 1  # duplicates queue one job


def test_script_length_follows_the_voice_speaking_rate():
    from app.config import Settings
    from app.pipeline import speech_rate

    rates = {p: speech_rate(Settings(_env_file=None, tts_provider=p), "vi") for p in ("fpt", "edge_tts", "elevenlabs")}
    assert rates["fpt"] > rates["edge_tts"] > rates["elevenlabs"] == 2.2  # a faster voice needs more words for the same length
    assert speech_rate(Settings(_env_file=None, tts_provider="fpt"), "en") == 2.8  # words/s: Edge Andrew read 42 words in 15.1 s


def test_a_script_outside_the_length_window_is_rewritten_once_and_the_closer_draft_kept(tmp_path):
    long = json.loads((FIX / "script_vi.json").read_text(encoding="utf-8"))
    syll = sum(len(sc["narration"].split()) for sc in long["scenes"])  # 178: ~81 s at 2.2 syllables/s, far over 45 s
    short = json.loads(json.dumps(long))
    for sc in short["scenes"]:
        words = sc["narration"].split()
        sc["narration"] = " ".join(words[:len(words) // 2 + 1])
    cut = sum(len(sc["narration"].split()) for sc in short["scenes"])
    assert 36 <= cut / 2.2 <= 54  # the halved draft fits the 45 s window

    class TwoDrafts(RecordingLLM):
        async def complete(self, prompt, timeout_s=300):
            self.prompts.append(prompt)
            return LLMResponse(json.dumps(long if len(self.prompts) == 1 else short, ensure_ascii=False), "fake", "fake", cost_usd=0.01)

    s = Settings(_env_file=None, app_env="test", llm_provider="fixture", tts_provider="fixture", storage_dir=tmp_path,
                 database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    llm = TwoDrafts(FIX / "script_vi.json")
    with TestClient(create_app(s, llm=llm, tts=FixtureTTS("ffmpeg"))) as c:
        job = c.post("/jobs", json={"topic": "x y z", "target_duration_s": 45}).json()
        d = wait(c, job["id"])
    assert len(llm.prompts) == 2 and f"That script has {syll} spoken syllables" in llm.prompts[1] and "Cut about" in llm.prompts[1]
    assert d["outputs"]["SCRIPT"]["syllable_count"] == cut
    run = next(r for r in d["runs"] if r["stage"] == "SCRIPT")
    assert abs(run["cost_usd"] - 0.02) < 1e-9  # both calls are billed to the stage
