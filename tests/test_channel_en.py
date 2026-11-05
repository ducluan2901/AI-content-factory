"""The global (English) channel: Vietnamese-only FPT routes English jobs to Edge, and the facts guide switches by language."""
from pathlib import Path

import pytest

from app.pipeline import guide_name
from app.providers import LangRouterTTS, ProviderRejected, TTSResult


class Stub:
    def __init__(self, name, lang):
        self.name, self.voices, self.calls = name, {lang: f"{name}-voice"}, []

    async def synthesize(self, text, language, out, voice=None):
        self.calls.append(language)
        return TTSResult(out, [])


@pytest.mark.anyio
async def test_router_sends_each_language_to_its_provider(tmp_path):
    vi, en = Stub("fpt", "vi"), Stub("edge_tts", "en")
    r = LangRouterTTS({"vi": vi, "en": en})
    await r.synthesize("Xin chào", "vi", tmp_path / "a.mp3")
    await r.synthesize("Hello", "en", tmp_path / "b.mp3")
    assert (vi.calls, en.calls) == (["vi"], ["en"])
    assert r.voices == {"vi": "fpt-voice", "en": "edge_tts-voice"} and r.name == "fpt+edge_tts"
    with pytest.raises(ProviderRejected):
        await r.synthesize("Hola", "es", tmp_path / "c.mp3")


def test_facts_guide_switches_by_language(tmp_path):
    prompts = Path(__file__).resolve().parents[1] / "app" / "prompts"
    assert guide_name("facts", "en", prompts) == "format_facts_en"
    assert guide_name("facts", "vi", prompts) == "format_facts"
    assert guide_name("comedy", "en", prompts) == "format_comedy"  # no English comedy guide yet: the shared one
    assert guide_name("unknown", "en", tmp_path) == "format_facts"


def test_cache_files_are_written_whole(tmp_path):
    from app.providers import save_atomic
    f = tmp_path / "clip.mp4"
    save_atomic(f, b"abc")
    save_atomic(f, b"abcd")  # an existing file is replaced, and no .part file is left behind
    assert f.read_bytes() == b"abcd" and [p.name for p in tmp_path.iterdir()] == ["clip.mp4"]
