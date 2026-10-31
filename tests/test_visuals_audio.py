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
    media.render_photo_card(photo, "Một tiêu đề khá dài để kiểm tra xuống dòng", 0, r"C:\Windows\Fonts\segoeuib.ttf", out)
    assert Image.open(out).size == (media.W, media.H)


@needs_ffmpeg
def test_ken_burns_render_keeps_exact_total_duration(tmp_path):
    cards = []
    for i in range(3):
        p = tmp_path / f"c{i}.png"
        media.render_card(f"scene {i}", i, r"C:\Windows\Fonts\segoeuib.ttf", p)
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


# ---------------------------------------------------------------- Sprint 4: subtitle burn-in and risky terms


@needs_ffmpeg
def test_burned_subtitles_change_the_picture_and_are_recorded(tmp_path):
    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(s.ffmpeg_path))
    frames = {}
    with TestClient(app) as c:
        for burn in (False, True):
            job_id = c.post("/jobs", json={"topic": "x y z", "review_script": False, "target_duration_s": 55, "burn_subtitles": burn}).json()["id"]
            d = run_to_rest(c, job_id)
            assert d["job"]["status"] == "AWAITING_APPROVAL", d["job"]["error_summary"]
            r = d["outputs"]["RENDER"]
            assert r["subtitles_burned"] is burn
            png = tmp_path / f"f{burn}.png"
            subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", "2", "-i", str(tmp_path / r["video_path"]), "-frames:v", "1", str(png)], check=True)
            frames[burn] = Image.open(png).convert("RGB")
    from PIL import ImageChops
    box = ImageChops.difference(frames[False], frames[True]).getbbox()
    assert box is not None and box[1] > 1000  # captions appear in the lower part, clear of the headline


def test_risky_terms_found_case_insensitively_and_listed():
    from app.pipeline import risky_terms
    assert risky_terms(["Chữ thường", "Kiếm tiền NHANH với app này", "bình thường"]) == ["kiếm tiền nhanh"]
    assert risky_terms(["một câu hoàn toàn bình thường"]) == []


@needs_ffmpeg
def test_safety_terms_warn_but_do_not_block(tmp_path):
    risky = tmp_path / "risky.json"
    data = json.loads((FIX / "script_vi.json").read_text(encoding="utf-8"))
    data["caption"] = "Cam kết 100% hiệu quả, kiếm tiền nhanh cho mọi người."
    risky.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(risky), tts=FixtureTTS(s.ffmpeg_path))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "x y z", "target_duration_s": 55}).json()["id"]
        d = run_to_rest(c, job_id)
        review = {x["rule"]: x for x in d["outputs"]["SCRIPT"]["checks"]}
        assert review["safety_terms"]["passed"] is False and "kiếm tiền nhanh" in review["safety_terms"]["actual"]
        c.post(f"/jobs/{job_id}/script/approve", json={"approved_by": "founder"})
        d = run_to_rest(c, job_id)
        gate = {x["rule"]: x for x in d["outputs"]["QUALITY"]["checks"]}
        assert gate["safety_terms"]["passed"] is False and gate["safety_terms"]["severity"] == "warning"
        assert d["job"]["status"] == "AWAITING_APPROVAL"  # a human decides, nothing is blocked


@needs_ffmpeg
def test_burn_in_works_with_a_relative_storage_dir(tmp_path, monkeypatch):
    """The real default is STORAGE_DIR=./storage; burn-in changes ffmpeg's cwd, so relative paths must not leak through."""
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None, app_env="test", llm_provider="fixture", tts_provider="fixture", ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE,
                 storage_dir=Path("storage"), database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(FFMPEG))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "x y z", "review_script": False, "target_duration_s": 55, "burn_subtitles": True}).json()["id"]
        d = run_to_rest(c, job_id)
        assert d["job"]["status"] == "AWAITING_APPROVAL", d["job"]["error_summary"]
        assert d["outputs"]["RENDER"]["subtitles_burned"] is True


# ---------------------------------------------------------------- local image folder (no key, no account)


def test_local_stock_matches_by_file_name_and_never_guesses(tmp_path):
    import asyncio
    from app.providers import LocalStock
    for name in ("cat-kitchen-night.jpg", "meo_dua_chuot.png", "office-laptop.webp", "notes.txt", "dog.gif"):
        (tmp_path / name).write_bytes(b"x")
    stock = LocalStock(tmp_path)
    pick = lambda q: (lambda r: r.path.name if r else None)(asyncio.run(stock.search_photo(q)))  # noqa: E731
    assert pick("cat in a kitchen at night") == "cat-kitchen-night.jpg"  # most words in common wins
    assert pick("Mèo sợ dưa chuột") == "meo_dua_chuot.png"  # Vietnamese diacritics are ignored on both sides
    assert pick("person typing laptop office") == "office-laptop.webp"
    assert pick("airplane over the ocean") is None  # nothing in common: a card, never an unrelated photo
    assert pick("cat") == "cat-kitchen-night.jpg"  # one shared word is enough
    assert pick("dog") is None  # .gif and .txt are not image types the renderer takes


def test_build_stock_local_creates_its_folder(tmp_path):
    from app.providers import LocalStock
    folder = tmp_path / "my" / "images"
    stock = build_stock(settings(tmp_path, stock_provider="local", local_stock_dir=folder))
    assert isinstance(stock, LocalStock) and folder.is_dir()


@needs_ffmpeg
def test_local_photos_mix_with_cards_and_are_recorded_as_owned(tmp_path):
    from app.providers import LocalStock
    folder = tmp_path / "stock"
    folder.mkdir()
    Image.new("RGB", (900, 1400), (120, 60, 30)).save(folder / "chatbot-agent-hanh-dong.jpg")
    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(s.ffmpeg_path), stock=LocalStock(folder))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "x y z", "review_script": False, "target_duration_s": 55}).json()["id"]
        d = run_to_rest(c, job_id)
        assert d["job"]["status"] == "AWAITING_APPROVAL", d["job"]["error_summary"]
        assets = d["outputs"]["VISUALS"]["assets"]
        photos = [a for a in assets if a["kind"] == "stock_image"]
        cards = [a for a in assets if a["kind"] == "title_card"]
        assert photos and cards and d["outputs"]["VISUALS"]["stock_hits"] == len(photos)
        for a in photos:
            assert a["licence"] == "owned" and a["source_provider"] == "local" and a["attribution_text"] is None and a["source_url"] is None
        assert all(a["licence"] == "generated" for a in cards)
        # the client bundle must not carry anything that identifies this machine
        assert c.post(f"/jobs/{job_id}/approve", json={"approved_by": "founder"}).status_code == 200
        manifest = (tmp_path / "jobs" / job_id / "export" / "manifest.json").read_text(encoding="utf-8")
        assert tmp_path.name not in manifest and "file:" not in manifest and "chatbot-agent-hanh-dong.jpg" in manifest


@needs_ffmpeg
def test_every_scene_card_appears_at_its_own_time(tmp_path):
    """Regression: the first real video showed scene 1's card for the whole video. The duration-only check could not
    see it because every card in that test looked alike, so this one uses a different flat colour per scene."""
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    cards = []
    for i, rgb in enumerate(colours):
        p = tmp_path / f"c{i}.png"
        Image.new("RGB", (media.W, media.H), rgb).save(p)
        cards.append(p)
    durations = [1.0, 1.4, 1.0]
    audio = tmp_path / "silent.wav"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3.4", str(audio)], check=True)
    out = tmp_path / "final.mp4"
    media.render_video(FFMPEG, cards, durations, audio, out)
    assert abs(float(media.probe(FFPROBE, out)["format"]["duration"]) - 3.4) < 0.1
    for t, expected in ((0.5, 0), (1.7, 1), (2.9, 2)):
        png = tmp_path / f"f{t}.png"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(out), "-ss", str(t), "-frames:v", "1", str(png)], check=True)
        r, g, b = Image.open(png).convert("RGB").getpixel((media.W // 2, media.H // 2))
        assert [r, g, b].index(max(r, g, b)) == expected and max(r, g, b) > 200, (t, expected, (r, g, b))


class TwoSentenceTTS(FixtureTTS):
    """Silent audio like FixtureTTS, but reports two sentence boundaries per scene, as edge-tts does for real text."""

    async def synthesize(self, text, language, out, voice=None):
        res = await super().synthesize(text, language, out, voice)
        end = res.sentences[0]["end_ms"]
        res.sentences = [{"text": "Câu một.", "start_ms": 0, "end_ms": end // 2}, {"text": "Câu hai.", "start_ms": end // 2, "end_ms": end}]
        return res


@needs_ffmpeg
def test_one_card_per_sentence_timed_by_the_voice(tmp_path):
    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=TwoSentenceTTS(s.ffmpeg_path), stock=None)
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "x y z", "review_script": False, "target_duration_s": 55}).json()["id"]
        d = run_to_rest(c, job_id)
        assert d["job"]["status"] == "AWAITING_APPROVAL", d["job"]["error_summary"]
        assets, scenes = d["outputs"]["VISUALS"]["assets"], d["outputs"]["VOICE"]["scenes"]
        assert len(assets) == 2 * len(scenes)
        for sp in scenes:
            mine = [a for a in assets if a["scene_index"] == sp["scene_index"]]
            assert mine[0]["start_ms"] == sp["start_ms"] and mine[-1]["end_ms"] == sp["end_ms"] and mine[0]["end_ms"] == mine[1]["start_ms"]
        a0, a1 = (tmp_path / assets[0]["path"]).read_bytes(), (tmp_path / assets[1]["path"]).read_bytes()
        assert a0 != a1  # the second card carries the second sentence
        assert abs(d["outputs"]["RENDER"]["duration_s"] - d["outputs"]["VOICE"]["duration_s"]) <= 0.3


def test_headline_words_are_drawn_in_colour(tmp_path):
    font = r"C:\Windows\Fonts\segoeuib.ttf"
    plain, lit = tmp_path / "plain.png", tmp_path / "lit.png"
    media.render_card("Bố giữ remote, bố là luật", 0, font, plain)
    media.render_card("Bố giữ remote, bố là luật", 0, font, lit, media.plain_words("Bố là luật!"))
    count = lambda p: sum(1 for px in Image.open(p).convert("RGB").getdata() if px == media.HIGHLIGHT)
    assert count(lit) > count(plain) + 5000


@needs_ffmpeg
def test_music_is_mixed_under_the_voice_and_keeps_the_length(tmp_path):
    card = tmp_path / "c.png"
    Image.new("RGB", (media.W, media.H), (0, 0, 255)).save(card)
    voice, music, out = tmp_path / "voice.wav", tmp_path / "music.wav", tmp_path / "final.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3", str(voice)], check=True)
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1", str(music)], check=True)
    media.render_video(FFMPEG, [card], [3.0], voice, out, music=music)  # a 1 s track must loop under 3 s of voice
    assert abs(float(media.probe(FFPROBE, out)["format"]["duration"]) - 3.0) < 0.1
    det = subprocess.run([FFMPEG, "-i", str(out), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    mean = float(det.split("mean_volume:")[1].split("dB")[0])
    assert -50 < mean < -10, mean  # silent voice + a full-scale sine at MUSIC_GAIN_DB fading out: audible, well under full scale


def test_hashtags_become_lowercase_ascii():
    from app.pipeline import ScriptOut
    sc = ScriptOut(title="title", caption="cap", hashtags=["#Hài Hước", "điều hoà", "#AI2026"],
                   scenes=[{"narration": "câu một dài", "on_screen_text": "a"}, {"narration": "câu hai dài", "on_screen_text": "b"}])
    assert sc.hashtags == ["#haihuoc", "#dieuhoa", "#ai2026"]
