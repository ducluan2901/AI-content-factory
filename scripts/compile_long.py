"""Join exported Shorts into one 16:9 long video with a chapter card before each part (plan: watch hours for monetisation).

    python scripts/compile_long.py --name cho_meo_1 --title "9 điều chó mèo nhà bạn đang giấu bạn" job_01... job_01...

Writes storage/long/<name>/video.mp4 and description.txt (chapters with timestamps, every part's sources). FFmpeg only,
no API call. Each part is the job's export/upload.mp4, shown upright in the middle on a blurred copy of itself.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Settings  # noqa: E402

W, H, CARD_S = 1920, 1080, 2.5


def chapter_name(title: str) -> str:
    """The part of the job title before the colon or the first sentence, the way the schedule names topics."""
    return re.split(r"[:.]", title, maxsplit=1)[0].strip()


def ff(s: Settings, *args: str) -> None:
    subprocess.run([s.ffmpeg_path, "-v", "error", "-y", *args], check=True)


def duration(s: Settings, path: Path) -> float:
    return float(subprocess.run([s.ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout)


def render_card(s: Settings, text: str, out: Path) -> None:
    font = Path(s.font_path).as_posix().replace(":", "\\:")
    txt = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    ff(s, "-f", "lavfi", "-i", f"color=c=0x111827:s={W}x{H}:r=30", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(CARD_S),
       "-vf", f"drawtext=fontfile='{font}':text='{txt}':fontcolor=white:fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-shortest", str(out))


def render_part(s: Settings, src: Path, out: Path) -> None:
    ff(s, "-i", str(src),
       "-filter_complex", f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:5[bg];"
                          f"[0:v]scale=-2:{H}[fg];[bg][fg]overlay=(W-w)/2:0,fps=30[v]",
       "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(out))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--name", required=True, help="folder name under storage/long")
    ap.add_argument("--title", required=True)
    ap.add_argument("jobs", nargs="+", help="exported job ids, in playing order")
    args = ap.parse_args()
    s = Settings()
    out_dir = s.storage_dir / "long" / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    chapters, sources, t = [], [], 0.0
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, job in enumerate(args.jobs):
            export = s.storage_dir / "jobs" / job / "export"
            m = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
            name = chapter_name(m["title"])
            card, part = Path(tmp) / f"{i:02}_card.mp4", Path(tmp) / f"{i:02}_part.mp4"
            render_card(s, name, card)
            render_part(s, export / "upload.mp4", part)
            chapters.append(f"{int(t // 60):02}:{int(t % 60):02} {name}")
            t += CARD_S + duration(s, part)
            sources += m.get("sources", [])
            parts += [card, part]
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        ff(s, "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out_dir / "video.mp4"))
    chapters[0] = "00:00 " + chapters[0][6:]  # YouTube needs the first chapter at 00:00
    desc = f"{args.title}\n\nCác phần:\n" + "\n".join(chapters) + "\n\nNguồn:\n" + "\n".join(sources) + "\n"
    (out_dir / "description.txt").write_text(desc, encoding="utf-8")
    print(f"{out_dir / 'video.mp4'} {t:.0f}s, {len(args.jobs)} parts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
