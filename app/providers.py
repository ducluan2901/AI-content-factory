"""Provider ports and adapters. Business code depends on these classes, never on a vendor SDK."""
import asyncio
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings


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
PRICES = {"claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}


class AnthropicApiProvider:
    """Default generator: the Anthropic API, billed per call. Key from ANTHROPIC_API_KEY."""

    name = "anthropic_api"

    def __init__(self, model: str, api_key: str | None = None, client=None):
        import anthropic

        self._anthropic = anthropic
        self.model = model
        self.client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=3)

    async def complete(self, prompt: str, timeout_s: int = 300) -> LLMResponse:
        a = self._anthropic
        try:
            resp = await self.client.with_options(timeout=timeout_s).beta.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
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

    def __init__(self, voices: dict[str, str]):
        self.voices = voices

    async def synthesize(self, text: str, language: str, out: Path) -> TTSResult:
        import edge_tts

        last: Exception | None = None
        # the endpoint intermittently returns NoAudioReceived for valid text; retry per call, not per stage
        for attempt in range(5):
            sentences = []
            try:
                comm = edge_tts.Communicate(text, self.voices[language], boundary="SentenceBoundary")
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


class FixtureTTS:
    """Silent audio sized like real speech (~3 syllables/s, the vi delivery rate), so tests run offline."""

    name = "fixture"

    def __init__(self, ffmpeg: str):
        self.ffmpeg = ffmpeg

    async def synthesize(self, text: str, language: str, out: Path) -> TTSResult:
        dur = max(1.0, len(text.split()) / 3.0)
        cmd = [self.ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{dur:.2f}", "-c:a", "libmp3lame", str(out)]
        await asyncio.to_thread(subprocess.run, cmd, check=True)
        return TTSResult(out, [{"text": text, "start_ms": 0, "end_ms": int(dur * 1000)}])


def build_llm(s: Settings):
    if s.llm_provider == "anthropic_api":
        if not s.anthropic_api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic_api needs ANTHROPIC_API_KEY in .env or the environment")
        return AnthropicApiProvider(s.script_model, s.anthropic_api_key.get_secret_value())
    if s.llm_provider == "claude_cli":
        return ClaudeCliProvider(s)
    if s.llm_provider == "fixture":
        return FixtureLLM(s.fixture_dir / "llm" / "script_vi.json")
    raise ValueError(f"unknown LLM_PROVIDER {s.llm_provider}")


def build_tts(s: Settings):
    if s.tts_provider == "edge_tts":
        return EdgeTTS({"vi": s.tts_voice_vi, "en": s.tts_voice_en})
    if s.tts_provider == "fixture":
        return FixtureTTS(s.ffmpeg_path)
    raise ValueError(f"unknown TTS_PROVIDER {s.tts_provider}")
