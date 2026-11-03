"""Media processing: title cards (Pillow), FFmpeg render/probe, SRT/VTT from voice timings."""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1080, 1920
# keep text out of the platform UI chrome (TikTok/Reels caption + buttons)
SAFE_TOP, SAFE_BOTTOM, SAFE_X = 260, 480, 90
PALETTES = [((18, 24, 64), (88, 40, 160)), ((10, 40, 60), (20, 130, 140)), ((50, 14, 50), (180, 50, 90)), ((20, 20, 24), (60, 70, 110))]
HIGHLIGHT = (255, 196, 60)


def plain_words(text: str) -> set[str]:
    """Lower-case words without punctuation, for matching a headline's words inside a spoken sentence."""
    return {w for w in (re.sub(r"[\W_]+", "", x.lower()) for x in text.split()) if w}


class MediaError(Exception):
    pass


def run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    except FileNotFoundError as e:
        raise MediaError(f"executable not found: {cmd[0]} (set FFMPEG_PATH / FFPROBE_PATH)") from e
    if proc.returncode != 0:
        raise MediaError(f"{Path(cmd[0]).name} failed ({proc.returncode}): {proc.stderr[-800:]}")
    return proc.stdout


def probe(ffprobe: str, path: Path) -> dict:
    return json.loads(run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]))


def duration_s(ffprobe: str, path: Path) -> float:
    return float(probe(ffprobe, path)["format"]["duration"])


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    # break only at spaces: in Vietnamese every space is a syllable boundary, so no mid-syllable breaks
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def _draw_caption(img: Image.Image, text: str, index: int, font_path: str, highlight: set[str] = frozenset()) -> None:
    """Shared text layer for a gradient card and a stock photo: the spoken sentence, big, inside the safe area.
    Words in `highlight` (the scene headline's words) are drawn in colour so the key words pop (kinetic captions)."""
    draw = ImageDraw.Draw(img)
    max_w, max_h = W - 2 * SAFE_X, H - SAFE_TOP - SAFE_BOTTOM
    size = 112 if index == 0 else 96
    while True:
        font = ImageFont.truetype(font_path, size)
        lines = wrap(draw, text, font, max_w)
        line_h = int(size * 1.25)
        if len(lines) * line_h <= max_h or size <= 48:
            break
        size -= 6
    y = SAFE_TOP + (max_h - len(lines) * line_h) // 2
    if lines:  # a text-free card (word captions come at render) gets no accent bar either
        draw.rounded_rectangle([SAFE_X, y - 50, SAFE_X + 140, y - 36], radius=7, fill=HIGHLIGHT)
    space = draw.textlength(" ", font=font)
    for line in lines:
        x = SAFE_X
        for word in line.split():
            fill = HIGHLIGHT if plain_words(word) & highlight else (255, 255, 255)
            draw.text((x, y), word, font=font, fill=fill, stroke_width=4, stroke_fill=(0, 0, 0))
            x += draw.textlength(word, font=font) + space
        y += line_h


def render_card(text: str, index: int, font_path: str, out: Path, highlight: set[str] = frozenset()) -> None:
    top, bottom = PALETTES[index % len(PALETTES)]
    grad = Image.linear_gradient("L").resize((W, H))
    img = Image.composite(Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), grad)
    _draw_caption(img, text, index, font_path, highlight)
    img.save(out)


def render_photo_card(photo_path: Path, text: str, index: int, font_path: str, out: Path, highlight: set[str] = frozenset()) -> None:
    """A stock photo cropped to 9:16, darkened under the safe area for legibility, with the same caption as a card."""
    img = ImageOps.exif_transpose(Image.open(photo_path)).convert("RGB")
    img = ImageOps.fit(img, (W, H), method=Image.LANCZOS)
    shade = Image.new("L", (1, H), 0)
    for y in range(H):
        shade.putpixel((0, y), int(150 * max(0, (y - (SAFE_TOP - 80)) / (H - SAFE_TOP + 80))))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade.resize((W, H)))
    _draw_caption(img, text, index, font_path, highlight)
    img.save(out)


def concat_audio(ffmpeg: str, parts: list[Path], out: Path) -> None:
    args = [ffmpeg, "-y", "-v", "error"]
    for p in parts:
        args += ["-i", str(p)]
    labels = "".join(f"[{i}:a]" for i in range(len(parts)))
    args += ["-filter_complex", f"{labels}concat=n={len(parts)}:v=0:a=1[a]", "-map", "[a]", "-ar", "48000", str(out)]
    run(args)


FPS = 30
# each card lands with a quick zoom-out "punch" (PUNCH_FRAMES frames), then drifts in slowly up to ZOOM_MAX
PUNCH_FRAMES, PUNCH_ZOOM, DRIFT_PER_FRAME, ZOOM_MAX = 8, 1.10, 0.0006, 1.25
MUSIC_GAIN_DB = -17  # music under a -14 LUFS voice: present, never competing
WHOOSH_S, WHOOSH_GAIN_DB = 0.45, -12


SUB_SCRIPT_H = 288  # libass default script height for SRT; MarginV and FontSize below are in these units, not pixels
SUB_SIZE, SUB_MARGIN_V = 11, 75  # ~73 px text, bottom edge ~500 px up: clear of the platform UI (SAFE_BOTTOM = 480)


def subtitle_filter(srt: Path, font_path: str, workdir: Path) -> str:
    """Burn-in filter. libass wants Windows paths escaped ('C\\:/...'), so instead copy the SRT and the font next
    to the output and refer to them by relative name; the caller runs ffmpeg with cwd=workdir. Takes an ASS file too:
    it carries its own style, so only the font name is forced, to match the font copied here."""
    from PIL import ImageFont

    fonts = workdir / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(font_path, fonts / Path(font_path).name)
    name = f"burn{srt.suffix}"
    shutil.copyfile(srt, workdir / name)
    family = ImageFont.truetype(font_path, 20).getname()[0]
    if srt.suffix == ".ass":
        return f"subtitles={name}:fontsdir=fonts:force_style='FontName={family}'"
    style = (f"FontName={family},Bold=1,FontSize={SUB_SIZE},Alignment=2,MarginV={SUB_MARGIN_V},"
             "MarginL=14,MarginR=14,Outline=2,Shadow=0")
    return f"subtitles={name}:fontsdir=fonts:force_style='{style}'"


def render_video(ffmpeg: str, cards: list[Path], durations: list[float], audio: Path, out: Path,
                 srt: Path | None = None, font_path: str | None = None, music: Path | None = None,
                 offsets: list[float] | None = None, whoosh_at: list[float] | None = None) -> list[str]:
    """Each still lands with a punch zoom and then drifts in slowly, before concatenation. An .mp4 in `cards` is a
    stock clip instead: it plays from its offset (seconds), cropped to fill 9:16, held on its last frame if too short.
    Each still is a single-frame input and zoompan's d= turns that one frame into exactly d output frames, which
    ends the clip. (A looped input made every clip endless, so concat never left scene 1: the first real video showed
    scene 1's card for its whole length while a duration-only test passed.)
    With srt and font_path the subtitles are burned into the picture (a platform-independent hard caption).
    With music, the track loops under the voice at MUSIC_GAIN_DB and fades out over the last two seconds.
    whoosh_at: seconds where a whoosh peaks (the start of each new fact)."""
    # burn-in runs ffmpeg with another cwd, so every path must be absolute (STORAGE_DIR defaults to ./storage)
    cards, audio, out = [c.resolve() for c in cards], audio.resolve(), out.resolve()
    srt = srt.resolve() if srt else None
    cmd = [ffmpeg, "-y", "-v", "error"]
    offsets = offsets or [0.0] * len(cards)
    for card, d, off in zip(cards, durations, offsets):
        if card.suffix == ".mp4":
            cmd += ["-ss", f"{off:.2f}", "-t", f"{d + 1:.2f}", "-i", str(card)]
        else:
            cmd += ["-i", str(card)]  # one frame; no -loop, see the docstring
    cmd += ["-i", str(audio)]
    n = len(cards)
    parts = []
    for i, d in enumerate(durations):
        frames = max(1, round(d * FPS))
        if cards[i].suffix == ".mp4":
            parts.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,"
                         f"tpad=stop_mode=clone:stop_duration={d:.3f},trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}]")
            continue
        z = (f"if(lt(on,{PUNCH_FRAMES}),{PUNCH_ZOOM}-{(PUNCH_ZOOM - 1) / PUNCH_FRAMES:.5f}*on,"
             f"min(1+{DRIFT_PER_FRAME}*(on-{PUNCH_FRAMES}),{ZOOM_MAX}))")
        parts.append(f"[{i}:v]scale={W * 2}:{H * 2},zoompan=z='{z}':d={frames}:s={W}x{H}:fps={FPS},setsar=1[v{i}]")
    labels = "".join(f"[v{i}]" for i in range(n))
    burn = f",{subtitle_filter(srt, font_path, out.parent)}" if srt else ""
    parts.append(f"{labels}concat=n={n}:v=1:a=0{burn},format=yuv420p[v]")
    amap, mix = f"{n}:a", [f"[{n}:a]"]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music.resolve())]
        fade_at = max(sum(durations) - 2, 0)
        parts.append(f"[{n + 1}:a]volume={MUSIC_GAIN_DB}dB,afade=t=out:st={fade_at:.2f}:d=2[m]")
        mix.append("[m]")
    if whoosh_at:  # a short swell of filtered noise that peaks on each cut: marks "next fact" like the reference videos
        k = len(whoosh_at)
        parts.append(f"anoisesrc=d={WHOOSH_S}:c=pink:a=0.5,highpass=f=500,lowpass=f=5000,afade=t=in:d={WHOOSH_S * 0.6:.2f},"
                     f"afade=t=out:st={WHOOSH_S * 0.6:.2f}:d={WHOOSH_S * 0.4:.2f},volume={WHOOSH_GAIN_DB}dB,asplit={k}"
                     + "".join(f"[w{j}]" for j in range(k)))
        for j, t in enumerate(whoosh_at):
            parts.append(f"[w{j}]adelay={round(max(0.0, t - WHOOSH_S * 0.6) * 1000)}:all=1[wd{j}]")
            mix.append(f"[wd{j}]")
    if len(mix) > 1:
        parts.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0[a]")
        amap = "[a]"
    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "[v]", "-map", amap,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "160k",
        "-shortest",  # frames are rounded per scene (+-1/30 s each); never let a rounding tail outlive the voice
        "-movflags", "+faststart", str(out),
    ]
    run(cmd, cwd=out.parent if srt else None)
    return cmd


def _ts(ms: int, sep: str) -> str:
    h, ms = divmod(int(ms), 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02}{sep}{ms:03}"


def build_cues(segments: list[dict], max_line: int = 32) -> list[dict]:
    """Split each timed sentence into cues of <= 2 lines x max_line chars, timed by character share."""
    cues = []
    for seg in segments:
        lines, cur = [], ""
        for w in seg["text"].split():  # greedy fill at spaces only: no mid-syllable breaks
            if cur and len(f"{cur} {w}") > max_line:
                lines.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            lines.append(cur)
        groups = [lines[i:i + 2] for i in range(0, len(lines), 2)]
        total = sum(len(" ".join(g)) for g in groups) or 1
        # TTS sentence spans can overlap by a few ms; never start before the previous cue ends
        t = max(seg["start_ms"], cues[-1]["end_ms"] if cues else 0)
        span = max(seg["end_ms"] - t, 1)
        for g in groups:
            end = t + span * len(" ".join(g)) / total
            cues.append({"start_ms": int(t), "end_ms": int(end), "lines": g})
            t = end
    return cues


LOUDNORM_TARGET_LUFS = -14.0  # BACKLOG 3.3: the voice-over target


def normalize_loudness(ffmpeg: str, in_path: Path, out_path: Path) -> float | None:
    """Single-pass EBU R128 loudness normalisation (shortcut: single-pass, not measure-then-apply two-pass,
    so the reported figure is an estimate, not an exact measurement; upgrade to two-pass if review shows drift).
    Returns the achieved integrated loudness in LUFS, or None if it could not be measured (true digital silence
    reports -inf, which is not a real measurement and is not JSON-serialisable, so it becomes None here too).
    """
    cmd = [ffmpeg, "-y", "-hide_banner", "-nostats", "-i", str(in_path),
           "-af", f"loudnorm=I={LOUDNORM_TARGET_LUFS}:TP=-1.5:LRA=11:print_format=json",
           "-ar", "48000", str(out_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        raise MediaError(f"executable not found: {cmd[0]} (set FFMPEG_PATH)") from e
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg loudnorm failed ({proc.returncode}): {proc.stderr[-800:]}")
    m = re.search(r"\{[^{}]*\}", proc.stderr)
    if not m:
        return None
    data = json.loads(m.group(0))
    try:
        value = float(data.get("output_i"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def write_subtitles(cues: list[dict], srt: Path, vtt: Path) -> None:
    srt.write_text("\n".join(f"{i}\n{_ts(c['start_ms'], ',')} --> {_ts(c['end_ms'], ',')}\n" + "\n".join(c["lines"]) + "\n" for i, c in enumerate(cues, 1)), encoding="utf-8")
    vtt.write_text("WEBVTT\n\n" + "\n".join(f"{_ts(c['start_ms'], '.')} --> {_ts(c['end_ms'], '.')}\n" + "\n".join(c["lines"]) + "\n" for c in cues), encoding="utf-8")


WORD_CHUNK = 3  # words on screen at once
WORD_COLOR = "&H3CC4FF&"  # HIGHLIGHT in ASS's blue-green-red order
POP = "{\\fscx70\\fscy70\\t(0,90,\\fscx100\\fscy100)}"
ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Arial,140,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,9,3,5,{SAFE_X},{SAFE_X},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_ts(ms: float) -> str:
    cs = int(ms) // 10
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def write_word_captions(segments: list[dict], out: Path) -> int:
    """Word-by-word captions, the look of the facts reference videos: up to WORD_CHUNK words big in the middle of the
    screen, the word being spoken in colour. With word timings from the TTS (ElevenLabs) each word shows from its own
    start; otherwise each word gets a share of its sentence's span by length. Returns the number of caption events."""
    clean = lambda s: re.sub(r"[{}\\]", "", s)  # braces and backslashes are ASS override syntax
    events, prev_end = [], 0
    for seg in segments:
        t0 = max(seg["start_ms"], prev_end)  # TTS sentence spans can overlap by a few ms
        if seg.get("words"):
            words, starts = [clean(w["text"]) for w in seg["words"]], [max(w["start_ms"], t0) for w in seg["words"]]
        else:
            words = clean(seg["text"]).split()
            span, total, before = max(seg["end_ms"] - t0, 1), sum(len(w) + 1 for w in words), 0
            starts = []
            for w in words:
                starts.append(t0 + span * before // total)
                before += len(w) + 1
        if not words:
            continue
        ends = starts[1:] + [max(seg["end_ms"], starts[-1])]  # a word stays until the next one starts: no blinking
        for i, (s0, e0) in enumerate(zip(starts, ends)):
            c = i - i % WORD_CHUNK
            chunk = words[c:c + WORD_CHUNK]
            text = " ".join(f"{{\\c{WORD_COLOR}}}{x}{{\\c&HFFFFFF&}}" if c + j == i else x for j, x in enumerate(chunk))
            if i == c:  # a new group of words pops in: 70% to full size over 90 ms
                text = POP + text
            events.append(f"Dialogue: 0,{_ass_ts(s0)},{_ass_ts(e0)},Word,,0,0,0,,{text}")
        prev_end = ends[-1]
    out.write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8")
    return len(events)


UPLOAD_MAX_BYTES = 9_500_000  # Claude in Chrome's file upload takes at most 10 MB per call


def upload_copy(ffmpeg: str, ffprobe: str, src: Path, out: Path, max_bytes: int = UPLOAD_MAX_BYTES) -> int:
    """A copy of the video small enough for browser upload: the file itself when it fits, otherwise re-encoded at the
    bitrate that fits its length, lowered by 15% per try until it does. Returns the copy's size in bytes."""
    if src.stat().st_size <= max_bytes:
        shutil.copyfile(src, out)
        return out.stat().st_size
    total_kbps = max_bytes * 8 / 1000 / duration_s(ffprobe, src) * 0.92  # headroom for the container and rate control
    for _ in range(4):
        v = max(int(total_kbps) - 128, 300)
        run([ffmpeg, "-y", "-v", "error", "-i", str(src), "-c:v", "libx264", "-preset", "slow", "-b:v", f"{v}k",
             "-maxrate", f"{int(v * 1.3)}k", "-bufsize", f"{v * 2}k", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])
        if out.stat().st_size <= max_bytes:
            return out.stat().st_size
        total_kbps *= 0.85
    raise MediaError(f"could not fit {src.name} under {max_bytes} bytes")
