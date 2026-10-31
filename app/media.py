"""Media processing: title cards (Pillow), FFmpeg render/probe, SRT/VTT from voice timings."""
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
# keep text out of the platform UI chrome (TikTok/Reels caption + buttons)
SAFE_TOP, SAFE_BOTTOM, SAFE_X = 260, 480, 90
PALETTES = [((18, 24, 64), (88, 40, 160)), ((10, 40, 60), (20, 130, 140)), ((50, 14, 50), (180, 50, 90)), ((20, 20, 24), (60, 70, 110))]


class MediaError(Exception):
    pass


def run(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
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


def render_card(text: str, index: int, total: int, font_path: str, out: Path) -> None:
    top, bottom = PALETTES[index % len(PALETTES)]
    grad = Image.linear_gradient("L").resize((W, H))
    img = Image.composite(Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), grad)
    draw = ImageDraw.Draw(img)
    max_w, max_h = W - 2 * SAFE_X, H - SAFE_TOP - SAFE_BOTTOM
    size = 104 if index == 0 else 88
    while True:
        font = ImageFont.truetype(font_path, size)
        lines = wrap(draw, text, font, max_w)
        line_h = int(size * 1.3)
        if len(lines) * line_h <= max_h or size <= 48:
            break
        size -= 6
    y = SAFE_TOP + (max_h - len(lines) * line_h) // 2
    draw.rounded_rectangle([SAFE_X, y - 50, SAFE_X + 140, y - 36], radius=7, fill=(255, 196, 60))
    for line in lines:
        draw.text((SAFE_X, y), line, font=font, fill=(255, 255, 255))
        y += line_h
    draw.text((SAFE_X, H - SAFE_BOTTOM + 40), f"{index + 1}/{total}", font=ImageFont.truetype(font_path, 36), fill=(200, 200, 220))
    img.save(out)


def concat_audio(ffmpeg: str, parts: list[Path], out: Path) -> None:
    args = [ffmpeg, "-y", "-v", "error"]
    for p in parts:
        args += ["-i", str(p)]
    labels = "".join(f"[{i}:a]" for i in range(len(parts)))
    args += ["-filter_complex", f"{labels}concat=n={len(parts)}:v=0:a=1[a]", "-map", "[a]", "-ar", "48000", str(out)]
    run(args)


def render_video(ffmpeg: str, cards: list[Path], durations: list[float], audio: Path, out: Path) -> list[str]:
    cmd = [ffmpeg, "-y", "-v", "error"]
    for card, d in zip(cards, durations):
        cmd += ["-framerate", "30", "-loop", "1", "-t", f"{d:.3f}", "-i", str(card)]
    cmd += ["-i", str(audio)]
    n = len(cards)
    labels = "".join(f"[{i}:v]" for i in range(n))
    cmd += [
        "-filter_complex", f"{labels}concat=n={n}:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-map", f"{n}:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out),
    ]
    run(cmd)
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


def write_subtitles(cues: list[dict], srt: Path, vtt: Path) -> None:
    srt.write_text("\n".join(f"{i}\n{_ts(c['start_ms'], ',')} --> {_ts(c['end_ms'], ',')}\n" + "\n".join(c["lines"]) + "\n" for i, c in enumerate(cues, 1)), encoding="utf-8")
    vtt.write_text("WEBVTT\n\n" + "\n".join(f"{_ts(c['start_ms'], '.')} --> {_ts(c['end_ms'], '.')}\n" + "\n".join(c["lines"]) + "\n" for c in cues), encoding="utf-8")
