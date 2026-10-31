"""Stages, the runner (timeout / retry / stage_run persistence), the orchestrator, and the approval gate.

Walking skeleton (BACKLOG A.1): SCRIPT -> VOICE -> VISUALS -> SUBTITLES -> RENDER -> QUALITY
-> AWAITING_APPROVAL -> (human) -> EXPORTED. Research / strategy / SEO / stock arrive in weeks 2-3.
"""
import asyncio
import hashlib
import re
import json
import shutil
import time
import zipfile
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import media
from app.config import ROOT, Settings
from app.db import Approval, Channel, ContentJob, StageRun, latest_ok_run, now
from app.planner import FORMATS
from app.providers import ProviderError, extract_json

LANG_NAMES = {"vi": "Vietnamese", "en": "English"}
SYLLABLES_PER_S = {"vi": 3.3, "en": 2.6}  # vi measured: 178 syllables in 53.9s on HoaiMyNeural; en unmeasured; PRD D4


class SchemaError(Exception):
    """LLM output failed validation after the repair attempt."""


class Scene(BaseModel):
    narration: str = Field(min_length=5)
    on_screen_text: str = Field(min_length=1, max_length=120)
    visual_query: str | None = Field(default=None, max_length=120)  # English stock-photo search terms; falls back to on_screen_text


class ScriptOut(BaseModel):
    title: str = Field(min_length=3, max_length=100)
    caption: str = Field(min_length=3, max_length=2200)
    hashtags: list[str] = Field(min_length=1, max_length=15)
    scenes: list[Scene] = Field(min_length=2, max_length=12)

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return ["#" + t.lstrip("#").replace(" ", "") for t in v]


# ---------------------------------------------------------------- stages
# each stage: async (ctx) -> output dict. ctx = {settings, llm, tts, job, dir, attempt, up: {stage: output}}


def rel(s: Settings, p: Path) -> str:
    return p.resolve().relative_to(s.storage_dir.resolve()).as_posix()


def load_prompt(name: str) -> tuple[str, str]:
    """Template text and its version tag (name@sha8), so every stage_run records exactly which prompt ran."""
    text = (ROOT / "app" / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    return text, f"{name}@{hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}"


def duration_window(job: ContentJob) -> tuple[int, int]:
    """Accepted length: the job's own target +-20%. Used by the script checks and the quality gate."""
    return round(job.target_duration_s * 0.8), round(job.target_duration_s * 1.2)


def scene_range(job: ContentJob) -> tuple[int, int]:
    """About one scene per 5-8 s of narration, never fewer than 3. Used by the prompt and the script checks."""
    return max(3, job.target_duration_s // 8), max(4, job.target_duration_s // 5)


def script_checks(script: ScriptOut, job: ContentJob, est_s: float) -> list[dict]:
    """Advisory checks shown at script review, before any voice or render time is spent."""
    lo, hi = duration_window(job)
    n, hook = len(script.scenes), len(script.scenes[0].narration.split())
    min_n, max_n = scene_range(job)
    longest_card = max(len(sc.on_screen_text.split()) for sc in script.scenes)
    digits = [sc.narration for sc in script.scenes if re.search(r"\d", sc.narration)]

    def c(rule, passed, expected, actual, message):
        return {"rule": rule, "passed": bool(passed), "expected": str(expected), "actual": str(actual), "message": message}

    return [
        c("estimated_duration", lo <= est_s <= hi, f"{lo}-{hi}s", f"{est_s}s", "Spoken length estimate is inside the target window."),
        c("scene_count", min_n <= n <= max_n, f"{min_n}-{max_n}", n, "Scene count fits the length."),
        c("hook_length", hook <= 20, "<=20 syllables", hook, "The hook fits in the first 2-3 seconds."),
        c("on_screen_text", longest_card <= 8, "<=8 words", longest_card, "Card headlines stay readable."),
        c("platform_limits", len(script.title) <= 90 and len(script.caption) <= 300 and 3 <= len(script.hashtags) <= 8,
          "title<=90, caption<=300, 3-8 tags", f"{len(script.title)}, {len(script.caption)}, {len(script.hashtags)}", "Title, caption and hashtags fit every platform."),
        c("product_facts", job.format != "gadget" or bool(job.input_product_desc), "product facts given", "given" if job.input_product_desc else "missing",
          "Gadget scripts must come from given product facts, never invented ones."),
        c("numbers_to_verify", not digits, "no figures", f"{len(digits)} scene(s) with figures", "Every number in the narration must be verified before approval."),
    ]


def finish_script(script: ScriptOut, job: ContentJob, job_dir: Path, version: int) -> dict:
    """Shared tail of the LLM stage and a human edit: estimate, checks, and the script-v{n}.json artifact."""
    rate = SYLLABLES_PER_S[job.language]
    syll = sum(len(sc.narration.split()) for sc in script.scenes)
    est = round(syll / rate, 1)
    out = script.model_dump() | {"version": version, "syllable_count": syll, "estimated_duration_s": est, "checks": script_checks(script, job, est)}
    (job_dir / "script").mkdir(parents=True, exist_ok=True)
    (job_dir / "script" / f"script-v{version}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out



async def stage_script(ctx: dict) -> dict:
    s, job = ctx["settings"], ctx["job"]
    rate = SYLLABLES_PER_S[job.language]
    template, v_main = load_prompt("script")
    guide, v_fmt = load_prompt(f"format_{job.format if job.format in FORMATS else 'facts'}")
    min_scenes, max_scenes = scene_range(job)
    prompt = template.format(
        topic=job.input_topic,
        product_block=f"Product to feature: {job.input_product_desc}\n" if job.input_product_desc else "",
        format_guide=guide.strip(),
        language_name=LANG_NAMES[job.language],
        duration=job.target_duration_s,
        syllables=int(job.target_duration_s * rate),
        rate=rate,
        min_scenes=min_scenes,
        max_scenes=max_scenes,
    )
    if job.operator_notes:  # regeneration: the operator's feedback and the draft it refers to
        prompt += f"\n\nThe operator reviewed a previous draft and asked for these changes:\n{job.operator_notes}"
        if ctx.get("previous"):
            prev = {k: ctx["previous"][k] for k in ("title", "caption", "hashtags", "scenes") if k in ctx["previous"]}
            prompt += "\n\nPrevious draft (JSON):\n" + json.dumps(prev, ensure_ascii=False)[:4000]
        prompt += "\n\nWrite a new draft that applies the feedback and keeps what the operator did not mention."
    log = {"prompt": prompt, "responses": []}
    log_path = ctx["dir"] / "_llm" / f"SCRIPT-{ctx['attempt']}.json"
    meta = {"provider": ctx["llm"].name, "cost_usd": 0.0, "log_path": rel(s, log_path), "prompt_version": f"{v_main} {v_fmt}"}
    try:
        for _ in range(2):  # one error-fed repair attempt (ARCHITECTURE §4)
            resp = await ctx["llm"].complete(prompt, timeout_s=s.stage_timeout_s)
            log["responses"].append(resp.text)
            meta.update(model=resp.model, tokens_in=resp.tokens_in, tokens_out=resp.tokens_out)
            meta["cost_usd"] += resp.cost_usd
            try:
                script = ScriptOut.model_validate(extract_json(resp.text))
                break
            except (ValueError, ValidationError) as e:
                err = str(e)[:1500]
                prompt = f"{prompt}\n\nYour previous answer was invalid:\n{err}\n\nPrevious answer:\n{resp.text[:4000]}\n\nReturn the corrected JSON object only."
        else:
            raise SchemaError(f"script JSON invalid after repair: {err[:300]}")
    finally:
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return finish_script(script, job, ctx["dir"], ctx["attempt"]) | {"_meta": meta}


async def stage_voice(ctx: dict) -> dict:
    s, job, scenes = ctx["settings"], ctx["job"], ctx["up"]["SCRIPT"]["scenes"]
    d = ctx["dir"] / "voice"
    parts, timeline, sentences, t = [], [], [], 0
    for i, sc in enumerate(scenes):
        res = await ctx["tts"].synthesize(sc["narration"], job.language, d / f"s{i}.mp3", voice=ctx.get("voice"))
        dur_ms = int(media.duration_s(s.ffprobe_path, res.audio_path) * 1000)
        parts.append(res.audio_path)
        timeline.append({"scene_index": i, "start_ms": t, "end_ms": t + dur_ms})
        sentences += [{"scene_index": i, "text": x["text"], "start_ms": t + x["start_ms"], "end_ms": min(t + x["end_ms"], t + dur_ms)} for x in res.sentences]
        t += dur_ms
    raw_audio = d / "vo_raw.wav"
    await asyncio.to_thread(media.concat_audio, s.ffmpeg_path, parts, raw_audio)
    audio = d / "vo.wav"
    loudness_lufs = await asyncio.to_thread(media.normalize_loudness, s.ffmpeg_path, raw_audio, audio)
    voice_id = ctx.get("voice") or getattr(ctx["tts"], "voices", {}).get(job.language, "fixture")
    return {"audio_path": rel(s, audio), "duration_s": round(t / 1000, 3), "scenes": timeline, "segments": sentences,
            "loudness_lufs": loudness_lufs,
            "_meta": {"provider": ctx["tts"].name, "model": voice_id}}


async def stage_visuals(ctx: dict) -> dict:
    """One asset per scene: a Pexels stock photo with the caption composited on, or a title card when no
    stock provider is configured, the search found nothing, or the call failed (BACKLOG R4 - a card is a
    licence-clean fallback, never ship an asset with an unrecorded licence)."""
    s, scenes, stock = ctx["settings"], ctx["up"]["SCRIPT"]["scenes"], ctx.get("stock")
    d = ctx["dir"] / "visuals" / "cards"
    assets, stock_hits = [], 0
    for i, sc in enumerate(scenes):
        p = d / f"{i:02}.png"
        photo = await stock.search_photo(sc.get("visual_query") or sc["on_screen_text"]) if stock else None
        if photo:
            await asyncio.to_thread(media.render_photo_card, photo.path, sc["on_screen_text"], i, len(scenes), s.font_path, p)
            assets.append({"kind": "stock_image", "scene_index": i, "order_index": i, "path": rel(s, p), "width": media.W, "height": media.H,
                           "source_provider": stock.name, "source_url": photo.source_url, "source_id": photo.source_id,
                           "author": photo.author, "licence": stock.licence, "licence_url": stock.licence_url,
                           "attribution_required": False, "attribution_text": f"Photo by {photo.author} on Pexels", "font": Path(s.font_path).name})
            stock_hits += 1
        else:
            await asyncio.to_thread(media.render_card, sc["on_screen_text"], i, len(scenes), s.font_path, p)
            # licence ledger (PRD D3): generated cards are ours; the font is the only third-party input
            assets.append({"kind": "title_card", "scene_index": i, "order_index": i, "path": rel(s, p), "width": media.W, "height": media.H,
                           "source_provider": "title_card", "licence": "generated", "attribution_required": False,
                           "attribution_text": None, "font": Path(s.font_path).name})
    return {"assets": assets, "stock_hits": stock_hits, "_meta": {"provider": stock.name if stock else "title_card", "model": "pillow"}}


async def stage_subtitles(ctx: dict) -> dict:
    s, job = ctx["settings"], ctx["job"]
    cues = media.build_cues(ctx["up"]["VOICE"]["segments"])
    d = ctx["dir"] / "subtitles"
    srt, vtt = d / f"{job.language}.srt", d / f"{job.language}.vtt"
    media.write_subtitles(cues, srt, vtt)
    return {"srt_path": rel(s, srt), "vtt_path": rel(s, vtt), "cue_count": len(cues),
            "max_chars_per_line": max((len(l) for c in cues for l in c["lines"]), default=0),
            "max_cue_duration_s": round(max((c["end_ms"] - c["start_ms"] for c in cues), default=0) / 1000, 2),
            "_meta": {"provider": "subtitle_builder"}}


async def stage_render(ctx: dict) -> dict:
    s, up = ctx["settings"], ctx["up"]
    cards = [s.storage_dir / a["path"] for a in up["VISUALS"]["assets"]]
    durs = [(x["end_ms"] - x["start_ms"]) / 1000 for x in up["VOICE"]["scenes"]]
    d = ctx["dir"] / "render"
    out, thumb = d / "final.mp4", d / "thumb.jpg"
    cmd = await asyncio.to_thread(media.render_video, s.ffmpeg_path, cards, durs, s.storage_dir / up["VOICE"]["audio_path"], out)
    from PIL import Image
    Image.open(cards[0]).convert("RGB").save(thumb, quality=88)
    info = media.probe(s.ffprobe_path, out)
    v = next((x for x in info["streams"] if x["codec_type"] == "video"), {})
    a = next((x for x in info["streams"] if x["codec_type"] == "audio"), {})
    return {"video_path": rel(s, out), "thumbnail_path": rel(s, thumb), "container": "mp4",
            "video_codec": v.get("codec_name"), "audio_codec": a.get("codec_name"), "width": v.get("width"), "height": v.get("height"),
            "fps": v.get("r_frame_rate"), "duration_s": round(float(info["format"]["duration"]), 3), "bytes": int(info["format"]["size"]),
            "subtitles_burned": False, "ffmpeg_command": " ".join(cmd), "_meta": {"provider": "ffmpeg"}}


async def stage_quality(ctx: dict) -> dict:
    up = ctx["up"]
    r, sc, sub, vo = up["RENDER"], up["SCRIPT"], up["SUBTITLES"], up["VOICE"]
    # the format only sets the default target; the gate judges against the job's own target
    lo, hi = duration_window(ctx["job"])

    def check(rule, severity, passed, expected, actual, message):
        return {"rule": rule, "severity": severity, "passed": bool(passed), "expected": str(expected), "actual": str(actual), "message": message}

    checks = [
        check("duration_window", "error", lo <= r["duration_s"] <= hi, f"{lo:.0f}-{hi:.0f}s", f"{r['duration_s']:.1f}s",
              f"Video length is inside the {lo}-{hi}s window." if lo <= r["duration_s"] <= hi else f"Video length is outside {lo}-{hi}s: lengthen or shorten the script."),
        check("aspect_ratio", "error", (r["width"], r["height"]) == (1080, 1920), "1080x1920", f"{r['width']}x{r['height']}", "Vertical 9:16 resolution."),
        check("codecs", "error", (r["video_codec"], r["audio_codec"]) == ("h264", "aac"), "h264/aac", f"{r['video_codec']}/{r['audio_codec']}", "Platform-safe codecs."),
        check("audio_present", "error", r["audio_codec"] is not None and vo["duration_s"] > 1, "voice-over", f"{vo['duration_s']}s", "Voice-over is present."),
        check("av_sync", "error", abs(r["duration_s"] - vo["duration_s"]) <= 0.3, "<=0.3s drift", f"{abs(r['duration_s'] - vo['duration_s']):.2f}s", "Video and voice-over lengths match."),
        check("asset_licence", "error", all(a["licence"] != "unknown" for a in up["VISUALS"]["assets"]), "no unknown licence",
              ",".join(sorted({a["licence"] for a in up["VISUALS"]["assets"]})), "Every visual asset has a recorded licence."),
        check("loudness", "warning", vo.get("loudness_lufs") is not None and abs(vo["loudness_lufs"] - media.LOUDNORM_TARGET_LUFS) <= 1.5,
              f"{media.LOUDNORM_TARGET_LUFS}±1.5 LUFS", f"{vo.get('loudness_lufs')} LUFS",
              "Voice-over loudness is close to the target." if vo.get("loudness_lufs") is not None and abs(vo["loudness_lufs"] - media.LOUDNORM_TARGET_LUFS) <= 1.5
              else "Voice-over loudness drifted from the target; listen before approving."),
        check("subtitle_readability", "warning", sub["max_chars_per_line"] <= 32 and sub["max_cue_duration_s"] <= 7,
              "<=32 chars/line, <=7s/cue", f"{sub['max_chars_per_line']} chars, {sub['max_cue_duration_s']}s", "Subtitle lines are short enough to read."),
        check("platform_limits", "warning", len(sc["title"]) <= 90 and len(sc["caption"]) <= 300 and 3 <= len(sc["hashtags"]) <= 8,
              "title<=90, caption<=300, 3-8 tags", f"{len(sc['title'])}, {len(sc['caption'])}, {len(sc['hashtags'])}", "Title, caption and hashtags fit every platform."),
        check("claim_grounding", "warning", False, "sourced claims", "no research stage yet",
              "Claims are not source-checked in this version; verify facts before approving."),
    ]
    blocking = [c for c in checks if c["severity"] == "error" and not c["passed"]]
    return {"checks": checks, "passed": not blocking, "_meta": {"provider": "quality_gate"}}


# (name, fn, max_attempts)
STAGES = [
    ("SCRIPT", stage_script, 2),
    ("VOICE", stage_voice, 3),
    ("VISUALS", stage_visuals, 1),
    ("SUBTITLES", stage_subtitles, 1),
    ("RENDER", stage_render, 1),
    ("QUALITY", stage_quality, 1),
]
STAGE_NAMES = [n for n, _, _ in STAGES]
RETRYABLE = (ProviderError, SchemaError, asyncio.TimeoutError)


# ---------------------------------------------------------------- runner + orchestrator


class Pipeline:
    def __init__(self, settings: Settings, sm: sessionmaker[Session], llm, tts, stock=None):
        self.s, self.sm, self.llm, self.tts, self.stock = settings, sm, llm, tts, stock

    def job_dir(self, job_id: str) -> Path:
        d = self.s.storage_dir / "jobs" / job_id
        for sub in ("script", "voice", "visuals/cards", "subtitles", "render", "export", "_llm"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d

    async def _run_stage(self, job: ContentJob, name: str, fn, max_attempts: int, up: dict, voice: str | None = None) -> dict:
        last_err: Exception | None = None
        for _ in range(max_attempts):
            with self.sm() as db:
                attempt = len(db.scalars(select(StageRun.id).where(StageRun.job_id == job.id, StageRun.stage == name)).all()) + 1
                run = StageRun(job_id=job.id, stage=name, attempt=attempt, status="RUNNING",
                               input_json=json.dumps({"upstream": list(up)}, ensure_ascii=False))
                db.add(run)
                db.get(ContentJob, job.id).current_stage = name
                db.commit()
            t0 = time.perf_counter()
            prev = None
            if name == "SCRIPT":  # a regenerated script builds on the newest earlier draft, superseded or not
                with self.sm() as db:
                    prev = db.scalars(select(StageRun).where(StageRun.job_id == job.id, StageRun.stage == name, StageRun.status == "OK").order_by(StageRun.attempt.desc())).first()
            ctx = {"settings": self.s, "llm": self.llm, "tts": self.tts, "stock": self.stock, "job": job, "dir": self.job_dir(job.id), "attempt": attempt, "up": up,
                   "previous": json.loads(prev.output_json) if prev else None, "voice": voice}
            try:
                out = await asyncio.wait_for(fn(ctx), timeout=self.s.stage_timeout_s)
                status, err = "OK", None
            except RETRYABLE as e:
                status, err = ("TIMEOUT" if isinstance(e, asyncio.TimeoutError) else "FAILED"), e
            except Exception as e:  # non-retryable: wrapped and recorded, never swallowed
                status, err = "FAILED", e
            with self.sm() as db:
                run = db.get(StageRun, run.id)
                run.status, run.finished_at = status, now()
                run.duration_ms = int((time.perf_counter() - t0) * 1000)
                if err is None:
                    meta = out.pop("_meta", {})
                    run.output_json = json.dumps(out, ensure_ascii=False)
                    run.provider, run.model = meta.get("provider"), meta.get("model")
                    run.prompt_version = meta.get("prompt_version")
                    run.tokens_in, run.tokens_out, run.log_path = meta.get("tokens_in"), meta.get("tokens_out"), meta.get("log_path")
                    run.cost_usd = meta.get("cost_usd", 0.0)
                    db.get(ContentJob, job.id).total_cost_usd += run.cost_usd
                else:
                    run.error_type, run.error_message = type(err).__name__, str(err)[:2000] or repr(err)
                db.commit()
            if err is None:
                return out
            last_err = err
            if not isinstance(err, RETRYABLE):
                break
            await asyncio.sleep(2 * attempt)
        raise last_err

    async def run_job(self, job_id: str) -> str:
        with self.sm() as db:
            job = db.get(ContentJob, job_id)
            job.status, job.started_at, job.error_summary = "RUNNING", job.started_at or now(), None
            db.commit()
            channel = db.get(Channel, job.channel_id) if job.channel_id else None
            voice = channel.voice_id if channel else None  # per-channel voice overrides the per-language default
        up: dict = {}
        final, summary, name = None, None, None
        try:
            for name, fn, attempts in STAGES:
                with self.sm() as db:
                    done = latest_ok_run(db, job_id, name)  # resume: skip stages that already succeeded
                if done:
                    up[name] = json.loads(done.output_json)
                else:
                    up[name] = await self._run_stage(job, name, fn, attempts, up, voice)
                if name == "SCRIPT" and job.review_script and not job.script_approved_at:
                    final = "SCRIPT_REVIEW"  # plan §7.5: a human approves the script before voice and render
                    break
            if final is None:
                final = "AWAITING_APPROVAL" if up["QUALITY"]["passed"] else "NEEDS_REVISION"
                summary = None if up["QUALITY"]["passed"] else "; ".join(c["message"] for c in up["QUALITY"]["checks"] if c["severity"] == "error" and not c["passed"])
        except Exception as e:
            final, summary = "FAILED", f"{name}: {type(e).__name__}: {e}"[:1000]
        with self.sm() as db:
            job = db.get(ContentJob, job_id)
            job.status, job.error_summary, job.current_stage, job.finished_at = final, summary, None, now()
            db.commit()
        return final



# ---------------------------------------------------------------- job service: the approval wall (ARCHITECTURE §8)


class GateError(Exception):
    pass


RERUNNABLE = {"FAILED", "NEEDS_REVISION", "REJECTED"}


def supersede_from(db: Session, job_id: str, from_stage: str, token: str) -> None:
    """Mark the live runs of from_stage and everything after it as superseded; the rows stay for the audit trail."""
    for name in STAGE_NAMES[STAGE_NAMES.index(from_stage):]:
        for run in db.scalars(select(StageRun).where(StageRun.job_id == job_id, StageRun.stage == name, StageRun.superseded_by.is_(None))):
            run.superseded_by = token


def requeue(db: Session, job_id: str, from_stage: str | None) -> ContentJob:
    job = db.get(ContentJob, job_id)
    if job.status not in RERUNNABLE:
        raise GateError(f"job is {job.status}; only {sorted(RERUNNABLE)} can be re-run")
    if from_stage:
        if from_stage not in STAGE_NAMES:
            raise GateError(f"unknown stage {from_stage}")
        # shortcut: marker instead of the new run's id; link runs when per-stage re-run UI lands (W3 3.5)
        supersede_from(db, job_id, from_stage, f"rerun@{now()}")
        if from_stage == "SCRIPT":  # a new script must pass the script gate again
            job.script_approved_at = None
    elif job.status != "FAILED":
        raise GateError("from_stage is required unless the job FAILED (a plain resume would reproduce the same result)")
    job.status, job.finished_at = "QUEUED", None
    db.commit()
    return job


# ---------------------------------------------------------------- script gate (plan §7.5 SCRIPT_GENERATED -> SCRIPT_APPROVED)

SCRIPT_EDITABLE = {"SCRIPT_REVIEW", "NEEDS_REVISION", "REJECTED", "FAILED"}


def save_script(db: Session, s: Settings, job_id: str, data: dict, edited_by: str) -> dict:
    """A human-edited script becomes a new SCRIPT run (provider 'human'); later stages are superseded and must re-run."""
    job = db.get(ContentJob, job_id)
    if job.status not in SCRIPT_EDITABLE:
        raise GateError(f"job is {job.status}; the script can be edited in {sorted(SCRIPT_EDITABLE)}")
    if not edited_by.strip():
        raise GateError("edited_by is required")
    script = ScriptOut.model_validate(data)
    supersede_from(db, job_id, "SCRIPT", f"edit@{now()}")
    attempt = len(db.scalars(select(StageRun.id).where(StageRun.job_id == job_id, StageRun.stage == "SCRIPT")).all()) + 1
    out = finish_script(script, job, s.storage_dir / "jobs" / job_id, attempt)
    db.add(StageRun(job_id=job_id, stage="SCRIPT", attempt=attempt, status="OK", provider="human", model=edited_by.strip(),
                    input_json=json.dumps({"edited_by": edited_by.strip()}), output_json=json.dumps(out, ensure_ascii=False), finished_at=now()))
    job.status, job.script_approved_at, job.current_stage, job.error_summary = "SCRIPT_REVIEW", None, None, None
    db.commit()
    return out


def approve_script(db: Session, job_id: str, approved_by: str, notes: str | None) -> ContentJob:
    job = db.get(ContentJob, job_id)
    if job.status != "SCRIPT_REVIEW":
        raise GateError(f"job is {job.status}, not SCRIPT_REVIEW")
    if not approved_by.strip():
        raise GateError("approved_by is required")
    db.add(Approval(job_id=job_id, decision="script_approved", approved_by=approved_by.strip(), notes=notes))
    # the approved draft settles the feedback; keeping it would push later drafts further the same way
    job.script_approved_at, job.status, job.finished_at, job.operator_notes = now(), "QUEUED", None, None
    db.commit()
    return job


def regenerate_script(db: Session, job_id: str, notes: str, requested_by: str) -> ContentJob:
    """Reject the draft with feedback; the next SCRIPT run sees the notes and the previous draft."""
    job = db.get(ContentJob, job_id)
    if job.status not in SCRIPT_EDITABLE:
        raise GateError(f"job is {job.status}; regenerate is allowed in {sorted(SCRIPT_EDITABLE)}")
    if not notes.strip() or not requested_by.strip():
        raise GateError("notes and requested_by are required")
    db.add(Approval(job_id=job_id, decision="script_rejected", approved_by=requested_by.strip(), notes=notes))
    supersede_from(db, job_id, "SCRIPT", f"regen@{now()}")
    job.operator_notes, job.script_approved_at, job.status, job.finished_at, job.error_summary = notes.strip(), None, "QUEUED", None, None
    db.commit()
    return job



def decide(db: Session, job_id: str, decision: str, approved_by: str, notes: str | None) -> ContentJob:
    job = db.get(ContentJob, job_id)
    if job.status != "AWAITING_APPROVAL":
        raise GateError(f"job is {job.status}, not AWAITING_APPROVAL")
    if not approved_by.strip():
        raise GateError("approved_by is required")
    apr = Approval(job_id=job_id, decision=decision, approved_by=approved_by.strip(), notes=notes)
    db.add(apr)
    db.flush()
    if decision == "approved":
        job.status, job.approval_id = "APPROVED", apr.id
    else:
        job.status = "REJECTED"
    db.commit()
    return job


def export(db: Session, s: Settings, job_id: str) -> dict:
    job = db.get(ContentJob, job_id)
    if job.status != "APPROVED" or not job.approval_id:  # layer 1; the DB CHECK is layer 2
        raise GateError("export requires a human approval")
    runs = {n: latest_ok_run(db, job_id, n) for n in STAGE_NAMES}
    out = {n: json.loads(r.output_json) for n, r in runs.items()}
    d = s.storage_dir / "jobs" / job_id / "export"
    d.mkdir(parents=True, exist_ok=True)
    lang = job.language
    files = {
        "video.mp4": out["RENDER"]["video_path"], "thumbnail.jpg": out["RENDER"]["thumbnail_path"],
        f"{lang}.srt": out["SUBTITLES"]["srt_path"], f"{lang}.vtt": out["SUBTITLES"]["vtt_path"],
    }
    for dst, src in files.items():
        shutil.copyfile(s.storage_dir / src, d / dst)
    sc = out["SCRIPT"]
    (d / "caption.txt").write_text(f"{sc['title']}\n\n{sc['caption']}\n\n{' '.join(sc['hashtags'])}\n", encoding="utf-8")
    apr = db.get(Approval, job.approval_id)
    manifest = {
        "job_id": job.id, "topic": job.input_topic, "language": lang, "platforms": json.loads(job.target_platforms),
        "ai_generated": True,  # for platform AI-content disclosure (BACKLOG R9)
        "title": sc["title"], "caption": sc["caption"], "hashtags": sc["hashtags"],
        "video": {k: out["RENDER"][k] for k in ("width", "height", "duration_s", "video_codec", "audio_codec", "fps", "bytes")},
        "assets": out["VISUALS"]["assets"],
        "quality_checks": out["QUALITY"]["checks"],
        "approval": {"id": apr.id, "approved_by": apr.approved_by, "notes": apr.notes, "at": apr.created_at},
        "stages": [{"stage": n, "run_id": r.id, "provider": r.provider, "model": r.model, "cost_usd": r.cost_usd, "duration_ms": r.duration_ms} for n, r in runs.items()],
        "total_cost_usd": job.total_cost_usd, "exported_at": now(),
    }
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    zip_path = d.parent / f"{job_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(d.iterdir()):
            z.write(f, f.name)
    job.status = "EXPORTED"
    db.commit()
    return {"bundle_dir": d.resolve().as_posix(), "zip_path": zip_path.resolve().as_posix(), "files": sorted(f.name for f in d.iterdir())}
