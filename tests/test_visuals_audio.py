"""Sprint 3: Pexels stock photos (+ card fallback), Ken Burns render, loudness normalisation. Offline, real FFmpeg."""
import io
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import media
from app.config import Settings
from app.main import create_app
from app.providers import FixtureLLM, FixtureStock, FixtureTTS, PexelsProvider, build_stock

FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")
FIX = Path(__file__).parent / "fixtures" / "llm"
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="FFmpeg not found; set FFMPEG_PATH / FFPROBE_PATH")


def tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 600), (40, 90, 140)).save(buf, format="JPEG")
    return buf.getvalue()


def settings(tmp_path: Path, **kw) -> Settings:
    return Settings(_env_file=None, app_env="test", llm_provider="fixture", tts_provider="fixture",
                    ffmpeg_path=FFMPEG or "ffmpeg", ffprobe_path=FFPROBE or "ffprobe", storage_dir=tmp_path,
                    database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}", **kw)


def run_to_rest(client: TestClient, job_id: str, timeout_s: float = 90) -> dict:
    end = time.time() + timeout_s
    while time.time() < end:
        d = client.get(f"/jobs/{job_id}").json()
        if d["job"]["status"] not in ("QUEUED", "RUNNING"):
            return d
        time.sleep(0.3)
    raise AssertionError("job did not finish")


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------- media.py


@needs_ffmpeg
def test_render_photo_card_is_vertical_and_readable(tmp_path):
    photo = tmp_path / "src.jpg"
    Image.new("RGB", (2000, 1200), (10, 80, 10)).save(photo)  # landscape source, must crop to 9:16
    out = tmp_path / "card.png"
    media.render_photo_card(photo, "Một tiêu đề khá dài để kiểm tra xuống dòng", 0, 3, r"C:\Windows\Fonts\segoeuib.ttf", out)
    assert Image.open(out).size == (media.W, media.H)


@needs_ffmpeg
def test_ken_burns_render_keeps_exact_total_duration(tmp_path):
    cards = []
    for i in range(3):
        p = tmp_path / f"c{i}.png"
        media.render_card(f"scene {i}", i, 3, r"C:\Windows\Fonts\segoeuib.ttf", p)
        cards.append(p)
    audio = tmp_path / "silent.wav"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "6.4", str(audio)], check=True)
    out = tmp_path / "final.mp4"
    durations = [2.1, 3.0, 1.3]
    media.render_video(FFMPEG, cards, durations, audio, out)
    info = media.probe(FFPROBE, out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (media.W, media.H)
    assert abs(float(info["format"]["duration"]) - sum(durations)) < 0.1


@needs_ffmpeg
def test_loudness_normalize_reports_none_on_silence_and_a_real_value_on_a_tone(tmp_path):
    silence, tone = tmp_path / "silence.wav", tmp_path / "tone.wav"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "2", str(silence)], check=True)
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000,volume=-25dB", "-t", "2", str(tone)], check=True)
    assert media.normalize_loudness(FFMPEG, silence, tmp_path / "s_out.wav") is None
    lufs = media.normalize_loudness(FFMPEG, tone, tmp_path / "t_out.wav")
    assert lufs is not None and abs(lufs - media.LOUDNORM_TARGET_LUFS) < 2.0


# ---------------------------------------------------------------- providers.py: Pexels (mocked HTTP, no network)


@pytest.mark.anyio
async def test_pexels_search_downloads_and_caches(tmp_path):
    calls = {"search": 0, "download": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "api.pexels.com" in str(request.url) and "/search" in str(request.url):
            calls["search"] += 1
            assert request.headers["authorization"] == "test-key"
            assert request.url.params["query"] == "cat cucumber kitchen"
            return httpx.Response(200, json={"photos": [{"id": 42, "url": "https://www.pexels.com/photo/42",
                "photographer": "Jane Doe", "photographer_url": "https://pexels.com/@jane",
                "src": {"large2x": "https://images.pexels.com/photos/42/large2x.jpg"}}]})
        calls["download"] += 1
        return httpx.Response(200, content=tiny_jpeg())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.pexels.com/v1")
    provider = PexelsProvider("test-key", tmp_path / "cache", client=client)
    photo = await provider.search_photo("cat cucumber kitchen")
    assert photo is not None and photo.source_id == "42" and photo.author == "Jane Doe" and photo.path.is_file()
    assert calls == {"search": 1, "download": 1}

    photo2 = await provider.search_photo("cat cucumber kitchen")  # same URL -> same cache key, no re-download
    assert photo2.path == photo.path and calls["download"] == 1


@pytest.mark.anyio
async def test_pexels_returns_none_without_raising_on_no_results_or_error(tmp_path):
    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"photos": []})

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    for handler in (empty, broken):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.pexels.com/v1")
        provider = PexelsProvider("test-key", tmp_path / "cache", client=client)
        assert await provider.search_photo("anything") is None


@pytest.mark.anyio
async def test_fixture_stock_is_deterministic_and_offline(tmp_path):
    stock = FixtureStock(tmp_path / "cache")
    a = await stock.search_photo("office worker")
    b = await stock.search_photo("office worker")
    c = await stock.search_photo("different query")
    assert a.path == b.path and a.path != c.path and a.path.is_file()


def test_build_stock_selects_and_degrades(tmp_path):
    assert build_stock(settings(tmp_path, stock_provider="none")) is None
    assert isinstance(build_stock(settings(tmp_path, stock_provider="fixture")), FixtureStock)
    assert build_stock(settings(tmp_path, stock_provider="pexels")) is None  # no key configured
    assert isinstance(build_stock(settings(tmp_path, stock_provider="pexels", pexels_api_key="k")), PexelsProvider)
    with pytest.raises(ValueError):
        build_stock(settings(tmp_path, stock_provider="bogus"))


# ---------------------------------------------------------------- end to end through the pipeline


@needs_ffmpeg
def test_stage_visuals_uses_stock_photos_with_licence_and_loudness_check_is_advisory(tmp_path):
    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(s.ffmpeg_path), stock=FixtureStock(tmp_path / "cache"))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "x y z", "review_script": False, "target_duration_s": 55}).json()["id"]
        d = run_to_rest(c, job_id)
        assert d["job"]["status"] == "AWAITING_APPROVAL", d["job"]["error_summary"]
        assets = d["outputs"]["VISUALS"]["assets"]
        assert d["outputs"]["VISUALS"]["stock_hits"] == len(assets)
        for a in assets:
            assert a["kind"] == "stock_image" and a["licence"] == "pexels" and a["licence_url"]
            assert a["source_url"] and a["author"] and a["attribution_text"]
        # loudness check is present (silent fixture audio -> not passed, but it's a warning, never blocking)
        checks = {c["rule"]: c for c in d["outputs"]["QUALITY"]["checks"]}
        assert checks["loudness"]["severity"] == "warning" and checks["loudness"]["passed"] is False
        assert d["outputs"]["QUALITY"]["passed"] is True  # warnings never block


@needs_ffmpeg
def test_stage_visuals_falls_back_to_cards_without_a_stock_provider(tmp_path):
    s = settings(tmp_path)  # no stock= passed -> build_stock(s) -> None (pexels, no key)
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(s.ffmpeg_path))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "x y z", "review_script": False}).json()["id"]
        d = run_to_rest(c, job_id)
        assets = d["outputs"]["VISUALS"]["assets"]
        assert all(a["kind"] == "title_card" and a["licence"] == "generated" for a in assets)
        assert d["outputs"]["VISUALS"]["stock_hits"] == 0


@needs_ffmpeg
def test_visual_query_optional_falls_back_to_on_screen_text(tmp_path):
    seen_queries = []

    class RecordingStock(FixtureStock):
        async def search_photo(self, query):
            seen_queries.append(query)
            return await super().search_photo(query)

    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(s.ffmpeg_path), stock=RecordingStock(tmp_path / "cache"))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "x y z", "review_script": False}).json()["id"]
        run_to_rest(c, job_id)
    sc = json.loads((FIX / "script_vi.json").read_text(encoding="utf-8"))
    assert seen_queries == [scene["on_screen_text"] for scene in sc["scenes"]]  # the fixture has no visual_query field
