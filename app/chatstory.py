"""Chat-story videos: a comedy told as a group chat that fills up message by message (Messenger-style bubbles,
typing dots, a pop on every message, music, a big hook line on top). No voice-over and no stock photos.

Prototype for founder review, outside the job pipeline: `python -m app.chatstory <out_dir> [script.json]` writes
script.json and video.mp4. Without a script file it asks Claude for one (SCRIPT_MODEL / SCRIPT_EFFORT).
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, field_validator

from app.config import Settings
from app.media import probe, wrap
from app.providers import AnthropicApiProvider, extract_json

W, H, FPS = 1080, 1920, 30
FONT, FONT_BOLD = r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\seguisb.ttf"
TOP, BOTTOM = 470, 1430  # chat area: under the hook band, above the platform caption and buttons
BLUE, GREY, INK, MUTED = (0, 132, 255), (233, 233, 238), (20, 20, 22), (130, 130, 140)
AVATAR_COLOURS = [(255, 149, 0), (52, 199, 89), (175, 82, 222), (255, 59, 48), (90, 200, 250), (255, 204, 0)]
MUSIC_DB, POP_DB = -15, -6


class Message(BaseModel):
    sender: str = Field(min_length=1, max_length=30)
    text: str = Field(min_length=1, max_length=160)


class ChatScript(BaseModel):
    title: str = Field(min_length=3, max_length=90)
    caption: str = Field(min_length=3, max_length=300)
    hashtags: list[str] = Field(min_length=3, max_length=8)
    hook: str = Field(min_length=3, max_length=60)  # the big line above the chat, read in the first 2 seconds
    chat_name: str = Field(min_length=1, max_length=40)
    me: str = Field(min_length=1, max_length=30)  # whose phone this is: their messages are blue, on the right
    messages: list[Message] = Field(min_length=6, max_length=30)
    outro: str = Field(min_length=3, max_length=80)  # the comment bait shown over the final chat

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        from app.pipeline import normalize_tags

        return normalize_tags(v)


PROMPT = """You are the head writer of a Vietnamese TikTok comedy channel. Write one CHAT-STORY video: the whole joke
is a group chat (Messenger/Zalo) that the viewer reads as it fills up, message by message. No narration.

Topic: {topic}

Work silently first: pitch five premises from very specific Vietnamese everyday life that 20-30 year olds have
lived (office lunch orders, family group chat, landlord, Tết, mother's calls, roommates, crush, boss on Zalo at
10 pm). Pick the one with the strongest "y chang nhà/phòng tao" recognition AND a twist nobody sees coming.
Write it, test it as a strict editor (hook, escalation, payoff, relatable, comments, readable; rewrite anything
under 8/10), then polish.

Chat rules:
- 3 or 4 people with real Vietnamese names or roles ("Mẹ", "Chị Hằng", "Tuấn"); one of them is "me", the phone owner.
- 12 to 20 messages. Most are very short (1-8 words), the way people really text: lowercase is fine, "kkk",
  "ủa", "thôi", "ok", typos are fine if natural. Never longer than 20 words. No emoji characters.
- The comedy comes from rhythm: fast replies, a pause, an escalation every few messages, the twist in the last
  2-3 messages. The reader must get each joke without explanation.
- hook: the line shown big above the chat, at most 8 words, the situation in one jolt (e.g. "Khi cả phòng hỏi ăn gì trưa nay").
- outro: one line shown at the end that makes people comment or tag someone.
- Never mock a real person, group, region or brand. No invented statistics, prices or quotes. Nothing dangerous.
- title at most 90 characters, caption at most 300 ending with a question, 3-6 hashtags lowercase without diacritics.

Return ONLY JSON:
{{"title": "...", "caption": "...", "hashtags": ["#..."], "hook": "...", "chat_name": "...", "me": "<name of the phone owner>",
  "messages": [{{"sender": "<name>", "text": "..."}}], "outro": "..."}}"""


def font(size: int, bold: bool = False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT, size)


def timeline(script: ChatScript) -> list[tuple[int, bool, float]]:
    """(messages shown, typing dots visible, seconds) per still. Reading time grows with length; the end holds."""
    out = [(0, False, 1.8)]  # the hook alone first, so it is read
    for i, m in enumerate(script.messages):
        if m.sender != script.me and (i == 0 or script.messages[i - 1].sender != m.sender):
            out.append((i, True, 0.45))  # someone else starts typing
        # shortcut: a fixed reading speed (~20 chars/s plus a beat); tune from completion rates once videos are posted
        out.append((i + 1, False, min(2.6, 0.7 + len(m.text) / 20)))
    out.append((len(script.messages), False, 3.0))  # outro over the finished chat
    return out


def _bubble_items(script: ChatScript, shown: int, typing: bool) -> list[tuple[str, int, object]]:
    """(kind, height, payload) for every visible row, in order."""
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    rows = []
    for i, m in enumerate(script.messages[:shown]):
        first_of_run = i == 0 or script.messages[i - 1].sender != m.sender
        if first_of_run and m.sender != script.me:
            rows.append(("name", 44, m.sender))
        lines = wrap(d, m.text, font(46), 700)
        rows.append(("msg", 34 * 2 + len(lines) * 58 + 12, (m.sender, lines)))
    if typing and shown < len(script.messages):
        nxt = script.messages[shown].sender
        if shown == 0 or script.messages[shown - 1].sender != nxt:
            rows.append(("name", 44, nxt))
        rows.append(("typing", 110, nxt))
    return rows


def render_frame(script: ChatScript, shown: int, typing: bool, outro: bool, out: Path) -> None:
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    people = list(dict.fromkeys(m.sender for m in script.messages if m.sender != script.me))
    colour = {p: AVATAR_COLOURS[i % len(AVATAR_COLOURS)] for i, p in enumerate(people)}
    # chat header
    d.rectangle([0, 330, W, 450], fill=(248, 248, 250))
    d.line([0, 450, W, 450], fill=(225, 225, 230), width=2)
    d.text((W // 2, 372), script.chat_name, font=font(40, True), fill=INK, anchor="mm")
    d.text((W // 2, 418), f"{len(people) + 1} thành viên", font=font(28), fill=MUTED, anchor="mm")
    # rows stacked from the bottom; older rows scroll off the top
    rows = _bubble_items(script, shown, typing)
    y = BOTTOM - sum(h for _, h, _ in rows)
    canvas = Image.new("RGB", (W, max(BOTTOM - y, 1) + 20), (255, 255, 255))
    c = ImageDraw.Draw(canvas)
    cy = 0
    for kind, h, payload in rows:
        if kind == "name":
            c.text((150, cy + 14), payload, font=font(30), fill=MUTED)
        elif kind == "msg":
            sender, lines = payload
            mine = sender == script.me
            bw = max(c.textlength(line, font=font(46)) for line in lines) + 68
            x0 = W - 60 - bw if mine else 150
            c.rounded_rectangle([x0, cy, x0 + bw, cy + h - 12], radius=42, fill=BLUE if mine else GREY)
            for k, line in enumerate(lines):
                c.text((x0 + 34, cy + 30 + k * 58), line, font=font(46), fill=(255, 255, 255) if mine else INK)
            if not mine:
                c.ellipse([60, cy + h - 12 - 72, 132, cy + h - 12], fill=colour[sender])
                c.text((96, cy + h - 12 - 36), sender[0].upper(), font=font(36, True), fill=(255, 255, 255), anchor="mm")
        else:  # typing dots
            c.rounded_rectangle([150, cy, 150 + 170, cy + h - 12], radius=42, fill=GREY)
            for k in range(3):
                c.ellipse([185 + k * 40, cy + 38, 207 + k * 40, cy + 60], fill=MUTED)
            c.ellipse([60, cy + h - 12 - 72, 132, cy + h - 12], fill=colour[payload])
            c.text((96, cy + h - 12 - 36), payload[0].upper(), font=font(36, True), fill=(255, 255, 255), anchor="mm")
        cy += h
    visible = canvas.crop((0, max(0, canvas.height - 20 - (BOTTOM - TOP)), W, canvas.height - 20))
    img.paste(visible, (0, BOTTOM - visible.height))
    # input bar under the chat
    d.rounded_rectangle([60, BOTTOM + 20, W - 60, BOTTOM + 100], radius=40, fill=(242, 242, 246))
    d.text((100, BOTTOM + 60), "Aa", font=font(34), fill=MUTED, anchor="lm")
    # hook band on top, outro band at the end
    band = script.outro if outro else script.hook
    lines = wrap(d, band, font(62, True), W - 140)
    d.rectangle([0, 0, W, 330], fill=(255, 214, 10) if outro else (18, 18, 20))
    ty = 165 - len(lines) * 38
    for line in lines:
        d.text((W // 2, ty + 38), line, font=font(62, True), fill=INK if outro else (255, 255, 255), anchor="mm")
        ty += 76
    img.save(out)


def render(script: ChatScript, out_dir: Path, ffmpeg: str, music: Path | None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames, lines, t, pops = out_dir / "frames", [], 0.0, []
    frames.mkdir(exist_ok=True)
    tl = timeline(script)
    for k, (shown, typing, secs) in enumerate(tl):
        p = frames / f"{k:03}.png"
        render_frame(script, shown, typing, k == len(tl) - 1, p)
        lines += [f"file '{p.resolve().as_posix()}'", f"duration {secs:.3f}"]
        if not typing and 0 < shown and (k == 0 or tl[k - 1][0] < shown):
            pops.append(t)  # a message just landed
        t += secs
    lines.append(f"file '{(frames / f'{len(tl) - 1:03}.png').resolve().as_posix()}'")  # concat needs the last frame twice
    (out_dir / "frames.txt").write_text("\n".join(lines), encoding="utf-8")
    pop = out_dir / "pop.wav"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=1320:sample_rate=48000", "-t", "0.07",
                    "-af", "afade=t=out:st=0.02:d=0.05", str(pop)], check=True)
    cmd = [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(out_dir / "frames.txt"), "-i", str(pop)]
    n = len(pops)
    fl = [f"[1:a]volume={POP_DB}dB,asplit={n}" + "".join(f"[p{i}]" for i in range(n))]
    fl += [f"[p{i}]adelay={int(s * 1000)}|{int(s * 1000)}[d{i}]" for i, s in enumerate(pops)]
    mix = "".join(f"[d{i}]" for i in range(n))
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        fl.append(f"[2:a]volume={MUSIC_DB}dB,afade=t=out:st={max(t - 2, 0):.2f}:d=2[m]")
        mix += "[m]"
        n += 1
    fl.append(f"{mix}amix=inputs={n}:duration=longest:normalize=0,atrim=0:{t:.3f}[a]")
    video = out_dir / "video.mp4"
    cmd += ["-filter_complex", ";".join(fl), "-map", "0:v", "-map", "[a]", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-t", f"{t:.3f}", "-movflags", "+faststart", str(video)]
    subprocess.run(cmd, check=True)
    return video


async def write_script(s: Settings, topic: str) -> tuple[ChatScript, float]:
    llm = AnthropicApiProvider(s.script_model, s.anthropic_api_key.get_secret_value(), s.script_effort)
    prompt, cost = PROMPT.format(topic=topic), 0.0
    for _ in range(2):
        resp = await llm.complete(prompt, timeout_s=s.stage_timeout_s)
        cost += resp.cost_usd
        try:
            return ChatScript.model_validate(extract_json(resp.text)), cost
        except ValueError as e:
            prompt += f"\n\nYour previous answer was invalid ({str(e)[:600]}). Return the corrected JSON only."
    raise RuntimeError("chat script invalid twice")


def main() -> None:
    s = Settings()
    out_dir = Path(sys.argv[1]).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 2:
        script, cost = ChatScript.model_validate_json(Path(sys.argv[2]).read_text(encoding="utf-8")), 0.0
    else:
        topic = sys.argv[3] if len(sys.argv) > 3 else "Tự chọn: tình huống đời thường dễ đồng cảm nhất với người Việt 20-30 tuổi."
        script, cost = asyncio.run(write_script(s, topic))
    (out_dir / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")
    from app.pipeline import pick_music

    video = render(script, out_dir, s.ffmpeg_path, pick_music(s, out_dir.name))
    print(f"{video} · {float(probe(s.ffprobe_path, video)['format']['duration']):.1f}s · script ${cost:.4f}")


if __name__ == "__main__":
    main()
