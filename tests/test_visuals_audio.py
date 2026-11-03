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
from app.providers import FixtureLLM, FixtureStock, FixtureTTS, PexelsProvider, PixabayProvider, build_stock

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


@pytest.mark.anyio
async def test_pixabay_skips_grey_and_used_photos_and_degrades_quietly(tmp_path):
    calls = []
    grey = io.BytesIO()
    Image.new("RGB", (150, 100), (128, 128, 128)).save(grey, format="JPEG")

    def hit(i, tags="family, living room, sofa"):
        return {"id": i, "pageURL": f"https://pixabay.com/photos/{i}", "largeImageURL": f"https://pixabay.com/get/{i}.jpg",
                "previewURL": f"https://pixabay.com/preview/{i}.jpg", "user": "ann", "tags": tags}

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/":
            assert request.url.params["key"] == "k" and request.url.params["orientation"] == "vertical"
            return httpx.Response(200, json={"hits": [] if request.url.params["q"] == "nothing" else [hit(5, "boat, yacht, sea"), hit(9), hit(7), hit(8)]})
        if request.url.path == "/preview/9.jpg":
            return httpx.Response(200, content=grey.getvalue())
        if request.url.path.startswith(("/preview/", "/get/")):
            return httpx.Response(200, content=tiny_jpeg())
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://pixabay.com")
    prov = PixabayProvider("k", tmp_path / "cache", client=client)
    a = await prov.search_photo("family living room")  # 5's tags do not match (Pixabay ranks loosely); 9 is black and white
    b = await prov.search_photo("family living room")  # 7 already used: the next one
    assert (a.source_id, b.source_id) == ("7", "8") and a.path.is_file() and prov.attribution(a) == "Image by ann on Pixabay"
    assert await prov.search_photo("family living room") is None  # only grey or used left: a title card, not a repeat
    again = await PixabayProvider("k", tmp_path / "cache", client=client).search_photo("family living room")
    assert again.source_id == "7" and calls.count("/get/7.jpg") == 1  # a new process may reuse it, from the cache
    assert await prov.search_photo("nothing") is None
    assert build_stock(settings(tmp_path, stock_provider="pixabay")) is None  # no key: cards, not a crash


# ---------------------------------------------------------------- facts format: stock clips + word-by-word captions


def test_word_captions_colour_one_word_at_a_time(tmp_path):
    out = tmp_path / "w.ass"
    n = media.write_word_captions([{"text": "Số 1. Bạch tuộc có ba tim.", "start_ms": 0, "end_ms": 3000},
                                   {"text": "Thật {đấy}.", "start_ms": 2990, "end_ms": 4000}], out)
    events = [l for l in out.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    assert n == len(events) == 9
    assert events[0].endswith(f"{{\\c{media.WORD_COLOR}}}Số{{\\c&HFFFFFF&}} 1. Bạch")  # 3 words on screen, the spoken one in colour
    assert events[3].endswith(f"{{\\c{media.WORD_COLOR}}}tuộc{{\\c&HFFFFFF&}} có ba")
    assert media.POP in events[0] and media.POP in events[3] and media.POP not in events[1]  # each new group pops in once
    times = [(e.split(",")[1], e.split(",")[2]) for e in events]
    assert times[0][0] == "0:00:00.00" and times[6][1] == "0:00:03.00" and times[-1][1] == "0:00:04.00"
    assert all(a[1] == b[0] for a, b in zip(times, times[1:]))  # back to back: the overlapping second sentence waits
    assert "{đấy}" not in events[-1] and "đấy." in events[-1]  # braces are ASS syntax: stripped


@needs_ffmpeg
def test_clips_and_cards_render_together_with_burned_word_captions(tmp_path):
    clip = tmp_path / "clip.mp4"  # landscape and shorter than its slot: cropped to 9:16, last frame held
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-t", "2", str(clip)], check=True)
    card = tmp_path / "c.png"
    media.render_card("", 1, r"C:\Windows\Fonts\segoeuib.ttf", card)
    audio = tmp_path / "a.wav"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3.5", str(audio)], check=True)
    ass = tmp_path / "vi.ass"
    media.write_word_captions([{"text": "Một hai ba bốn năm", "start_ms": 0, "end_ms": 3500}], ass)
    out = tmp_path / "render" / "final.mp4"
    out.parent.mkdir()
    media.render_video(FFMPEG, [clip, card], [2.5, 1.0], audio, out, ass, r"C:\Windows\Fonts\segoeuib.ttf", offsets=[0.5, 0.0], whoosh_at=[2.5])
    def loudness(start, dur):  # the voice track is silent: any sound is the whoosh
        err = subprocess.run([FFMPEG, "-v", "info", "-ss", str(start), "-t", str(dur), "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        return float(err.split("max_volume:")[1].split("dB")[0])
    assert loudness(2.2, 0.4) > -40 and loudness(0.5, 1.0) < -80  # a whoosh on the cut, silence elsewhere
    info = media.probe(FFPROBE, out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (media.W, media.H) and abs(float(info["format"]["duration"]) - 3.5) < 0.1
    assert (out.parent / "burn.ass").is_file()


@pytest.mark.anyio
async def test_pixabay_videos_match_tags_prefer_upright_and_skip_short_grey_or_used(tmp_path):
    grey = io.BytesIO()
    Image.new("RGB", (150, 100), (128, 128, 128)).save(grey, format="JPEG")

    def hit(i, tags="octopus, sea, ocean", duration=10, w=1920, h=1080):
        return {"id": i, "pageURL": f"https://pixabay.com/videos/{i}", "user": "bo", "tags": tags, "duration": duration,
                "videos": {"medium": {"url": f"https://cdn.test/{i}.mp4", "width": w, "height": h, "thumbnail": f"https://cdn.test/{i}.jpg"}}}

    def handler(request):
        if request.url.path == "/api/videos/":
            return httpx.Response(200, json={"hits": [hit(1, "car, road"), hit(6, "perfume, sea, ocean"), hit(2, duration=2), hit(3),
                                                      hit(4, w=1080, h=1920), hit(5)]})
        if request.url.path == "/5.jpg":
            return httpx.Response(200, content=grey.getvalue())
        if request.url.path.endswith(".jpg"):
            return httpx.Response(200, content=tiny_jpeg())
        if request.url.path.endswith(".mp4"):
            return httpx.Response(200, content=b"mp4")
        return httpx.Response(500)

    prov = PixabayProvider("k", tmp_path / "cache", client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://pixabay.com"))
    clips = await prov.search_videos("octopus sea ocean")
    assert [c.source_id for c in clips] == ["4", "3"]  # 1 off-topic, 6 lacks the subject, 2 too short, 5 grey; upright 4 first
    assert clips[0].path.read_bytes() == b"mp4" and clips[0].duration_s == 10.0 and prov.attribution(clips[0]) == "Image by bo on Pixabay"
    assert await prov.search_videos("octopus sea ocean") == []  # all used: the scene falls back to a photo
    fresh = PixabayProvider("k", tmp_path / "cache", client=prov.client)
    assert [c.source_id for c in await fresh.search_videos("octopus blue blood")] == ["4", "3"]  # no full match: the subject alone


@needs_ffmpeg
def test_facts_job_cuts_stock_clips_every_two_seconds_and_burns_word_captions(tmp_path):
    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(s.ffmpeg_path), stock=FixtureStock(tmp_path / "cache", s.ffmpeg_path))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "x y z", "review_script": False, "format": "facts", "target_duration_s": 55}).json()["id"]
        d = run_to_rest(c, job_id)
        assert d["job"]["status"] == "AWAITING_APPROVAL", d["job"]["error_summary"]
        vis, vo = d["outputs"]["VISUALS"], d["outputs"]["VOICE"]
        assert vis["captions"] == "words" and all(a["kind"] == "stock_video" and a["licence"] == "pexels" for a in vis["assets"])
        assert all(a["end_ms"] - a["start_ms"] <= 2000 for a in vis["assets"])
        assert vis["assets"][0]["start_ms"] == 0 and vis["assets"][-1]["end_ms"] == vo["scenes"][-1]["end_ms"]
        assert all(a["end_ms"] == b["start_ms"] for a, b in zip(vis["assets"], vis["assets"][1:]))
        assert d["outputs"]["RENDER"]["subtitles_burned"] is True and d["outputs"]["SUBTITLES"]["ass_path"].endswith("vi.ass")
        tags = d["outputs"]["SCRIPT"]["hashtags"]
        assert tags[:3] == ["#suthatthuvi", "#khamphathegioi", "#learnontiktok"] and tags[-1] == "#viral" and len(tags) <= 8


@needs_ffmpeg
def test_a_scene_whose_search_runs_dry_reuses_earlier_clips_not_a_blank_card(tmp_path):
    class DryAfterOne(FixtureStock):
        calls = 0

        async def search_videos(self, query, n=3):
            DryAfterOne.calls += 1
            return await super().search_videos(query, n) if DryAfterOne.calls == 1 else []

        async def search_photo(self, query):
            return None

    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(s.ffmpeg_path), stock=DryAfterOne(tmp_path / "cache", s.ffmpeg_path))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "x y z", "review_script": False, "format": "facts", "target_duration_s": 55}).json()["id"]
        d = run_to_rest(c, job_id)
    assets = d["outputs"]["VISUALS"]["assets"]
    assert all(a["kind"] == "stock_video" for a in assets) and {a["source_id"] for a in assets} == {"fixture-0", "fixture-1"}
    later = [a for a in assets if a["scene_index"] > 0]
    assert later and all(a["clip_offset_s"] >= 1.0 for a in later)  # the reused clips play from their middle



@needs_ffmpeg
def test_a_scene_with_one_fresh_clip_mixes_in_earlier_ones_instead_of_looping_it(tmp_path):
    class OneAfterFirst(FixtureStock):
        calls = 0

        async def search_videos(self, query, n=3):
            OneAfterFirst.calls += 1
            clips = await super().search_videos(query, n)
            return clips if OneAfterFirst.calls == 1 else clips[:1]

    s = settings(tmp_path)
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(s.ffmpeg_path), stock=OneAfterFirst(tmp_path / "cache", s.ffmpeg_path))
    with TestClient(app) as c:
        job_id = c.post("/jobs", json={"topic": "x y z", "review_script": False, "format": "facts", "target_duration_s": 55}).json()["id"]
        d = run_to_rest(c, job_id)
    assets = d["outputs"]["VISUALS"]["assets"]
    for i in {a["scene_index"] for a in assets}:
        shots = [a for a in assets if a["scene_index"] == i]
        assert len(shots) < 2 or len({a["path"] for a in shots}) >= 2  # never one clip for every shot of a scene

class FakeHaiku:
    """Stands in for AsyncAnthropic: answers the clip check with a fixed keep list and records what it was shown."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []
        self.beta = self
        self.messages = self

    async def create(self, **kw):
        from types import SimpleNamespace as NS

        self.calls.append(kw)
        if isinstance(self.answer, Exception):
            raise self.answer
        return NS(model="claude-haiku-5-5", usage=NS(input_tokens=1000, output_tokens=20),
                  content=[NS(type="text", text=json.dumps({"keep": self.answer}))])


@pytest.mark.anyio
async def test_clip_check_drops_the_frames_haiku_rejects_and_degrades_to_tags_on_failure(tmp_path):
    from app.providers import ClipCheck

    def hit(i):
        return {"id": i, "pageURL": f"https://pixabay.com/videos/{i}", "user": "bo", "tags": "octopus, sea", "duration": 10,
                "videos": {"medium": {"url": f"https://cdn.test/{i}.mp4", "width": 1920, "height": 1080, "thumbnail": f"https://cdn.test/{i}.jpg"}}}

    def handler(request):
        if request.url.path == "/api/videos/":
            return httpx.Response(200, json={"hits": [hit(1), hit(2), hit(3)]})
        return httpx.Response(200, content=tiny_jpeg() if request.url.path.endswith(".jpg") else b"mp4")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://pixabay.com")
    haiku = FakeHaiku([2, 0, 7])  # frame 1 is the fairground ride; 7 does not exist
    check = ClipCheck(client=haiku)
    prov = PixabayProvider("k", tmp_path / "cache", client=client, check=check)
    assert [c.source_id for c in await prov.search_videos("octopus sea")] == ["3", "1"]  # Haiku's order, 2 dropped
    sent = haiku.calls[0]
    assert sent["model"] == "claude-haiku-5-5" and sum(b["type"] == "image" for b in sent["messages"][0]["content"]) == 3
    assert abs(check.cost_usd - (1000 * 0.10 + 20 * 0.50) / 1e6) < 1e-12

    judge = FakeHaiku([0])
    narrow = PixabayProvider("k", tmp_path / "cache3", client=client, check=ClipCheck(client=judge))
    await narrow.search_videos("octopus blue blood")  # no full tag match: retried as "octopus", still judged as the full scene
    assert '"octopus blue blood"' in judge.calls[-1]["messages"][0]["content"][-1]["text"]

    broken = PixabayProvider("k", tmp_path / "cache2", client=client, check=ClipCheck(client=FakeHaiku(RuntimeError("down"))))
    assert [c.source_id for c in await broken.search_videos("octopus sea")] == ["1", "2", "3"]  # the check failing never costs the clips


@needs_ffmpeg
def test_upload_copy_fits_the_browser_upload_limit(tmp_path):
    src = tmp_path / "big.mp4"  # noise does not compress: about 1 MB for 3 s
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "nullsrc=s=540x960:r=30,geq=random(1)*255:128:128", "-f", "lavfi",
                    "-i", "sine=frequency=300", "-t", "3", "-c:v", "libx264", "-crf", "18", "-shortest", str(src)], check=True)
    assert src.stat().st_size > 300_000
    size = media.upload_copy(FFMPEG, FFPROBE, src, tmp_path / "up.mp4", max_bytes=300_000)
    assert size <= 300_000 and abs(media.duration_s(FFPROBE, tmp_path / "up.mp4") - 3) < 0.2
    small = media.upload_copy(FFMPEG, FFPROBE, tmp_path / "up.mp4", tmp_path / "same.mp4", max_bytes=300_000)
    assert small == size  # already small enough: copied as is
