"""Translated jobs for the global channels: the source script is localised scene for scene, its clips are reused and
re-cut over the new voice, the paid QA review is skipped, and the API refuses sources that cannot be translated."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import copy_visuals, font_for
from app.providers import EdgeTTS, FixtureStock, FixtureTTS, LangRouterTTS, LLMResponse, ProviderRejected, TTSResult
from tests.test_pipeline import FIX, needs_ffmpeg, run_to_rest, settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


class StubLLM:
    """The fixture Vietnamese script for a fresh job, the Spanish fixture for a translation, and a clean back-check."""

    name = "stub"

    def __init__(self):
        self.prompts = []

    async def complete(self, prompt, timeout_s=300):
        self.prompts.append(prompt)
        if "Compare scene by scene" in prompt:
            text = '{"drifted": [{"scene": 5, "why": "ten results became ten people"}]}'
        elif "You localise" in prompt:
            text = (FIX / "script_es.json").read_text(encoding="utf-8")
        else:
            text = (FIX / "script_vi.json").read_text(encoding="utf-8")
        return LLMResponse(text=text, provider=self.name, model="stub", cost_usd=0.001)


class RecordingTTS(FixtureTTS):
    def __init__(self, ffmpeg):
        super().__init__(ffmpeg)
        self.calls = []

    async def synthesize(self, text, language, out, voice=None):
        self.calls.append((language, voice))
        return await super().synthesize(text, language, out, voice)


@needs_ffmpeg
def test_translation_reuses_clips_and_stops_at_script_review(tmp_path):
    s = settings(tmp_path)
    llm, tts = StubLLM(), RecordingTTS(s.ffmpeg_path)
    app = create_app(s, llm=llm, tts=tts, stock=FixtureStock(tmp_path / "cache", s.ffmpeg_path))
    with TestClient(app) as c:
        src_id = c.post("/jobs", json={"topic": "AI agent 2026", "review_script": False, "target_duration_s": 55}).json()["id"]
        src = run_to_rest(c, src_id)
        assert src["job"]["status"] == "AWAITING_APPROVAL", src["job"]["error_summary"]
        ch = c.post("/channels", json={"name": "ES", "market": "global", "language": "es", "voice_id": "es-MX-JorgeNeural"}).json()["id"]

        # refusals: no channel, a channel in the source's language, a source that has no visuals yet
        assert c.post("/jobs", json={"source_job_id": src_id}).status_code == 400
        vi_ch = c.post("/channels", json={"name": "VI", "language": "vi"}).json()["id"]
        assert c.post("/jobs", json={"source_job_id": src_id, "channel_id": vi_ch}).status_code == 409
        pending = c.post("/jobs", json={"topic": "no visuals yet"}).json()["id"]
        run_to_rest(c, pending)
        assert c.post("/jobs", json={"source_job_id": pending, "channel_id": ch}).status_code == 409
        assert c.post("/jobs", json={"source_job_id": "job_nope", "channel_id": ch}).status_code == 409
        assert c.post("/jobs", json={}).status_code == 422

        r = c.post("/jobs", json={"source_job_id": src_id, "channel_id": ch, "notes": "Next: your cat"})
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        assert r.json()["language"] == "es" and r.json()["source_job_id"] == src_id and r.json()["target_duration_s"] == 55
        d = run_to_rest(c, job_id)
        assert d["job"]["status"] == "SCRIPT_REVIEW", d["job"]["error_summary"]
        sc = d["outputs"]["SCRIPT"]
        assert len(sc["scenes"]) == 8 and sc["scenes"][0]["narration"].startswith("En 2026") and sc["source_job_id"] == src_id
        assert sc["sources"] == src["outputs"]["SCRIPT"]["sources"]  # the source's sources stand
        fidelity = next(x for x in sc["checks"] if x["rule"] == "translation_fidelity")
        assert fidelity["passed"] is False and "scene 5" in fidelity["actual"]
        assert "Notes from the operator: Next: your cat" in llm.prompts[-2] and "8 scenes" in llm.prompts[-2]
        # the next-episode line is added by code, once, from next_subject (the model dropped it in 4 of 5 real runs)
        assert sc["scenes"][-1]["narration"].endswith("Cuéntalo en los comentarios. Mañana: tu gato.")
        assert next(x for x in sc["checks"] if x["rule"] == "next_episode_line")["passed"] is True

        assert c.post(f"/jobs/{job_id}/script/approve", json={"approved_by": "founder"}).status_code == 200
        d = run_to_rest(c, job_id)
        assert d["job"]["status"] == "AWAITING_APPROVAL", d["job"]["error_summary"]
        assert ("es", "es-MX-JorgeNeural") in tts.calls  # the channel's voice reached the TTS
        src_paths = {a["path"] for a in src["outputs"]["VISUALS"]["assets"]}
        assets = d["outputs"]["VISUALS"]["assets"]
        assert assets and {a["path"] for a in assets} <= src_paths and d["outputs"]["VISUALS"]["captions"] == "words"
        assert [a["scene_index"] for a in assets] == sorted(a["scene_index"] for a in assets)
        assert all(a["end_ms"] - a["start_ms"] <= 2001 for a in assets)
        assert all(a["licence"] == "pexels" for a in assets)  # licence ledger carried over
        checks = {x["rule"]: x for x in d["outputs"]["QUALITY"]["checks"]}
        assert checks["qa_review"]["passed"] is True and src_id in checks["qa_review"]["actual"]
        assert d["job"]["total_cost_usd"] == pytest.approx(0.002)  # two stub calls; nothing paid for clips or QA

        ok = c.post(f"/jobs/{job_id}/approve", json={"approved_by": "founder"})
        assert ok.status_code == 200, ok.text
        caption = (tmp_path / "jobs" / job_id / "export" / "caption.txt").read_text(encoding="utf-8")
        assert caption.startswith("Por qué los agentes") and "¿Cuál de los tres cambios ya ves en tu trabajo?" in caption.split("--- pinned comment ---")[1]
        manifest = json.loads((tmp_path / "jobs" / job_id / "export" / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["language"] == "es" and {"es.srt", "es.vtt"} <= set(ok.json()["export"]["files"])


def test_copy_visuals_recuts_source_clips_over_new_spans(tmp_path):
    s = settings(tmp_path)
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / name).write_bytes(b"x")
    src = {"captions": "words", "stock_hits": 2, "assets": [
        {"kind": "stock_video", "path": "a.mp4", "scene_index": 0, "order_index": 0, "start_ms": 0, "end_ms": 2000, "licence": "pixabay", "clip_offset_s": 0.5},
        {"kind": "stock_video", "path": "b.mp4", "scene_index": 0, "order_index": 1, "start_ms": 2000, "end_ms": 3000, "licence": "pixabay", "clip_offset_s": 0.5},
        {"kind": "stock_video", "path": "a.mp4", "scene_index": 0, "order_index": 2, "start_ms": 3000, "end_ms": 4000, "licence": "pixabay", "clip_offset_s": 2.0},
        {"kind": "stock_video", "path": "c.mp4", "scene_index": 1, "order_index": 3, "start_ms": 4000, "end_ms": 6000, "licence": "pixabay", "clip_offset_s": 0.5},
    ]}
    vo = {"scenes": [{"scene_index": 0, "start_ms": 0, "end_ms": 5000}, {"scene_index": 1, "start_ms": 5000, "end_ms": 6500}]}
    out = copy_visuals(s, src, vo)
    assert [(a["path"], a["start_ms"], a["end_ms"]) for a in out["assets"]] == [
        ("a.mp4", 0, 1667), ("b.mp4", 1667, 3333), ("a.mp4", 3333, 5000), ("c.mp4", 5000, 6500)]
    assert [a["order_index"] for a in out["assets"]] == [0, 1, 2, 3] and out["assets"][0]["licence"] == "pixabay"
    (tmp_path / "c.mp4").unlink()
    with pytest.raises(Exception, match="missing"):
        copy_visuals(s, src, vo)


@pytest.mark.anyio
async def test_router_default_and_edge_voice_guard(tmp_path):
    class Stub:
        def __init__(self, name, lang):
            self.name, self.voices, self.calls = name, {lang: "v"}, []

        async def synthesize(self, text, language, out, voice=None):
            self.calls.append((language, voice))
            return TTSResult(out, [])

    vi, edge = Stub("fpt", "vi"), Stub("edge_tts", "en")
    r = LangRouterTTS({"vi": vi, "en": edge}, default=edge)
    await r.synthesize("Hola", "es", tmp_path / "a.mp3", voice="es-MX-JorgeNeural")
    await r.synthesize("Chào", "vi", tmp_path / "b.mp3")
    assert edge.calls == [("es", "es-MX-JorgeNeural")] and vi.calls == [("vi", None)]
    with pytest.raises(ProviderRejected):
        await LangRouterTTS({"vi": vi}).synthesize("Hola", "es", tmp_path / "c.mp3")
    with pytest.raises(ProviderRejected):  # no network call: the guard runs before the endpoint
        await EdgeTTS({"en": "en-US-AriaNeural"}).synthesize("Hola", "es", tmp_path / "d.mp3")


def test_japanese_gets_its_own_font(tmp_path):
    s = settings(tmp_path)
    assert font_for(s, "ja") == s.font_path_ja and font_for(s, "de") == s.font_path
