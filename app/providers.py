"""Provider ports and adapters. Business code depends on these classes, never on a vendor SDK."""
import asyncio
import base64
import hashlib
import io
import json
import logging
import math
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import Settings

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Transient or provider-side failure; the runner may retry it."""


class ProviderRejected(Exception):
    """Permanent failure (bad key, bad request, content refusal). Never retried: a human must act."""


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    cost_usd: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None


@dataclass
class TTSResult:
    audio_path: Path
    sentences: list[dict] = field(default_factory=list)  # {text, start_ms, end_ms} relative to this clip


def extract_json(text: str) -> dict:
    """LLMs wrap JSON in prose or code fences; take the outermost object."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in LLM response")
    return json.loads(m.group(0))


# USD per 1M tokens (input, output), first-party API rates. Used for stage_run.cost_usd (PRD S6).
PRICES = {"claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00),
          "claude-fable-5-1": (10.00, 50.00)}


class AnthropicApiProvider:
    """Default generator: the Anthropic API, billed per call. Key from ANTHROPIC_API_KEY."""

    name = "anthropic_api"

    def __init__(self, model: str, api_key: str | None = None, effort: str = "medium", client=None):
        import anthropic

        self._anthropic = anthropic
        self.model, self.effort = model, effort
        self.client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=3)

    async def complete(self, prompt: str, timeout_s: int = 300) -> LLMResponse:
        a = self._anthropic
        try:
            resp = await self.client.with_options(timeout=timeout_s).beta.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": self.effort},
                # on a safety decline the API re-runs the request on a fallback model it picks
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (a.RateLimitError, a.InternalServerError, a.APIConnectionError, a.APITimeoutError) as e:
            raise ProviderError(f"anthropic API transient error: {e}") from e
        except a.APIStatusError as e:  # 400/401/403/404: retrying cannot help
            raise ProviderRejected(f"anthropic API {e.status_code}: {e.message}") from e
        if resp.stop_reason == "refusal":
            cat = resp.stop_details.category if resp.stop_details else None
            raise ProviderRejected(f"model declined the request (category {cat})")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("response hit max_tokens before finishing")
        u = resp.usage
        p_in, p_out = PRICES.get(resp.model, (0.0, 0.0))  # unknown fallback model: cost shows as 0, model is still recorded
        return LLMResponse(
            text="".join(b.text for b in resp.content if b.type == "text"),
            provider=self.name,
            model=resp.model,
            cost_usd=(u.input_tokens * p_in + u.output_tokens * p_out) / 1e6,
            tokens_in=u.input_tokens,
            tokens_out=u.output_tokens,
        )


class ClaudeCliProvider:
    """dev-only: headless `claude -p` on the founder's subscription (PRD §8). Concurrency 1.
    Only for trying prompts during development; it spends the founder's Max quota."""

    name = "claude_cli"
    _lock = asyncio.Lock()

    def __init__(self, settings: Settings):
        self.exe = settings.claude_cli_path
        self.model = settings.claude_model

    def _call(self, prompt: str, timeout_s: int) -> LLMResponse:
        cmd = [self.exe, "-p", "--output-format", "json", "--max-turns", "1"]
        if self.model:
            cmd += ["--model", self.model]
        # prompt via stdin: avoids Windows command-line length and encoding limits
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout_s)
        except FileNotFoundError as e:
            raise ProviderError(f"claude CLI not found at {self.exe!r}") from e
        except subprocess.TimeoutExpired as e:
            raise ProviderError(f"claude CLI timed out after {timeout_s}s") from e
        if proc.returncode != 0 and not proc.stdout:
            raise ProviderError(f"claude exited {proc.returncode}: {proc.stderr[-500:]}")
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise ProviderError(f"claude returned non-JSON: {proc.stdout[:300]}") from e
        if out.get("is_error"):
            raise ProviderError(f"claude error: {out.get('result')}")
        usage = out.get("usage") or {}
        return LLMResponse(
            text=out.get("result", ""),
            provider=self.name,
            model=next(iter(out.get("modelUsage") or {}), self.model or "default"),
            # API-equivalent cost reported by the CLI; the subscription's marginal cost is $0
            cost_usd=float(out.get("total_cost_usd") or 0),
            tokens_in=usage.get("input_tokens"),
            tokens_out=usage.get("output_tokens"),
        )

    async def complete(self, prompt: str, timeout_s: int = 300) -> LLMResponse:
        async with self._lock:
            return await asyncio.to_thread(self._call, prompt, timeout_s)


class FixtureLLM:
    """Replays a recorded response file: offline, deterministic, free."""

    name = "fixture"

    def __init__(self, path: Path):
        self.path = path

    async def complete(self, prompt: str, timeout_s: int = 300) -> LLMResponse:
        return LLMResponse(text=self.path.read_text(encoding="utf-8"), provider=self.name, model=self.path.name)


class EdgeTTS:
    """Free Microsoft Edge neural voices. Unofficial endpoint: can break without notice (BACKLOG D.1)."""

    name = "edge_tts"

    def __init__(self, voices: dict[str, str], rate: str = "+0%"):
        self.voices, self.rate = voices, rate

    async def synthesize(self, text: str, language: str, out: Path, voice: str | None = None) -> TTSResult:
        import edge_tts

        last: Exception | None = None
        # the endpoint intermittently returns NoAudioReceived for valid text; retry per call, not per stage
        for attempt in range(5):
            sentences = []
            try:
                comm = edge_tts.Communicate(text, voice or self.voices[language], rate=self.rate, boundary="SentenceBoundary")
                with out.open("wb") as f:
                    async for chunk in comm.stream():
                        if chunk["type"] == "audio":
                            f.write(chunk["data"])
                        elif chunk["type"] == "SentenceBoundary":
                            start = chunk["offset"] // 10_000  # 100 ns ticks -> ms
                            sentences.append({"text": chunk["text"], "start_ms": start, "end_ms": start + chunk["duration"] // 10_000})
                return TTSResult(out, sentences)
            except Exception as e:  # network / endpoint errors are transient
                last = e
                await asyncio.sleep(1.5 * (attempt + 1))
        raise ProviderError(f"edge-tts failed after 5 tries: {last}") from last


class FptTTS:
    """FPT.AI text-to-speech v5: native Vietnamese voices from three regions. The API returns a link that becomes
    downloadable after 5 s to 2 min and gives no timings, so each sentence is voiced on its own and the clips are
    joined: every sentence then has an exact start for the per-sentence cards."""

    name = "fpt_tts"
    URL = "https://api.fpt.ai/hmi/tts/v5"
    MKP_URL = "https://mkp-api.fptcloud.com/v1/audio/speech"  # FPT AI Marketplace, model FPT.TTS-pro
    VOICES = {"banmai", "lannhi", "leminh", "myan", "thuminh", "giahuy", "linhsan",  # v5
              "fci_vietkhoa", "fci_xuantoan", "fci_phuongan", "std_banmai", "std_quanganh"}  # FPT.TTS-pro

    def __init__(self, api_key: str, voice: str, speed: int, ffmpeg: str, ffprobe: str, client: httpx.AsyncClient | None = None,
                 poll_s: float = 1.5, wait_s: float = 150):
        self.api_key, self.voice, self.speed, self.ffmpeg, self.ffprobe = api_key, voice, speed, ffmpeg, ffprobe
        self.marketplace = api_key.startswith("sk-")  # FPT AI Marketplace keys; console.fpt.ai keys have no prefix
        self.voices = {"vi": voice}
        self.client = client or httpx.AsyncClient(timeout=30)
        self.poll_s, self.wait_s = poll_s, wait_s

    @staticmethod
    def sentences(text: str) -> list[str]:
        """Split at sentence ends; a fragment under 3 characters (the API minimum) joins the next one."""
        parts, carry = [], ""
        for s in re.split(r"(?<=[.!?…])\s+", text.strip()):
            s = f"{carry} {s}".strip() if carry else s
            if len(s) < 3:
                carry = s
            else:
                parts.append(s)
                carry = ""
        if carry:
            parts = parts[:-1] + [f"{parts[-1]} {carry}"] if parts else [carry]
        return parts

    async def _voice_one(self, sentence: str, voice: str, out: Path) -> None:
        if self.marketplace:  # OpenAI-style speech endpoint: answers with the audio itself
            r = await self.client.post(self.MKP_URL, headers={"Authorization": f"Bearer {self.api_key}"},
                                       json={"model": "FPT.TTS-pro", "input": sentence, "voice": voice, "response_format": "mp3",
                                             "speed": 1 + 0.1 * self.speed})
            if r.status_code in (401, 403):
                raise ProviderRejected(f"FPT AI Marketplace refused the key ({r.status_code}); check FPT_AI_API_KEY")
            if r.status_code >= 400 or not r.content:
                raise ProviderError(f"FPT AI Marketplace HTTP {r.status_code}: {r.text[:200]}")
            out.write_bytes(r.content)
            return
        r = await self.client.post(self.URL, content=sentence.encode("utf-8"),
                                   headers={"api_key": self.api_key, "voice": voice, "speed": str(self.speed), "format": "mp3"})
        if r.status_code in (401, 403):
            raise ProviderRejected(f"FPT.AI refused the key ({r.status_code}); check FPT_AI_API_KEY")
        if r.status_code >= 400:
            raise ProviderError(f"FPT.AI HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        if body.get("error") != 0 or not body.get("async"):
            raise ProviderError(f"FPT.AI error {body.get('error')}: {body.get('message')}")
        end = time.monotonic() + self.wait_s
        while time.monotonic() < end:  # the docs do not say what the link returns before it is ready: anything but a non-empty 200 means wait
            a = await self.client.get(body["async"], follow_redirects=True)
            if a.status_code == 200 and a.content:
                out.write_bytes(a.content)
                return
            await asyncio.sleep(self.poll_s)
        raise ProviderError(f"FPT.AI audio not ready after {self.wait_s:.0f} s (request {body.get('request_id')})")

    def _duration_ms(self, path: Path) -> int:
        out = subprocess.run([self.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, check=True).stdout
        return int(float(out) * 1000)

    async def synthesize(self, text: str, language: str, out: Path, voice: str | None = None) -> TTSResult:
        if language != "vi":
            raise ProviderRejected("FPT.AI voices are Vietnamese only")
        voice = voice if voice in self.VOICES else self.voice
        clips, timings, t = [], [], 0
        for i, s in enumerate(self.sentences(text)):
            clip = out.with_name(f"{out.stem}_{i:02}.mp3")
            await self._voice_one(s, voice, clip)
            d = self._duration_ms(clip)
            clips.append(clip)
            timings.append({"text": s, "start_ms": t, "end_ms": t + d})
            t += d
        args = [self.ffmpeg, "-y", "-v", "error"] + [x for c in clips for x in ("-i", str(c))]
        args += ["-filter_complex", "".join(f"[{i}:a]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=0:a=1[a]", "-map", "[a]", str(out)]
        await asyncio.to_thread(subprocess.run, args, check=True)
        return TTSResult(out, timings)


class ElevenLabsTTS:
    """ElevenLabs text-to-speech: expressive multilingual voices, and a timing for every character, so the word-by-word
    captions follow each spoken word exactly instead of an estimate. One request per scene; the voice is a voice id
    from your ElevenLabs voice library (VoiceLab: the ID button on a voice)."""

    name = "elevenlabs"
    URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"

    def __init__(self, api_key: str, voice_id: str, model: str, client: httpx.AsyncClient | None = None):
        self.api_key, self.voice_id, self.model = api_key, voice_id, model
        self.voices = {"vi": voice_id, "en": voice_id}
        self.client = client or httpx.AsyncClient(timeout=90)

    @staticmethod
    def timings(alignment: dict) -> list[dict]:
        """Words from the character timings, grouped into sentences after . ! ? or …"""
        words, cur = [], None
        for ch, a, b in zip(alignment["characters"], alignment["character_start_times_seconds"], alignment["character_end_times_seconds"]):
            if ch.isspace():
                cur = None
                continue
            if cur is None:
                cur = {"text": "", "start_ms": int(a * 1000)}
                words.append(cur)
            cur["text"] += ch
            cur["end_ms"] = int(b * 1000)
        sentences, cur = [], []
        for w in words:
            cur.append(w)
            if w["text"][-1] in ".!?…":
                sentences.append(cur)
                cur = []
        sentences += [cur] if cur else []
        return [{"text": " ".join(w["text"] for w in s), "start_ms": s[0]["start_ms"], "end_ms": s[-1]["end_ms"], "words": s} for s in sentences]

    async def synthesize(self, text: str, language: str, out: Path, voice: str | None = None) -> TTSResult:
        voice = voice if voice and re.fullmatch(r"[A-Za-z0-9]{20}", voice) else self.voice_id  # ignore another provider's voice name
        r = await self.client.post(self.URL.format(voice=voice), params={"output_format": "mp3_44100_128"},
                                   headers={"xi-api-key": self.api_key}, json={"text": text, "model_id": self.model})
        if r.status_code in (401, 403):
            raise ProviderRejected(f"ElevenLabs refused the request ({r.status_code}: {r.text[:200]}); check ELEVENLABS_API_KEY and your quota")
        if r.status_code >= 400:
            raise ProviderError(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        if not body.get("alignment"):
            raise ProviderError("ElevenLabs returned no timings")
        out.write_bytes(base64.b64decode(body["audio_base64"]))
        return TTSResult(out, self.timings(body["alignment"]))


class FixtureTTS:
    """Silent audio sized like real speech (~3 syllables/s, the vi delivery rate), so tests run offline."""

    name = "fixture"

    def __init__(self, ffmpeg: str):
        self.ffmpeg = ffmpeg

    async def synthesize(self, text: str, language: str, out: Path, voice: str | None = None) -> TTSResult:
        dur = max(1.0, len(text.split()) / 3.0)
        cmd = [self.ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{dur:.2f}", "-c:a", "libmp3lame", str(out)]
        await asyncio.to_thread(subprocess.run, cmd, check=True)
        return TTSResult(out, [{"text": text, "start_ms": 0, "end_ms": int(dur * 1000)}])


def build_llm(s: Settings):
    if s.llm_provider == "anthropic_api":
        if not s.anthropic_api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic_api needs ANTHROPIC_API_KEY in .env or the environment")
        return AnthropicApiProvider(s.script_model, s.anthropic_api_key.get_secret_value(), s.script_effort)
    if s.llm_provider == "claude_cli":
        return ClaudeCliProvider(s)
    if s.llm_provider == "fixture":
        return FixtureLLM(s.fixture_dir / "llm" / "script_vi.json")
    raise ValueError(f"unknown LLM_PROVIDER {s.llm_provider}")


@dataclass
class StockPhoto:
    path: Path
    source_id: str
    source_url: str | None  # None for your own files: a local path must never reach the client bundle
    author: str
    author_url: str | None = None
    duration_s: float | None = None  # stock video clips only


class PexelsProvider:
    """Free stock photos, one per scene, searched by the scene's own visual_query (BACKLOG 3.1).
    Never raises for "no result" or a transient problem - the caller falls back to a title card (BACKLOG R4)."""

    name = "pexels"
    licence = "pexels"
    licence_url = "https://www.pexels.com/license/"

    @staticmethod
    def attribution(photo: "StockPhoto") -> str:
        return f"Photo by {photo.author} on Pexels"

    def __init__(self, api_key: str, cache_dir: Path, client: httpx.AsyncClient | None = None):
        self.api_key, self.cache_dir = api_key, cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.AsyncClient(base_url="https://api.pexels.com/v1", timeout=15)

    async def search_photo(self, query: str) -> StockPhoto | None:
        try:
            resp = await self.client.get("/search", headers={"Authorization": self.api_key},
                                         params={"query": query, "per_page": 1, "orientation": "portrait"})
            resp.raise_for_status()
            photos = resp.json().get("photos") or []
            if not photos:
                return None
            photo = photos[0]
            url = photo["src"]["large2x"]
            cached = self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.jpg"
            if not cached.is_file():
                img = await self.client.get(url, follow_redirects=True)
                img.raise_for_status()
                cached.write_bytes(img.content)
            return StockPhoto(cached, str(photo["id"]), photo["url"], photo.get("photographer") or "Pexels contributor", photo.get("photographer_url"))
        except Exception:  # noqa: BLE001 - missing stock is a graceful degrade, never a stage failure
            log.warning("pexels search failed for %r", query, exc_info=True)
            return None


class ClipCheck:
    """Claude Haiku looks at a stock search's candidate frames and keeps those that really show the scene's subject:
    Pixabay tags a fairground ride, a shark and grilled squid "octopus". One call per search, frames shrunk to 320 px
    (about 8 x 100 tokens, ~$0.0002). Returns None on any failure, and the caller keeps the tag ranking."""

    MODEL = "claude-haiku-5-5"

    def __init__(self, api_key: str | None = None, client=None):
        import anthropic

        self.client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=2)
        self.cost_usd = 0.0  # running total, read by the visuals stage

    @staticmethod
    def _small(image: bytes) -> str:
        from PIL import Image

        img = Image.open(io.BytesIO(image)).convert("RGB")
        img.thumbnail((320, 320))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()

    async def keep(self, query: str, frames: list[bytes]) -> list[int] | None:
        """Indexes of the frames that show the subject, best first."""
        content = []
        for i, f in enumerate(frames):
            content += [{"type": "text", "text": f"Frame {i}:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": self._small(f)}}]
        content.append({"type": "text", "text": (
            f'Each frame is from a stock video clip found for a short-video scene described as "{query}". Keep only frames '
            "whose main subject is that, as real footage: not a toy, ride, statue, drawing, logo, or the thing cooked as food, "
            "unless the description asks for it, and not a different animal or object. Order the kept frames best first. "
            'Answer with JSON only: {"keep": [frame numbers]}')})
        try:
            resp = await self.client.beta.messages.create(model=self.MODEL, max_tokens=300, thinking={"type": "disabled"},
                                                          output_config={"effort": "low"}, messages=[{"role": "user", "content": content}])
            p_in, p_out = PRICES.get(resp.model, PRICES[self.MODEL])
            self.cost_usd += (resp.usage.input_tokens * p_in + resp.usage.output_tokens * p_out) / 1e6
            keep = extract_json("".join(b.text for b in resp.content if b.type == "text")).get("keep", [])
            return list(dict.fromkeys(i for i in keep if isinstance(i, int) and 0 <= i < len(frames)))
        except Exception:  # noqa: BLE001 - an unchecked clip is a degrade, never a stage failure
            log.warning("clip check failed for %r", query, exc_info=True)
            return None


class PixabayProvider:
    """Free stock photos from Pixabay (free key, issued at once on sign-up; Pexels has paused new keys).
    Pixabay Content License: free use, no attribution required. Images are downloaded, never hot-linked.
    Never raises for "no result" or a transient problem - the caller falls back to a title card (BACKLOG R4)."""

    name = "pixabay"
    licence = "pixabay"
    licence_url = "https://pixabay.com/service/license-summary/"
    STOPWORDS = {"a", "an", "the", "of", "in", "on", "at", "under", "with", "and", "to", "for", "by", "from"}

    @staticmethod
    def attribution(photo: "StockPhoto") -> str:
        return f"Image by {photo.author} on Pixabay"

    CHECK_MAX = 8  # clip candidates looked at per search

    def __init__(self, api_key: str, cache_dir: Path, client: httpx.AsyncClient | None = None, check: "ClipCheck | None" = None):
        self.api_key, self.cache_dir, self.check = api_key, cache_dir, check
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.AsyncClient(base_url="https://pixabay.com", timeout=15)
        # shortcut: remembered per process, so a restart may reuse a photo; fine at a few videos a day
        self.used: set[str] = set()

    @staticmethod
    def _grey(image: bytes) -> bool:
        """Black-and-white footage looks like a different video next to colour: judged on a small preview."""
        from PIL import Image

        sat = Image.open(io.BytesIO(image)).convert("HSV").getchannel("S")
        return sum(sat.getdata()) / (sat.width * sat.height) < 25

    async def _is_grey(self, preview_url: str) -> bool:
        r = await self.client.get(preview_url, follow_redirects=True)
        r.raise_for_status()
        return self._grey(r.content)

    def _ranked(self, query: str, hits: list[dict], bonus=lambda h: 0) -> list[dict]:
        """Pixabay ranks loosely ("tv remote under pillow" -> a yacht): keep hits whose tags cover >= 60% of the content
        words, best coverage first; "asian" only ranks ("asian boy dishes" must not settle for an asian boy on a beach).
        The first content word is the subject (the prompt puts the object first) and must be a tag: "octopus close up"
        matched a perfume clip on "close" and "up" alone."""
        words = [w.rstrip("s") for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in self.STOPWORDS]
        want = set(words)
        core = want - {"asian"}
        subject = next((w for w in words if w != "asian"), None)
        scored = []
        for i, h in enumerate(hits):
            tags = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", h.get("tags", "").lower())}
            if core and subject in tags and sum(1 for w in core if w in tags) >= math.ceil(0.6 * len(core)):
                scored.append((-len(want & tags) - bonus(h), i, h))
        return [h for _, _, h in sorted(scored, key=lambda x: x[:2])]

    async def search_videos(self, query: str, n: int = 3, scene: str | None = None) -> list[StockPhoto]:
        """Up to n colour clips of at least 3 s not used before, upright ones first (less is cropped away for 9:16).
        Empty on no result or any failure: the caller falls back to a photo (BACKLOG R4)."""
        try:
            resp = await self.client.get("/api/videos/", params={"key": self.api_key, "q": query[:100], "video_type": "film",
                                                                 "safesearch": "true", "per_page": 50})
            resp.raise_for_status()
            hits = [h for h in resp.json().get("hits") or [] if h.get("duration", 0) >= 3 and h.get("videos", {}).get("medium", {}).get("url")]
            cands = []  # (hit, thumbnail bytes): fresh colour clips, best tag match first
            for h in self._ranked(query, hits, bonus=lambda h: h["videos"]["medium"].get("height", 0) > h["videos"]["medium"].get("width", 1)):
                if str(h["id"]) in self.used:
                    continue
                thumb = None
                if url := h["videos"]["medium"].get("thumbnail"):
                    r = await self.client.get(url, follow_redirects=True)
                    r.raise_for_status()
                    thumb = r.content
                    if self._grey(thumb):
                        continue
                cands.append((h, thumb))
                if len(cands) == self.CHECK_MAX:
                    break
            # tags lie (an octopus-shaped fairground ride is tagged "octopus"): a model looks at the frames when configured
            if self.check and (seen := [c for c in cands if c[1]]):
                keep = await self.check.keep(scene or query, [t for _, t in seen])
                if keep is not None:
                    cands = [seen[i] for i in keep]
            out = []
            for h, _ in cands:
                cached = self.cache_dir / f"pixabay-v{h['id']}.mp4"
                if not cached.is_file():
                    clip = await self.client.get(h["videos"]["medium"]["url"], follow_redirects=True)
                    clip.raise_for_status()
                    cached.write_bytes(clip.content)
                self.used.add(str(h["id"]))
                out.append(StockPhoto(cached, str(h["id"]), h["pageURL"], h.get("user") or "Pixabay contributor", duration_s=float(h["duration"])))
                if len(out) == n:
                    break
            # nothing for the full query ("octopus close up"): any fresh clip of the subject beats a blank card
            if not out and (words := [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in self.STOPWORDS | {"asian"}])[1:]:
                return await self.search_videos(words[0], n, scene=query)  # judged against the full scene: "man" alone let a man walking pass for "man waking up bed"
            return out
        except Exception:  # noqa: BLE001 - missing stock is a graceful degrade, never a stage failure
            log.warning("pixabay video search failed for %r", query, exc_info=True)
            return []

    async def search_photo(self, query: str) -> StockPhoto | None:
        """The best-ranked colour photo not used before (two scenes with similar queries got the same photo)."""
        try:
            resp = await self.client.get("/api/", params={"key": self.api_key, "q": query[:100], "image_type": "photo",
                                                          "orientation": "vertical", "safesearch": "true", "per_page": 20})
            resp.raise_for_status()
            hit = None
            for h in self._ranked(query, resp.json().get("hits") or []):
                if str(h["id"]) not in self.used and not await self._is_grey(h["previewURL"]):
                    hit = h
                    break
            if hit is None:
                return None
            self.used.add(str(hit["id"]))
            url = hit["largeImageURL"]
            cached = self.cache_dir / f"pixabay-{hit['id']}.jpg"
            if not cached.is_file():
                img = await self.client.get(url, follow_redirects=True)
                img.raise_for_status()
                cached.write_bytes(img.content)
            return StockPhoto(cached, str(hit["id"]), hit["pageURL"], hit.get("user") or "Pixabay contributor")
        except Exception:  # noqa: BLE001 - missing stock is a graceful degrade, never a stage failure
            log.warning("pixabay search failed for %r", query, exc_info=True)
            return None


class FixtureStock:
    """Offline twin: a small deterministic local image, so tests exercise the stock-asset path for free."""

    name = "fixture"
    licence = "pexels"  # stands in for a real stock licence so quality-gate tests exercise that path too
    licence_url = "https://www.pexels.com/license/"

    @staticmethod
    def attribution(photo: "StockPhoto") -> str:
        return f"Photo by {photo.author} (fixture)"

    def __init__(self, cache_dir: Path, ffmpeg: str | None = None):
        self.cache_dir, self.ffmpeg = cache_dir, ffmpeg
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def search_videos(self, query: str, n: int = 3) -> list[StockPhoto]:
        """Two 4 s landscape test-pattern clips when given FFmpeg, else none (photo fallback)."""
        if not self.ffmpeg:
            return []
        out = []
        for k in range(min(n, 2)):
            path = self.cache_dir / f"fixture-{hashlib.sha256(query.encode()).hexdigest()[:8]}-{k}.mp4"
            if not path.is_file():
                subprocess.run([self.ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-t", "4", str(path)], check=True)
            out.append(StockPhoto(path, f"fixture-{k}", "https://example.com/fixture-video", "Fixture Videographer", duration_s=4.0))
        return out

    async def search_photo(self, query: str) -> StockPhoto | None:
        from PIL import Image

        path = self.cache_dir / f"fixture-{hashlib.sha256(query.encode()).hexdigest()[:8]}.jpg"
        if not path.is_file():
            Image.new("RGB", (1200, 1800), (60, 90, 120)).save(path)
        return StockPhoto(path, "fixture", "https://example.com/fixture-photo", "Fixture Photographer")


def _tokens(text: str) -> set[str]:
    """Lower-case words of 3+ letters with Vietnamese diacritics removed, so 'mèo' matches 'meo'."""
    plain = unicodedata.normalize("NFKD", text.lower().replace("đ", "d")).encode("ascii", "ignore").decode()
    return {w for w in re.split(r"[^a-z0-9]+", plain) if len(w) >= 3}


class LocalStock:
    """Your own images in one folder, picked by file name: 'cat-kitchen-night.jpg' matches the query
    'cat in a kitchen at night'. The best match wins, ties go to the first file name; no match returns None and
    the scene gets a title card, never a random unrelated photo. Every file is recorded as licence 'owned', which is
    your statement that you have the right to use it: only put images there that you own or are licensed to use."""

    name = "local"
    licence = "owned"
    licence_url = None
    EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

    def __init__(self, folder: Path):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def attribution(photo: "StockPhoto") -> str | None:
        return None

    async def search_photo(self, query: str) -> StockPhoto | None:
        want = _tokens(query)
        best, best_score = None, 0
        for f in sorted(self.folder.iterdir(), key=lambda p: p.name.lower()):
            if f.suffix.lower() in self.EXTENSIONS and f.is_file() and (score := len(want & _tokens(f.stem))) > best_score:
                best, best_score = f, score
        if best is None:
            return None
        return StockPhoto(best, best.name, None, "owner")  # the file name is enough; the folder path is private


def build_stock(s: Settings):
    if s.stock_provider == "none":
        return None
    if s.stock_provider == "local":
        return LocalStock(s.local_stock_dir)
    if s.stock_provider == "fixture":
        return FixtureStock(s.storage_dir / "stock_cache", s.ffmpeg_path)
    if s.stock_provider == "pixabay":
        if not (s.pixabay_api_key and s.pixabay_api_key.get_secret_value()):
            log.warning("STOCK_PROVIDER=pixabay but PIXABAY_API_KEY is empty; every scene will fall back to a title card")
            return None
        key = s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None
        check = ClipCheck(key) if s.clip_check and key else None
        return PixabayProvider(s.pixabay_api_key.get_secret_value(), s.storage_dir / "stock_cache", check=check)
    if s.stock_provider == "pexels":
        if not (s.pexels_api_key and s.pexels_api_key.get_secret_value()):
            log.warning("STOCK_PROVIDER=pexels but PEXELS_API_KEY is empty; every scene will fall back to a title card")
            return None
        return PexelsProvider(s.pexels_api_key.get_secret_value(), s.storage_dir / "stock_cache")
    raise ValueError(f"unknown STOCK_PROVIDER {s.stock_provider}")


def build_tts(s: Settings):
    if s.tts_provider == "edge_tts":
        return EdgeTTS({"vi": s.tts_voice_vi, "en": s.tts_voice_en}, s.tts_rate)
    if s.tts_provider == "fixture":
        return FixtureTTS(s.ffmpeg_path)
    if s.tts_provider == "fpt":
        if not (s.fpt_ai_api_key and s.fpt_ai_api_key.get_secret_value()):
            raise ValueError("TTS_PROVIDER=fpt needs FPT_AI_API_KEY in .env")
        return FptTTS(s.fpt_ai_api_key.get_secret_value(), s.fpt_voice, s.fpt_speed, s.ffmpeg_path, s.ffprobe_path)
    if s.tts_provider == "elevenlabs":
        if not (s.elevenlabs_api_key and s.elevenlabs_api_key.get_secret_value() and s.elevenlabs_voice_id):
            raise ValueError("TTS_PROVIDER=elevenlabs needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in .env")
        return ElevenLabsTTS(s.elevenlabs_api_key.get_secret_value(), s.elevenlabs_voice_id, s.elevenlabs_model)
    raise ValueError(f"unknown TTS_PROVIDER {s.tts_provider}")
