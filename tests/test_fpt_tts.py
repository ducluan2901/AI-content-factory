"""FPT.AI TTS, offline: mocked API (link not ready at first), real FFmpeg for clip lengths and the join."""
import json
import os
import shutil
import subprocess

import httpx
import pytest

from app.config import Settings
from app.providers import FptTTS, ProviderRejected, build_tts

FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")


def test_sentences_split_and_keep_the_api_minimum():
    assert FptTTS.sentences("Tủ lạnh có mùi? Xịt thơm chỉ che. Ừ. Bỏ đi!") == ["Tủ lạnh có mùi?", "Xịt thơm chỉ che.", "Ừ. Bỏ đi!"]
    assert FptTTS.sentences("Ừ.") == ["Ừ."]  # a lone short line is still voiced


def test_build_tts_needs_the_key():
    with pytest.raises(ValueError, match="FPT_AI_API_KEY"):
        build_tts(Settings(_env_file=None, tts_provider="fpt"))


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="FFmpeg not found")
@pytest.mark.anyio
async def test_each_sentence_is_voiced_waited_for_and_joined_with_exact_timings(tmp_path):
    clip = tmp_path / "tone.mp3"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=24000", "-t", "1.5", str(clip)], check=True)
    posts, polls = [], {}

    def handler(request):
        if request.method == "POST":
            assert request.headers["api_key"] == "k" and request.headers["voice"] == "lannhi" and request.headers["speed"] == "1"
            posts.append(request.content.decode())
            return httpx.Response(200, json={"error": 0, "async": f"https://files.test/{len(posts)}.mp3", "request_id": "r", "message": "wait"})
        n = polls[request.url.path] = polls.get(request.url.path, 0) + 1
        return httpx.Response(404) if n == 1 else httpx.Response(200, content=clip.read_bytes())  # not ready on the first look

    tts = FptTTS("k", "banmai", 1, FFMPEG, FFPROBE, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), poll_s=0)
    res = await tts.synthesize("Câu một nè. Câu hai nè!", "vi", tmp_path / "s0.mp3", voice="lannhi")
    assert posts == ["Câu một nè.", "Câu hai nè!"] and all(n == 2 for n in polls.values())
    one = res.sentences[0]["end_ms"]
    assert abs(one - 1500) < 120 and res.sentences[1]["start_ms"] == one and abs(res.sentences[1]["end_ms"] - 2 * one) < 5
    total = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(res.audio_path)],
                                 capture_output=True, text=True).stdout)
    assert abs(total - 3.0) < 0.15


@pytest.mark.anyio
async def test_bad_key_is_permanent_and_english_is_refused(tmp_path):
    tts = FptTTS("bad", "banmai", 0, "ffmpeg", "ffprobe", client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(ProviderRejected, match="refused the key"):
        await tts.synthesize("Một câu đủ dài.", "vi", tmp_path / "x.mp3")
    with pytest.raises(ProviderRejected, match="Vietnamese only"):
        await tts.synthesize("Hello there.", "en", tmp_path / "y.mp3")


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="FFmpeg not found")
@pytest.mark.anyio
async def test_marketplace_key_uses_the_openai_style_endpoint(tmp_path):
    clip = tmp_path / "tone.mp3"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=24000", "-t", "1", str(clip)], check=True)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=clip.read_bytes(), headers={"content-type": "audio/mpeg"})

    tts = FptTTS("sk-abc", "std_quanganh", 0, FFMPEG, FFPROBE, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await tts.synthesize("Câu một nè. Câu hai nè!", "vi", tmp_path / "s0.mp3")
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == FptTTS.MKP_URL and seen[0].headers["authorization"] == "Bearer sk-abc"
    assert (body["model"], body["input"], body["voice"], body["speed"]) == ("FPT.TTS-pro", "Câu một nè.", "std_quanganh", 1)
    assert len(seen) == 2 and abs(res.sentences[1]["end_ms"] - 2000) < 150
