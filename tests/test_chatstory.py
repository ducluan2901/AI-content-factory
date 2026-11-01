"""Chat-story prototype, offline: timeline, frames that fill up, a video exactly as long as the timeline."""
import json
import os
import shutil
from pathlib import Path

import pytest
from PIL import Image

from app import chatstory
from app.media import probe

FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")
SAMPLE = chatstory.ChatScript.model_validate(json.loads((Path(__file__).parent / "fixtures" / "chat_sample.json").read_text(encoding="utf-8")))


def test_timeline_types_before_others_and_holds_the_end():
    tl = chatstory.timeline(SAMPLE)
    assert tl[0] == (0, False, 1.8) and tl[-1] == (len(SAMPLE.messages), False, 3.0)
    assert [s for s, typing, _ in tl if not typing][1:-1] == list(range(1, len(SAMPLE.messages) + 1))
    assert not any(typing and SAMPLE.messages[s].sender == SAMPLE.me for s, typing, _ in tl)  # my own messages have no dots


def test_frames_fill_up_and_overflow_scrolls(tmp_path):
    few, many = tmp_path / "few.png", tmp_path / "many.png"
    chatstory.render_frame(SAMPLE, 2, False, False, few)
    chatstory.render_frame(SAMPLE, len(SAMPLE.messages), False, True, many)
    white = lambda p: sum(1 for px in Image.open(p).crop((0, chatstory.TOP, chatstory.W, chatstory.BOTTOM)).getdata() if px == (255, 255, 255))
    assert white(many) < white(few)  # more bubbles on screen
    assert Image.open(many).getpixel((5, 5)) == (255, 214, 10)  # the outro band replaces the hook at the end


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="FFmpeg not found")
def test_video_is_as_long_as_its_timeline(tmp_path):
    music = tmp_path / "m.wav"
    import subprocess
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000", "-t", "2", str(music)], check=True)
    video = chatstory.render(SAMPLE, tmp_path / "out", FFMPEG, music)
    want = sum(secs for _, _, secs in chatstory.timeline(SAMPLE))
    info = probe(FFPROBE, video)
    assert abs(float(info["format"]["duration"]) - want) < 0.15
    assert {s["codec_name"] for s in info["streams"]} == {"h264", "aac"}
