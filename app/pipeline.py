"""Stages, the runner (timeout / retry / stage_run persistence), the orchestrator, and the approval gate.

Walking skeleton (BACKLOG A.1): SCRIPT -> VOICE -> VISUALS -> SUBTITLES -> RENDER -> QUALITY
-> AWAITING_APPROVAL -> (human) -> EXPORTED. Research / strategy / SEO / stock arrive in weeks 2-3.
"""
import asyncio
import hashlib
import math
import re
import json
import shutil
import time
import unicodedata
import zipfile
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import media, qa_review
from app.config import ROOT, Settings
from app.db import Approval, Channel, ContentJob, StageRun, latest_ok_run, now
from app.planner import FORMATS
from app.providers import ClipCheck, ProviderError, extract_json

LANG_NAMES = {"vi": "Vietnamese", "en": "English", "es": "Spanish (Latin American)", "pt": "Brazilian Portuguese", "de": "German",
              "fr": "French", "ja": "Japanese"}
LANG_RE = "^(" + "|".join(LANG_NAMES) + ")$"  # the API's accepted languages
# Edge HoaiMy at the default rate, measured: 3.3 syll/s on a 54 s script of long sentences, 2.44 on a 33 s comedy script
# of short punchy ones (each scene is its own TTS clip, so short scenes carry more padding). With TTS_RATE=+10% and
# viral-style scripts of very short sentences: 2.11 and 2.31 (each sentence ends in a pause). Every VOICE run records
# the observed rate; revise this from those numbers. en counts words, not syllables: Edge Andrew +10% read 42 words in
# 15.1 s (2.77 words/s) on the first English facts video.
SYLLABLES_PER_S = {"vi": 2.2, "en": 2.8,
                   # the translated channels count words too, and ja counts characters (spoken_units). Measured on the first translated
                   # video (the dog's face, 4 short scenes, Edge +10%): es Jorge 2.11 and 1.89, pt Antonio 1.96, de Conrad 1.54,
                   # fr Henri 2.29 words/s; ja Keita 5.19 and 5.09 chars/s. Every VOICE run records the observed rate; revise from those
                   "es": 2.0, "pt": 1.95, "de": 1.55, "fr": 2.25, "ja": 5.1}
# per voice, vi: Edge is the mean of the last three measurements (2.11, 2.31, 2.59); FPT.TTS-pro is the default voice
# fci_vietkhoa, 4.21-4.42 on four facts videos (std_quanganh read ~3.4). ElevenLabs unmeasured: the default above.
VOICE_RATE_VI = {"edge_tts": 2.35, "fpt": 4.3}


def speech_rate(s: Settings, language: str) -> float:
    """Spoken syllables per second of the configured voice: sizes the script to the target length."""
    return VOICE_RATE_VI.get(s.tts_provider, SYLLABLES_PER_S["vi"]) if language == "vi" else SYLLABLES_PER_S[language]


# the next-episode line a translated video ends on ("Mañana: tu gato."), the one reason a Short gives to subscribe
NEXT_WORD = {"vi": "Mai:", "en": "Tomorrow:", "es": "Mañana:", "pt": "Amanhã:", "de": "Morgen:", "fr": "Demain :", "ja": "次は"}
NEXT_RESERVE = {"words": 3, "characters": 7}  # what that line takes out of the last scene's budget
MAX_TEMPO = 1.2  # a translated voice-over that runs long is sped up at most this much (still natural for Shorts narration)


def last_json(text: str) -> dict:
    """The last parseable JSON object in a reply: a model that corrects itself mid-answer prints two, and extract_json's
    outermost match then spans both and fails."""
    dec, i, out = json.JSONDecoder(), 0, None
    while (i := text.find("{", i)) != -1:
        try:
            out, i = dec.raw_decode(text, i)
        except ValueError:
            i += 1
    if not isinstance(out, dict):
        raise ValueError("no JSON object in LLM response")
    return out


def spoken_units(text: str, language: str) -> int:
    """What SYLLABLES_PER_S counts: whitespace-separated syllables (vi) or words, and characters for Japanese, where the
    translator's phrase spacing varies too much to size a scene by."""
    return len(text.replace(" ", "")) if language == "ja" else len(text.split())


JA_CAPTION_CHARS = 6  # Yu Gothic at the word-caption size (140) fits about 6 characters between the safe margins


def ja_caption_spacing(text: str) -> str:
    """Japanese has no spaces to break captions at: split after punctuation, then cut any piece still over a line."""
    pieces = re.sub(r"([、。？！?!])", r"\1 ", text).split()
    return " ".join(p[k:k + JA_CAPTION_CHARS] for p in pieces for k in range(0, len(p), JA_CAPTION_CHARS))


def font_for(s: Settings, language: str) -> str:
    return s.font_path_ja if language == "ja" else s.font_path


MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg"}
CLIP_FORMATS = {"facts"}  # stock video and word-by-word captions; the other formats keep sentence cards for now
FACTS_TAGS = ["#suthatthuvi", "#khamphathegioi", "#learnontiktok"]  # on 2026-11-02's viral facts videos (200K-17M views)
SHOT_MS = 2000  # the facts reference videos cut every 1-2 s (REFERENCE_LIBRARY.md)


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
    sources: list[str] = Field(default_factory=list, max_length=8)  # where each fact comes from, for the reviewer to open

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        # platform convention, and what hashtag search actually matches: lowercase ASCII without diacritics (#haihuoc)
        return ["#" + "".join(ch for ch in unicodedata.normalize("NFKD", t.replace("đ", "d").replace("Đ", "D")) if ch.isascii() and ch.isalnum()).lower()
                for t in v]


# ---------------------------------------------------------------- stages
# each stage: async (ctx) -> output dict. ctx = {settings, llm, tts, job, dir, attempt, up: {stage: output}}


def rel(s: Settings, p: Path) -> str:
    return p.resolve().relative_to(s.storage_dir.resolve()).as_posix()


def load_prompt(name: str, folder: Path = ROOT / "app" / "prompts") -> tuple[str, str]:
    """Template text and its version tag (name@sha8), so every stage_run records exactly which prompt ran."""
    text = (folder / f"{name}.md").read_text(encoding="utf-8")
    return text, f"{name}@{hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}"


def guide_name(fmt: str, language: str, folder: Path) -> str:
    """format_<fmt>_<lang>.md when the format has a guide written for that language, else format_<fmt>.md."""
    fmt = fmt if fmt in FORMATS else "facts"
    return f"format_{fmt}_{language}" if (folder / f"format_{fmt}_{language}.md").exists() else f"format_{fmt}"


def risky_terms(texts: list[str]) -> list[str]:
    """Terms from app/safety_terms.txt found in any of the texts (case-insensitive); a hit is a warning, never a block."""
    terms = [ln.strip().lower() for ln in (ROOT / "app" / "safety_terms.txt").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    blob = "\n".join(texts).lower()
    return sorted({t for t in terms if t in blob})


# First-person experience and before/after wording. A gadget script may only say this if the operator really did it,
# so a hit is a warning to confirm, never a block. Found in a real trial: the model narrated a planned 3-night test as done.
EXPERIENCE_MARKERS = ("mình đã thử", "mình thử", "tôi đã thử", "tôi thử", "mình đã dùng", "tôi đã dùng", "mình dùng thấy", "tôi dùng thấy",
                      "dùng thử", "mình test", "trước và sau", "i tried", "i tested", "i used", "before and after")


def experience_claims(texts: list[str]) -> list[str]:
    blob = "\n".join(texts).lower()
    return sorted(m for m in EXPERIENCE_MARKERS if m in blob)


def script_texts(sc: dict) -> list[str]:
    return [sc["title"], sc["caption"]] + [x for s in sc["scenes"] for x in (s["narration"], s["on_screen_text"])]


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
        c("safety_terms", not (hits := risky_terms([script.title, script.caption] + [x for s in script.scenes for x in (s.narration, s.on_screen_text)])),
          "no risky claim terms", ", ".join(hits) or "none", "No wording from the risky-claims list (app/safety_terms.txt)."),
        c("experience_claims", job.format != "gadget" or not (exp := experience_claims([x for s in script.scenes for x in (s.narration, s.on_screen_text)] + [script.caption])),
          "no experience or before/after claims", ", ".join(exp) if job.format == "gadget" and exp else "none",
          "A gadget script must not narrate a test or result the operator has not actually done and told us about."),
        c("sources", job.format != "facts" or bool(script.sources), "a source for the facts", f"{len(script.sources)} source(s)",
          "A facts script names where its facts come from, so the reviewer can check them."),
        c("numbers_to_verify",not digits, "no figures", f"{len(digits)} scene(s) with figures", "Every number in the narration must be verified before approval."),
    ]


def finish_script(script: ScriptOut, job: ContentJob, job_dir: Path, version: int, rate: float) -> dict:
    """Shared tail of the LLM stage and a human edit: estimate, checks, and the script-v{n}.json artifact."""
    syll = sum(spoken_units(sc.narration, job.language) for sc in script.scenes)
    est = round(syll / rate, 1)
    out = script.model_dump() | {"version": version, "syllable_count": syll, "estimated_duration_s": est, "checks": script_checks(script, job, est)}
    (job_dir / "script").mkdir(parents=True, exist_ok=True)
    (job_dir / "script" / f"script-v{version}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out



def source_outputs(sm: sessionmaker[Session], source_job_id: str) -> dict:
    """The approved source job's SCRIPT and VISUALS outputs, the two things a translation reuses."""
    with sm() as db:
        src = db.get(ContentJob, source_job_id)
        runs = {n: latest_ok_run(db, source_job_id, n) for n in ("SCRIPT", "VISUALS")}
        if not src or not all(runs.values()):
            raise GateError(f"source job {source_job_id} has no finished script and visuals to translate")
        return {"job": src, "SCRIPT": json.loads(runs["SCRIPT"].output_json), "VISUALS": json.loads(runs["VISUALS"].output_json)}


async def stage_translate(ctx: dict) -> dict:
    """SCRIPT for a translated job: the source script localised scene for scene (the clips are reused), then one cheap
    back-check that lists scenes whose meaning drifted, shown as a script check for the reviewer."""
    s, job = ctx["settings"], ctx["job"]
    src = source_outputs(ctx["sm"], job.source_job_id)
    source = {k: src["SCRIPT"][k] for k in ("title", "caption", "hashtags", "scenes", "sources")}
    n = len(source["scenes"])
    template, version = load_prompt("translate", s.prompt_dir)
    # each scene's clips last as long as the source voice did there; the new voice must fit that at its own speed
    spans = {i: (min(a["start_ms"] for a in src["VISUALS"]["assets"] if a["scene_index"] == i), max(a["end_ms"] for a in src["VISUALS"]["assets"] if a["scene_index"] == i))
             for i in range(n) if any(a["scene_index"] == i for a in src["VISUALS"]["assets"])}
    rate = speech_rate(s, job.language)
    unit = "characters" if job.language == "ja" else "words"
    # the "Tomorrow: X." line is added by code (models drop it when the budget is tight), so its length comes off the last scene
    reserve = {i: NEXT_RESERVE[unit] if i == n - 1 else 0 for i in range(n)}
    budget = {i: max(3, round((spans[i][1] - spans[i][0]) / 1000 * rate) - reserve[i]) for i in spans}
    budgets = "\n".join(f"- scene {i + 1}: about {budget[i]} {unit} ({(spans[i][1] - spans[i][0]) / 1000:.1f} s of clips)" for i in range(n) if i in spans)

    def overrun(sc: ScriptOut) -> int:  # units over budget, summed over the scenes that run long
        return sum(max(0, spoken_units(x.narration, job.language) - round(budget[i] * 1.1)) for i, x in enumerate(sc.scenes) if i in budget)
    prompt = template.format(language_name=LANG_NAMES[job.language], source_language_name=LANG_NAMES.get(src["job"].language, src["job"].language),
                             source_json=json.dumps(source, ensure_ascii=False), scene_count=n, scene_budgets=budgets, next_word=NEXT_WORD[job.language],
                             notes_block=f"\nNotes from the operator: {job.operator_notes}\n" if job.operator_notes else "")
    log = {"prompt": prompt, "responses": []}
    log_path = ctx["dir"] / "_llm" / f"SCRIPT-{ctx['attempt']}.json"
    meta = {"provider": ctx["llm"].name, "cost_usd": 0.0, "log_path": rel(s, log_path), "prompt_version": version}
    try:
        for _ in range(2):  # one error-fed repair attempt, as in stage_script
            resp = await ctx["llm"].complete(prompt, timeout_s=s.stage_timeout_s)
            log["responses"].append(resp.text)
            meta.update(model=resp.model, tokens_in=resp.tokens_in, tokens_out=resp.tokens_out)
            meta["cost_usd"] += resp.cost_usd
            try:
                raw = extract_json(resp.text)
                script = ScriptOut.model_validate(raw)
                if len(script.scenes) != n:
                    raise ValueError(f"{len(script.scenes)} scenes, the source has {n}: one scene per source scene, same order")
                break
            except (ValueError, ValidationError) as e:
                err = str(e)[:1500]
                prompt = f"{prompt}\n\nYour previous answer was invalid:\n{err}\n\nPrevious answer:\n{resp.text[:4000]}\n\nReturn the corrected JSON object only."
        else:
            raise SchemaError(f"translation JSON invalid after repair: {err[:300]}")
        if overrun(script):
            # Sonnet wrote German at the source's length (17 words for a 9-word scene) and ignored "Too long" when it came
            # with the whole prompt again; a short, separate task (these sentences, this many words) is what it follows
            long = {i: x.narration for i, x in enumerate(script.scenes) if i in budget and spoken_units(x.narration, job.language) > round(budget[i] * 1.1)}
            ask = "\n".join(f'{i + 1}. at most {budget[i]} {unit}: "{t}"' for i, t in long.items())
            resp = await ctx["llm"].complete(
                f"Shorten each {LANG_NAMES[job.language]} line below to at most the given number of {unit}, for a voice-over that must fit "
                f"its video clip. Keep every fact and number, the question and any '{NEXT_WORD[job.language]}' or 'number three' promise; "
                f"cut adjectives, repetition and asides. Spoken, natural {LANG_NAMES[job.language]}.\n\n{ask}\n\n"
                'Return ONLY JSON: {"lines": {"<number>": "<shortened line>"}}', timeout_s=s.stage_timeout_s)
            log["responses"].append(resp.text)
            meta["cost_usd"] += resp.cost_usd
            try:
                lines = last_json(resp.text)["lines"]
                for i in long:
                    short = str(lines.get(str(i + 1)) or "").strip()
                    if short and spoken_units(short, job.language) < spoken_units(script.scenes[i].narration, job.language):
                        script.scenes[i].narration = short
            except (ValueError, KeyError, TypeError, AttributeError):
                pass  # the first draft stands; the script checks show its length
        script.sources = source["sources"]  # the facts were checked on the source; its sources stand
        nxt = str(raw.get("next_subject") or "").strip().rstrip(".。!！ ")
        last = script.scenes[-1]
        if nxt and NEXT_WORD[job.language] not in last.narration:
            last.narration = f"{last.narration.rstrip()} {NEXT_WORD[job.language]} {nxt}{'。' if job.language == 'ja' else '.'}"
        check_prompt = (f"Source script scenes ({LANG_NAMES.get(src['job'].language, src['job'].language)}):\n"
                        + json.dumps([sc["narration"] for sc in source["scenes"]], ensure_ascii=False)
                        + f"\n\nTranslation ({LANG_NAMES[job.language]}):\n" + json.dumps([sc.narration for sc in script.scenes], ensure_ascii=False)
                        + (f"\n\nThe operator asked the translator for this, so it is not drift: {job.operator_notes}" if job.operator_notes else "")
                        + "\n\nThe translation is deliberately shorter where its language reads slower; dropped detail is not drift, a dropped fact is."
                        + "\n\nCompare scene by scene. A scene drifts when the translation changes, adds or drops a fact, a number or a claim; "
                          "wording and idiom may differ freely. Return ONLY JSON: {\"drifted\": [{\"scene\": <1-based index>, \"why\": \"...\"}]}")
        resp = await ctx["llm"].complete(check_prompt, timeout_s=s.stage_timeout_s)
        log["responses"].append(resp.text)
        meta["cost_usd"] += resp.cost_usd
        try:
            drifted = [f"scene {d['scene']}: {d['why']}" for d in last_json(resp.text).get("drifted", [])]
        except (ValueError, KeyError, TypeError, AttributeError):
            drifted = ["back-check unreadable: compare the scenes yourself"]
    finally:
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    out = finish_script(script, job, ctx["dir"], ctx["attempt"], rate)
    out["checks"].append({"rule": "translation_fidelity", "passed": not drifted, "expected": "every scene says what the source says",
                          "actual": "; ".join(drifted) or "no drift found", "message": "A second model pass compared each scene with the source."})
    ends = NEXT_WORD[job.language] in script.scenes[-1].narration
    out["checks"].append({"rule": "next_episode_line", "passed": ends or not job.operator_notes, "expected": f"ends on '{NEXT_WORD[job.language]} ...'",
                          "actual": script.scenes[-1].narration[-60:], "message": "The last line names tomorrow's episode, the viewer's reason to subscribe."})
    out["source_job_id"] = job.source_job_id
    return out | {"_meta": meta}


async def stage_script(ctx: dict) -> dict:
    s, job = ctx["settings"], ctx["job"]
    if job.source_job_id:
        return await stage_translate(ctx)
    rate = speech_rate(s, job.language)
    template, v_main = load_prompt("script", s.prompt_dir)
    guide, v_fmt = load_prompt(guide_name(job.format, job.language, s.prompt_dir), s.prompt_dir)
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
    if job.brand_name:  # a client video closes on the business's card; its tags are the client's, not the channel's
        prompt += (f"\n\nThis video is made for the business \"{job.brand_name}\" and ends on a card with its name, so never say the name "
                   "or its services in the narration. hashtags: ignore the format's fixed tags; give 4 to 6 tags that this business's "
                   "customers search for in {lang} (its field, the problem, the city if the topic names one), lowercase ASCII without "
                   "diacritics, nothing like #viral or #learnontiktok.").format(lang=LANG_NAMES[job.language])
    if job.operator_notes:  # regeneration: the operator's feedback and the draft it refers to
        prompt += f"\n\nThe operator reviewed a previous draft and asked for these changes:\n{job.operator_notes}"
        if ctx.get("previous"):
            prev = {k: ctx["previous"][k] for k in ("title", "caption", "hashtags", "scenes", "sources") if k in ctx["previous"]}
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
        # models undershoot a syllable budget (asked for 119, wrote 87): one rewrite to length, keep whichever is closer
        lo, hi = duration_window(job)
        target = job.target_duration_s * rate
        syll = sum(spoken_units(sc.narration, job.language) for sc in script.scenes)
        if not lo <= syll / rate <= hi:
            resp = await ctx["llm"].complete(
                f"{prompt}\n\nPrevious answer:\n{resp.text[:4000]}\n\nThat script has {syll} spoken syllables, about {syll / rate:.0f} s "
                f"when read aloud; it must be {lo}-{hi} s, about {target:.0f} syllables. "
                f"{'Add about ' + str(round(target - syll)) + ' syllables: more concrete detail inside the existing beats, or one more beat' if syll < target else 'Cut about ' + str(round(syll - target)) + ' syllables'}, "
                "keeping the hook, the order and the sources. Return the full corrected JSON object only.", timeout_s=s.stage_timeout_s)
            log["responses"].append(resp.text)
            meta["cost_usd"] += resp.cost_usd
            try:
                again = ScriptOut.model_validate(extract_json(resp.text))
                if abs(sum(spoken_units(sc.narration, job.language) for sc in again.scenes) - target) < abs(syll - target):
                    script = again
            except (ValueError, ValidationError):
                pass  # the first valid draft stands; the script checks show its length
    finally:
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    if job.format == "facts" and job.language == "vi" and not job.brand_name:  # the viral facts videos' tags, spelled right every time (a draft wrote #sudthuthuvi)
        own = [t for t in script.hashtags if t not in FACTS_TAGS + ["#viral"]]
        script.hashtags = (FACTS_TAGS + own)[:7] + ["#viral"]
    return finish_script(script, job, ctx["dir"], ctx["attempt"], rate) | {"_meta": meta}


async def stage_voice(ctx: dict) -> dict:
    s, job, scenes = ctx["settings"], ctx["job"], ctx["up"]["SCRIPT"]["scenes"]
    d = ctx["dir"] / "voice"
    parts, timeline, sentences, t = [], [], [], 0
    for i, sc in enumerate(scenes):
        res = await ctx["tts"].synthesize(sc["narration"], job.language, d / f"s{i}.mp3", voice=ctx.get("voice"))
        dur_ms = int(media.duration_s(s.ffprobe_path, res.audio_path) * 1000)
        parts.append(res.audio_path)
        timeline.append({"scene_index": i, "start_ms": t, "end_ms": t + dur_ms})
        sentences += [{"scene_index": i, "text": x["text"], "start_ms": t + x["start_ms"], "end_ms": min(t + x["end_ms"], t + dur_ms)}
                      | ({"words": [w | {"start_ms": t + w["start_ms"], "end_ms": t + w["end_ms"]} for w in x["words"]]} if x.get("words") else {})
                      for x in res.sentences]
        t += dur_ms
    raw_audio = d / "vo_raw.wav"
    await asyncio.to_thread(media.concat_audio, s.ffmpeg_path, parts, raw_audio)
    # a translation that still runs long (models miscount words; German and French overshot their budgets by a third) is
    # read faster instead of failing the length gate: atempo keeps the pitch, every timing shrinks by the same factor
    tempo = min(MAX_TEMPO, t / 1000 / (duration_window(job)[1] - 0.5)) if job.source_job_id else 1.0
    if tempo > 1.0:
        fast = d / "vo_fast.wav"
        await asyncio.to_thread(media.run, [s.ffmpeg_path, "-y", "-v", "error", "-i", str(raw_audio), "-filter:a", f"atempo={tempo:.4f}", str(fast)])
        raw_audio, t = fast, round(t / tempo)
        k = lambda x: {**x, "start_ms": round(x["start_ms"] / tempo), "end_ms": round(x["end_ms"] / tempo)}  # noqa: E731
        timeline = [k(x) for x in timeline]
        sentences = [k(x) | ({"words": [k(w) for w in x["words"]]} if x.get("words") else {}) for x in sentences]
    audio = d / "vo.wav"
    loudness_lufs = await asyncio.to_thread(media.normalize_loudness, s.ffmpeg_path, raw_audio, audio)
    voice_id = ctx.get("voice") or getattr(ctx["tts"], "voices", {}).get(job.language, "fixture")
    return {"audio_path": rel(s, audio), "duration_s": round(t / 1000, 3), "scenes": timeline, "segments": sentences,
            "loudness_lufs": loudness_lufs, "tempo": round(max(tempo, 1.0), 3),
            "syllables_per_s_observed": round(ctx["up"]["SCRIPT"]["syllable_count"] / (t / 1000), 2) if t else None,
            "_meta": {"provider": ctx["tts"].name, "model": voice_id}}


async def stage_visuals(ctx: dict) -> dict:
    """One card per spoken sentence (kinetic captions): the sentence in big type with the scene headline's words in
    colour, on the scene's stock photo when the search found one, or on a gradient card when no stock provider is
    configured, the search found nothing, or the call failed (BACKLOG R4 - a card is a licence-clean fallback, never
    ship an asset with an unrecorded licence). Each card starts exactly when its sentence does, from the voice
    stage's sentence boundaries, and a scene's cards together cover the scene's voice span."""
    s, scenes, stock, vo = ctx["settings"], ctx["up"]["SCRIPT"]["scenes"], ctx.get("stock"), ctx["up"]["VOICE"]
    if ctx["job"].source_job_id:
        return copy_visuals(s, source_outputs(ctx["sm"], ctx["job"].source_job_id)["VISUALS"], vo)
    d, font = ctx["dir"] / "visuals" / "cards", font_for(s, ctx["job"].language)
    # clip formats: stock video shots of at most SHOT_MS instead, the words go on screen as word-by-word captions at
    # render, so a scene without clips gets text-free cards
    clips_on = ctx["job"].format in CLIP_FORMATS and hasattr(stock, "search_videos")
    assets, stock_hits, pool = [], 0, []
    check = getattr(stock, "check", None)
    spent0 = check.cost_usd if check else 0.0  # shortcut: two jobs in VISUALS at once would share this delta; jobs run one at a time today
    overrides = json.loads(ctx["job"].visual_queries_json or "{}")  # the operator's search terms for scenes whose clips missed
    for i, sc in enumerate(scenes):
        span = vo["scenes"][i]
        query = overrides.get(str(i)) or sc.get("visual_query") or sc["on_screen_text"]
        clips = await stock.search_videos(query) if clips_on else []
        photo = None if clips else await stock.search_photo(query) if stock else None
        stock_hits += bool(clips or photo)
        earlier = list(pool)
        if clips_on and not clips and not photo and earlier:  # the search ran dry (every octopus clip already used): an earlier clip, later in, beats a blank card
            clips = earlier[i % len(earlier):] + earlier[:i % len(earlier)]  # every clip so far, so the late scenes do not all loop one scene's
        elif len(clips) == 1 and earlier:  # one fresh clip would loop the whole scene (a cat walking 4 times): mix in earlier ones
            clips = clips + earlier[-2:]
        pool += [c for c in clips if c not in earlier]
        if clips:
            n = max(1, math.ceil((span["end_ms"] - span["start_ms"]) / SHOT_MS))
            step = (span["end_ms"] - span["start_ms"]) / n
            for k in range(n):
                clip = clips[k % len(clips)]
                # a clip used again plays a later part of itself
                start = (clip.duration_s or 0) / 2 if clip in earlier else 0.5
                offset = min(start + k // len(clips) * step / 1000, max(0.0, (clip.duration_s or 0) - step / 1000))
                assets.append({"kind": "stock_video", "source_provider": stock.name, "source_url": clip.source_url, "source_id": clip.source_id,
                               "author": clip.author, "licence": stock.licence, "licence_url": stock.licence_url,
                               "attribution_required": False, "attribution_text": stock.attribution(clip),
                               "scene_index": i, "order_index": len(assets), "start_ms": span["start_ms"] + round(k * step),
                               "end_ms": span["start_ms"] + round((k + 1) * step), "path": rel(s, clip.path), "clip_offset_s": round(offset, 2),
                               "width": media.W, "height": media.H})
            continue
        segs = [x for x in vo["segments"] if x["scene_index"] == i] or [{"text": sc["narration"]}]
        bounds = [span["start_ms"]] + [x["start_ms"] for x in segs[1:]] + [span["end_ms"]]
        for k in range(1, len(bounds)):  # clamp inside the scene and keep the order; a scene's cards sum to its span
            bounds[k] = min(max(bounds[k], bounds[k - 1]), span["end_ms"])
        words = media.plain_words(sc["on_screen_text"])
        for k, seg in enumerate(segs):
            text = "" if clips_on else seg["text"]
            p = d / f"{len(assets):02}.png"
            timing = {"scene_index": i, "order_index": len(assets), "start_ms": bounds[k], "end_ms": bounds[k + 1], "path": rel(s, p),
                      "width": media.W, "height": media.H, "font": Path(font).name}
            if photo:
                await asyncio.to_thread(media.render_photo_card, photo.path, text, i, font, p, words)
                assets.append({"kind": "stock_image", "source_provider": stock.name, "source_url": photo.source_url, "source_id": photo.source_id,
                               "author": photo.author, "licence": stock.licence, "licence_url": stock.licence_url,
                               "attribution_required": False, "attribution_text": stock.attribution(photo)} | timing)
            else:
                await asyncio.to_thread(media.render_card, text, i, font, p, words)
                # licence ledger (PRD D3): generated cards are ours; the font is the only third-party input
                assets.append({"kind": "title_card", "source_provider": "title_card", "licence": "generated", "attribution_required": False,
                               "attribution_text": None} | timing)
    return {"assets": assets, "stock_hits": stock_hits, "captions": "words" if clips_on else "cards",
            "_meta": {"provider": stock.name if stock else "title_card", "model": check.model if check else "pillow",
                      "cost_usd": round(check.cost_usd - spent0, 6) if check else 0.0}}


def copy_visuals(s: Settings, src: dict, vo: dict) -> dict:
    """VISUALS for a translated job: the source scene's clips (already checked and licensed), re-cut over the new
    voice's span of that scene in SHOT_MS shots, the way stage_visuals cuts fresh clips. Nothing is searched or paid for."""
    by_scene: dict[int, list[dict]] = {}
    for a in src["assets"]:
        if a["path"] not in {x["path"] for x in by_scene.setdefault(a["scene_index"], [])}:
            by_scene[a["scene_index"]].append(a)
    assets = []
    for span in vo["scenes"]:
        clips = by_scene.get(span["scene_index"]) or by_scene[max(by_scene)]
        if missing := [c["path"] for c in clips if not (s.storage_dir / c["path"]).is_file()]:
            raise GateError(f"source clip missing from storage: {missing[0]}")
        n = max(1, math.ceil((span["end_ms"] - span["start_ms"]) / SHOT_MS))
        step = (span["end_ms"] - span["start_ms"]) / n
        for k in range(n):
            clip = clips[k % len(clips)]
            assets.append({key: clip[key] for key in clip if key not in ("order_index", "start_ms", "end_ms", "scene_index")}
                          | {"scene_index": span["scene_index"], "order_index": len(assets), "start_ms": span["start_ms"] + round(k * step),
                             "end_ms": span["start_ms"] + round((k + 1) * step)})
    return {"assets": assets, "stock_hits": src.get("stock_hits", 0), "captions": src.get("captions", "words"),
            "_meta": {"provider": "copied", "model": None, "cost_usd": 0.0}}


def pick_music(s: Settings, job_id: str) -> Path | None:
    """A background track from MUSIC_DIR, the same one on every re-render of a job; None when the folder is empty.
    Putting a track there is your statement that you may use it: only royalty-free or licensed music."""
    files = sorted(p for p in s.music_dir.glob("*") if p.suffix.lower() in MUSIC_EXT) if s.music_dir.is_dir() else []
    return files[int(hashlib.sha256(job_id.encode()).hexdigest(), 16) % len(files)] if files else None


async def stage_subtitles(ctx: dict) -> dict:
    s, job = ctx["settings"], ctx["job"]
    cues = media.build_cues(ctx["up"]["VOICE"]["segments"])
    d = ctx["dir"] / "subtitles"
    srt, vtt = d / f"{job.language}.srt", d / f"{job.language}.vtt"
    media.write_subtitles(cues, srt, vtt)
    ass = d / f"{job.language}.ass"
    segs = ctx["up"]["VOICE"]["segments"]
    if job.language == "ja":  # captions break only at spaces, and a line holds ~6 kana/kanji at the caption size
        segs = [x | {"text": ja_caption_spacing(x["text"])} for x in segs]
    media.write_word_captions(segs, ass)
    return {"srt_path": rel(s, srt), "vtt_path": rel(s, vtt), "ass_path": rel(s, ass), "cue_count": len(cues),
            "max_chars_per_line": max((len(l) for c in cues for l in c["lines"]), default=0),
            "max_cue_duration_s": round(max((c["end_ms"] - c["start_ms"] for c in cues), default=0) / 1000, 2),
            "_meta": {"provider": "subtitle_builder"}}


async def stage_render(ctx: dict) -> dict:
    s, up = ctx["settings"], ctx["up"]
    cards = [s.storage_dir / a["path"] for a in up["VISUALS"]["assets"]]
    durs = [(a["end_ms"] - a["start_ms"]) / 1000 for a in up["VISUALS"]["assets"]]
    d, font = ctx["dir"] / "render", font_for(s, ctx["job"].language)
    out, thumb = d / "final.mp4", d / "thumb.jpg"
    words = up["VISUALS"].get("captions") == "words"  # word captions are the clip formats' only text, so always burned
    burn = words or ctx["job"].burn_subtitles
    subs = s.storage_dir / up["SUBTITLES"]["ass_path" if words else "srt_path"] if burn else None
    music = pick_music(s, ctx["job"].id)
    offsets, tail = [a.get("clip_offset_s", 0.0) for a in up["VISUALS"]["assets"]], 0.0
    if ctx["job"].brand_name:  # client video: closing card with the name and logo, held after the voice ends
        logo = ctx["job"].brand_logo_path
        await asyncio.to_thread(media.render_brand_card, ctx["job"].brand_name, Path(logo) if logo else None, font, d / "brand.png")
        cards, durs, offsets, tail = cards + [d / "brand.png"], durs + [media.BRAND_CARD_S], offsets + [0.0], media.BRAND_CARD_S
    cmd = await asyncio.to_thread(media.render_video, s.ffmpeg_path, cards, durs, s.storage_dir / up["VOICE"]["audio_path"], out,
                                  subs, font if burn else None, music, offsets,
                                  [x["start_ms"] / 1000 for x in up["VOICE"]["scenes"][1:]] if words else None, tail)
    await asyncio.to_thread(media.run, [s.ffmpeg_path, "-y", "-v", "error", "-ss", "0.3", "-i", str(out), "-frames:v", "1", "-q:v", "3", str(thumb)])
    info = media.probe(s.ffprobe_path, out)
    v = next((x for x in info["streams"] if x["codec_type"] == "video"), {})
    a = next((x for x in info["streams"] if x["codec_type"] == "audio"), {})
    return {"video_path": rel(s, out), "thumbnail_path": rel(s, thumb), "container": "mp4",
            "video_codec": v.get("codec_name"), "audio_codec": a.get("codec_name"), "width": v.get("width"), "height": v.get("height"),
            "fps": v.get("r_frame_rate"), "duration_s": round(float(info["format"]["duration"]), 3), "bytes": int(info["format"]["size"]),
            "subtitles_burned": burn, "music_track": music.name if music else None, "ffmpeg_command": " ".join(cmd),
            "_meta": {"provider": "ffmpeg"}}


async def stage_quality(ctx: dict) -> dict:
    up = ctx["up"]
    r, sc, sub, vo = up["RENDER"], up["SCRIPT"], up["SUBTITLES"], up["VOICE"]
    tail = media.BRAND_CARD_S if ctx["job"].brand_name else 0.0
    # the format only sets the default target; the gate judges against the job's own target
    lo, hi = duration_window(ctx["job"])
    broken = await asyncio.to_thread(media.decode_errors, ctx["settings"].ffmpeg_path, ctx["settings"].storage_dir / r["video_path"])

    def check(rule, severity, passed, expected, actual, message):
        return {"rule": rule, "severity": severity, "passed": bool(passed), "expected": str(expected), "actual": str(actual), "message": message}

    checks = [
        check("duration_window", "error", lo <= r["duration_s"] <= hi, f"{lo:.0f}-{hi:.0f}s", f"{r['duration_s']:.1f}s",
              f"Video length is inside the {lo}-{hi}s window." if lo <= r["duration_s"] <= hi else f"Video length is outside {lo}-{hi}s: lengthen or shorten the script."),
        check("aspect_ratio", "error", (r["width"], r["height"]) == (1080, 1920), "1080x1920", f"{r['width']}x{r['height']}", "Vertical 9:16 resolution."),
        check("codecs", "error", (r["video_codec"], r["audio_codec"]) == ("h264", "aac"), "h264/aac", f"{r['video_codec']}/{r['audio_codec']}", "Platform-safe codecs."),
        check("decodes", "error", broken == 0, "0 decode errors", f"{broken} error line(s)",
              "Every frame decodes." if not broken else "The video has broken frames (a source clip was damaged): re-run from VISUALS."),
        check("audio_present", "error", r["audio_codec"] is not None and vo["duration_s"] > 1, "voice-over", f"{vo['duration_s']}s", "Voice-over is present."),
        check("av_sync", "error", abs(r["duration_s"] - vo["duration_s"] - tail) <= 0.3, "<=0.3s drift", f"{abs(r['duration_s'] - vo['duration_s'] - tail):.2f}s",
              "Video and voice-over lengths match." if not tail else "Video is the voice-over plus the closing card."),
        check("asset_licence", "error", all(a["licence"] != "unknown" for a in up["VISUALS"]["assets"]), "no unknown licence",
              ",".join(sorted({a["licence"] for a in up["VISUALS"]["assets"]})), "Every visual asset has a recorded licence."),
        check("loudness", "warning", vo.get("loudness_lufs") is not None and abs(vo["loudness_lufs"] - media.LOUDNORM_TARGET_LUFS) <= 1.5,
              f"{media.LOUDNORM_TARGET_LUFS}±1.5 LUFS", "not measurable (silent audio?)" if vo.get("loudness_lufs") is None else f"{vo['loudness_lufs']} LUFS",
              "Voice-over loudness is close to the target." if vo.get("loudness_lufs") is not None and abs(vo["loudness_lufs"] - media.LOUDNORM_TARGET_LUFS) <= 1.5
              else "Voice-over loudness drifted from the target; listen before approving."),
        check("safety_terms", "warning", not (hits := risky_terms(script_texts(sc))), "no risky claim terms", ", ".join(hits) or "none",
              "No wording from the risky-claims list (app/safety_terms.txt)." if not hits else "Risky claim wording found: check it before approving."),
        check("subtitle_readability", "warning", sub["max_chars_per_line"] <= 32 and sub["max_cue_duration_s"] <= 7,
              "<=32 chars/line, <=7s/cue", f"{sub['max_chars_per_line']} chars, {sub['max_cue_duration_s']}s", "Subtitle lines are short enough to read."),
        check("platform_limits", "warning", len(sc["title"]) <= 90 and len(sc["caption"]) <= 300 and 3 <= len(sc["hashtags"]) <= 8,
              "title<=90, caption<=300, 3-8 tags", f"{len(sc['title'])}, {len(sc['caption'])}, {len(sc['hashtags'])}", "Title, caption and hashtags fit every platform."),
        check("claim_grounding", "warning", False, "sourced claims", f"{len(sc.get('sources', []))} source(s) named by the writer, none opened",
              "Claims are not source-checked in this version: open the listed sources and verify the facts before approving."),
    ]
    s, cost = ctx["settings"], 0.0
    if ctx["job"].source_job_id:  # the frames and facts were reviewed on the source; a paid second look buys nothing
        checks.append(check("qa_review", "warning", True, "reviewed on the source", f"translation of {ctx['job'].source_job_id}",
                            "Same clips as the source job: check the captions' font and line breaks, not the pictures."))
    elif s.qa_review and s.llm_provider != "fixture" and s.anthropic_api_key:
        import anthropic

        try:
            frames = await asyncio.to_thread(qa_review.scene_frames, s.ffmpeg_path, str(s.storage_dir / r["video_path"]), vo["scenes"])
            client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key.get_secret_value(), max_retries=2)
            data, cost = await qa_review.review(client, s.qa_review_model, sc["scenes"], sc.get("sources", []), frames)
        except Exception:  # noqa: BLE001 - advisory: a broken frame grab must not fail a finished render
            data = None
        checks.append(qa_review.as_check(data))
    blocking = [c for c in checks if c["severity"] == "error" and not c["passed"]]
    return {"checks": checks, "passed": not blocking, "_meta": {"provider": "quality_gate", "cost_usd": round(cost, 6)}}


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
                   "previous": json.loads(prev.output_json) if prev else None, "voice": voice, "sm": self.sm}
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
    out = finish_script(script, job, s.storage_dir / "jobs" / job_id, attempt, speech_rate(s, job.language))
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
    media.upload_copy(s.ffmpeg_path, s.ffprobe_path, d / "video.mp4", d / "upload.mp4")  # what gets posted from the browser
    sc = out["SCRIPT"]
    # pinned comment: the caption's closing question, plus the link to the previous episode (the only clickable link a Short gets);
    # a client's video has no previous episode
    question = re.split(r"(?<=[.!?])\s+", sc["caption"].strip())[-1]
    (d / "caption.txt").write_text(f"{sc['title']}\n\n{sc['caption']}\n\n{' '.join(sc['hashtags'])}\n\n"
                                   f"--- pinned comment ---\n{question}\n" + ("" if job.brand_name else "Previous episode: <paste link>\n"), encoding="utf-8")
    apr = db.get(Approval, job.approval_id)
    manifest = {
        "job_id": job.id, "topic": job.input_topic, "language": lang, "platforms": json.loads(job.target_platforms),
        "ai_generated": True,  # for platform AI-content disclosure (BACKLOG R9)
        "title": sc["title"], "caption": sc["caption"], "hashtags": sc["hashtags"], "sources": sc.get("sources", []),
        "video": {k: out["RENDER"][k] for k in ("width", "height", "duration_s", "video_codec", "audio_codec", "fps", "bytes")},
        "assets": out["VISUALS"]["assets"],
        "music": {"file": out["RENDER"]["music_track"], "licence": "owned"} if out["RENDER"].get("music_track") else None,
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
