"""ElevenLabs TTS, offline: mocked API. Character timings become words and sentences, and the captions follow them."""
import base64
import json

import httpx
import pytest

from app import media
from app.config import Settings
from app.providers import ElevenLabsTTS, ProviderRejected, build_tts

VOICE = "AbCdEfGhIjKlMnOpQrSt"


def alignment(text: str, per_char_s: float = 0.1) -> dict:
    return {"characters": list(text), "character_start_times_seconds": [i * per_char_s for i in range(len(text))],
            "character_end_times_seconds": [(i + 1) * per_char_s for i in range(len(text))]}


def test_build_tts_needs_key_and_voice():
    with pytest.raises(ValueError, match="ELEVENLABS_VOICE_ID"):
        build_tts(Settings(_env_file=None, tts_provider="elevenlabs", elevenlabs_api_key="k"))


@pytest.mark.anyio
async def test_words_and_sentences_come_from_character_timings(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"audio_base64": base64.b64encode(b"mp3").decode(), "alignment": alignment(body["text"])})

    tts = ElevenLabsTTS("k", VOICE, "eleven_multilingual_v2", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await tts.synthesize("Số 1. Ba tim!", "vi", tmp_path / "s0.mp3", voice="vi-VN-HoaiMyNeural")  # an Edge name: ignored
    req = seen[0]
    assert req.url.path == f"/v1/text-to-speech/{VOICE}/with-timestamps" and req.headers["xi-api-key"] == "k"
    assert json.loads(req.content)["model_id"] == "eleven_multilingual_v2" and (tmp_path / "s0.mp3").read_bytes() == b"mp3"
    assert [s["text"] for s in res.sentences] == ["Số 1.", "Ba tim!"]
    assert res.sentences[0]["words"] == [{"text": "Số", "start_ms": 0, "end_ms": 200}, {"text": "1.", "start_ms": 300, "end_ms": 500}]
    assert (res.sentences[1]["start_ms"], res.sentences[1]["end_ms"]) == (600, 1300)


@pytest.mark.anyio
async def test_bad_key_or_quota_is_permanent(tmp_path):
    tts = ElevenLabsTTS("bad", VOICE, "m", client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="quota_exceeded"))))
    with pytest.raises(ProviderRejected, match="quota"):
        await tts.synthesize("Một câu.", "vi", tmp_path / "x.mp3")


def test_captions_follow_real_word_timings(tmp_path):
    out = tmp_path / "w.ass"
    words = [{"text": "Ba", "start_ms": 1000, "end_ms": 1200}, {"text": "trái", "start_ms": 1500, "end_ms": 1800},
             {"text": "tim.", "start_ms": 2400, "end_ms": 2900}]
    media.write_word_captions([{"text": "Ba trái tim.", "start_ms": 1000, "end_ms": 3000, "words": words}], out)
    times = [tuple(l.split(",")[1:3]) for l in out.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    assert times == [("0:00:01.00", "0:00:01.50"), ("0:00:01.50", "0:00:02.40"), ("0:00:02.40", "0:00:03.00")]


@pytest.fixture
def anyio_backend():
    return "asyncio"
