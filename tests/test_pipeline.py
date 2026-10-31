"""Offline end-to-end tests (fixture LLM + silent fixture TTS, real FFmpeg). Covers PRD S1 shape, S2, S4."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app import media
from app.config import Settings
from app.db import ContentJob, make_sessionmaker
from app.main import create_app
from app.providers import ClaudeCliProvider, FixtureLLM, FixtureTTS, ProviderError

FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")
FIX = Path(__file__).parent / "fixtures" / "llm"
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="FFmpeg not found; set FFMPEG_PATH / FFPROBE_PATH")


def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, app_env="test", llm_provider="fixture", tts_provider="fixture", ffmpeg_path=FFMPEG or "ffmpeg",
                    ffprobe_path=FFPROBE or "ffprobe", storage_dir=tmp_path, database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}")


def run_to_rest(client: TestClient, job_id: str, timeout_s: float = 180) -> dict:
    end = time.time() + timeout_s
    while time.time() < end:
        d = client.get(f"/jobs/{job_id}").json()
        if d["job"]["status"] not in ("QUEUED", "RUNNING"):
            return d
        time.sleep(0.5)
    raise AssertionError("job did not finish")


@needs_ffmpeg
def test_happy_path_requires_approval_then_exports(tmp_path):
    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(s.ffmpeg_path))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "Tại sao AI agent sẽ thay đổi cách làm việc năm 2026"}).json()["id"]
        d = run_to_rest(c, job_id)
        assert d["job"]["status"] == "AWAITING_APPROVAL", d["job"]["error_summary"]
        r = d["outputs"]["RENDER"]
        assert (r["width"], r["height"], r["video_codec"], r["audio_codec"]) == (1080, 1920, "h264", "aac")
        assert 45 <= r["duration_s"] <= 60
        srt = (tmp_path / d["outputs"]["SUBTITLES"]["srt_path"]).read_text(encoding="utf-8")
        assert "AI agent" in srt and "đồng nghiệp" in srt  # diacritics survive the round trip

        # S4: no export without a human decision; approval needs a name
        assert c.post(f"/jobs/{job_id}/approve", json={"approved_by": " "}).status_code == 409
        ok = c.post(f"/jobs/{job_id}/approve", json={"approved_by": "founder", "notes": "ok"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["job"]["status"] == "EXPORTED"
        assert {"video.mp4", "vi.srt", "caption.txt", "manifest.json", "thumbnail.jpg"} <= set(ok.json()["export"]["files"])
        manifest = json.loads((tmp_path / "jobs" / job_id / "export" / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["approval"]["approved_by"] == "founder" and manifest["ai_generated"] is True
        assert c.post(f"/jobs/{job_id}/approve", json={"approved_by": "x"}).status_code == 409  # cannot approve twice


@needs_ffmpeg
def test_quality_gate_catches_short_script(tmp_path):  # PRD S2
    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(FIX / "script_short.json"), tts=FixtureTTS(s.ffmpeg_path))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "short"}).json()["id"]
        d = run_to_rest(c, job_id)
        assert d["job"]["status"] == "NEEDS_REVISION"
        failed = [x["rule"] for x in d["outputs"]["QUALITY"]["checks"] if not x["passed"] and x["severity"] == "error"]
        assert failed == ["duration_window"]
        assert c.post(f"/jobs/{job_id}/approve", json={"approved_by": "founder"}).status_code == 409
        # re-run from VOICE keeps the script run, supersedes the rest
        assert c.post(f"/jobs/{job_id}/run", json={"from_stage": "VOICE"}).status_code == 200
        d = run_to_rest(c, job_id)
        script_runs = [r for r in d["runs"] if r["stage"] == "SCRIPT"]
        assert len(script_runs) == 1 and script_runs[0]["superseded_by"] is None
        assert len([r for r in d["runs"] if r["stage"] == "VOICE"]) == 2


def test_db_refuses_exported_without_approval(tmp_path):  # the storage-layer wall
    sm = make_sessionmaker(f"sqlite:///{(tmp_path / 'w.db').as_posix()}")
    with sm() as db:
        db.add(ContentJob(input_topic="x", status="EXPORTED"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_prod_refuses_claude_cli():
    with pytest.raises(RuntimeError):
        Settings(_env_file=None, app_env="prod", llm_provider="claude_cli").check()


def test_claude_cli_error_is_provider_error(monkeypatch):
    out = json.dumps({"is_error": True, "result": "Not logged in"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    with pytest.raises(ProviderError, match="Not logged in"):
        ClaudeCliProvider(Settings(_env_file=None))._call("hi", 5)


def test_claude_cli_parses_result(monkeypatch):
    out = json.dumps({"is_error": False, "result": '```json\n{"a": 1}\n```', "total_cost_usd": 0.01, "modelUsage": {"claude-sonnet-5-5": {}}, "usage": {"input_tokens": 10, "output_tokens": 5}})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    r = ClaudeCliProvider(Settings(_env_file=None))._call("hi", 5)
    assert r.model == "claude-sonnet-5-5" and r.tokens_in == 10 and r.cost_usd == 0.01


def test_subtitle_cues_respect_line_and_syllable_rules():
    text = "Thay đổi thứ hai: kỹ năng quan trọng không còn là tự làm mọi thứ, mà là giao việc rõ ràng và kiểm tra kết quả."
    cues = media.build_cues([{"text": text, "start_ms": 1000, "end_ms": 9000}])
    words = text.split()
    assert " ".join(" ".join(c["lines"]) for c in cues).split() == words  # nothing lost, no syllable split
    assert all(len(c["lines"]) <= 2 and all(len(l) <= 32 for l in c["lines"]) for c in cues)
    assert cues[0]["start_ms"] == 1000 and cues[-1]["end_ms"] == 9000
    assert all(a["end_ms"] == b["start_ms"] for a, b in zip(cues, cues[1:]))


def test_subtitle_cues_never_overlap():
    segs = [{"text": "Chatbot chỉ trả lời câu hỏi.", "start_ms": 5956, "end_ms": 8531},
            {"text": "Còn AI agent thì nhận một mục tiêu, tự lên kế hoạch, rồi tự làm từng bước.", "start_ms": 8481, "end_ms": 13918}]
    cues = media.build_cues(segs)
    assert all(a["end_ms"] <= b["start_ms"] for a, b in zip(cues, cues[1:]))
    assert all(len(l) <= 32 for c in cues for l in c["lines"])


class _FakeMessages:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.kwargs = resp, exc, None

    async def create(self, **kw):
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return self.resp


class _FakeClient:
    def __init__(self, messages):
        self.beta = type("B", (), {"messages": messages})()

    def with_options(self, **_):
        return self


def _resp(stop="end_turn", model="claude-sonnet-5-5"):
    from types import SimpleNamespace as NS
    return NS(stop_reason=stop, stop_details=NS(category="cyber") if stop == "refusal" else None, model=model,
              content=[NS(type="thinking", thinking=""), NS(type="text", text='{"ok": 1}')], usage=NS(input_tokens=18000, output_tokens=6000))


def test_anthropic_provider_costs_and_text():
    import asyncio
    from app.providers import AnthropicApiProvider
    msgs = _FakeMessages(_resp())
    r = asyncio.run(AnthropicApiProvider("claude-sonnet-5-5", client=_FakeClient(msgs)).complete("hi"))
    assert r.text == '{"ok": 1}' and r.model == "claude-sonnet-5-5"
    assert r.cost_usd == pytest.approx((18000 * 2 + 6000 * 10) / 1e6)
    assert msgs.kwargs["model"] == "claude-sonnet-5-5" and msgs.kwargs["fallbacks"] == "default" and msgs.kwargs["output_config"] == {"effort": "medium"}


def test_anthropic_provider_refusal_is_not_retried():
    import asyncio
    from app.pipeline import RETRYABLE
    from app.providers import AnthropicApiProvider, ProviderRejected
    with pytest.raises(ProviderRejected) as e:
        asyncio.run(AnthropicApiProvider("claude-sonnet-5-5", client=_FakeClient(_FakeMessages(_resp("refusal")))).complete("hi"))
    assert not isinstance(e.value, RETRYABLE)


def test_anthropic_provider_needs_a_key():
    from app.providers import build_llm
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_llm(Settings(_env_file=None, llm_provider="anthropic_api", anthropic_api_key=""))
