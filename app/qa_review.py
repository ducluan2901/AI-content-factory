"""QA reviewer: before a human sees a video, Claude looks at one frame from the middle of every scene next to that
scene's narration, and reads the facts against the named sources. It flags the problems reviewers kept catching by
hand: a tiger in a cat scene, the same chow clip twice, a still card at the end, a fact that overstates its source.
Advisory only: the result is a warning check, never a block, and any failure degrades to "not reviewed"."""
import base64
import json
import logging
import subprocess

from app.providers import PRICES, extract_json

log = logging.getLogger(__name__)

PROMPT = """You are the QA reviewer of a 20-second Vietnamese facts short. Frame N is from the middle of scene N.
Script (scene number, narration, stock search used):
{scenes}
Sources named by the writer:
{sources}

Report only problems a viewer would notice or a fact-checker would reject; say nothing about what is fine.
1. Scene: the frame shows a different species or subject than the narration (a tiger in a cat scene, no person in a human-eye scene), is visibly the same clip as another scene, or is a still card or nearly black. A related stock shot of the right subject is ok even if the exact action is not visible.
2. Fact: a claim that is wrong, or clearly stronger than the named source states. Not style, not wording, not mild simplification.
3. Structure: the spoken numbering ("số hai", "số ba") skips or does not match the facts. The hook naming "số ba" before fact 1 is a deliberate open loop, not a problem.
At most 5 issues in total, each one short Vietnamese sentence of at most 20 words. A scene with no problem gets "ok": true and an empty issue.
Answer with JSON only:
{{"scenes": [{{"scene": 1, "ok": true, "issue": ""}}], "facts": ["fact or structure issue; empty list if none"], "score": 1-10}}"""


def scene_frames(ffmpeg: str, video: str, timeline: list[dict]) -> list[bytes]:
    """One 360-px-wide JPEG from the middle of each scene of the rendered video."""
    out = []
    for t in timeline:
        mid = (t["start_ms"] + t["end_ms"]) / 2000
        out.append(subprocess.run([ffmpeg, "-v", "error", "-ss", f"{mid:.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=360:-2",
                                   "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True, check=True).stdout)
    return out


async def review(client, model: str, scenes: list[dict], sources: list[str], frames: list[bytes]) -> tuple[dict | None, float]:
    """(review, cost_usd); review is None when the call or the answer failed."""
    content = []
    for i, f in enumerate(frames, 1):
        content += [{"type": "text", "text": f"Frame {i}:"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(f).decode()}}]
    lines = "\n".join(f'{i}. "{s["narration"]}" (search: {s.get("visual_query") or s.get("on_screen_text")})' for i, s in enumerate(scenes, 1))
    content.append({"type": "text", "text": PROMPT.format(scenes=lines, sources="\n".join(sources) or "none")})
    try:
        resp = await client.beta.messages.create(model=model, max_tokens=1500, output_config={"effort": "medium"},
                                            messages=[{"role": "user", "content": content}])
        p_in, p_out = PRICES.get(resp.model, PRICES.get(model, (0, 0)))
        cost = (resp.usage.input_tokens * p_in + resp.usage.output_tokens * p_out) / 1e6
        data = extract_json("".join(b.text for b in resp.content if b.type == "text"))
        return (data if isinstance(data.get("scenes"), list) else None), cost
    except Exception:  # noqa: BLE001 - an unreviewed video still reaches the human, who reviews everything anyway
        log.warning("QA review failed", exc_info=True)
        return None, 0.0


def as_check(data: dict | None) -> dict:
    """The review as one warning row of the quality gate, so the console shows it with the other checks."""
    if data is None:
        return {"rule": "qa_review", "severity": "warning", "passed": False, "expected": "reviewed", "actual": "not reviewed",
                "message": "QA reviewer could not run: check every scene yourself."}
    issues = [f"cảnh {s.get('scene')}: {s.get('issue')}" for s in data["scenes"] if not s.get("ok", True)]
    issues += [f"fact: {f}" for f in data.get("facts") or [] if f]
    issues = issues[:5]  # the model sometimes lists more than asked; the console row stays readable
    score = data.get("score")
    ok = not issues
    return {"rule": "qa_review", "severity": "warning", "passed": ok, "expected": "no scene or fact issue",
            "actual": f"score {score}/10, {len(issues)} issue(s)",
            "message": "QA reviewer found nothing to fix." if ok else "QA: " + "; ".join(issues)}


if __name__ == "__main__":  # self-check of the parsing, no API call
    good = {"scenes": [{"scene": 1, "ok": True, "issue": ""}], "facts": [], "score": 9}
    bad = {"scenes": [{"scene": 2, "ok": False, "issue": "tiger, not a cat"}], "facts": ["3 hearts is right, 'blue' overstated"], "score": 4}
    assert as_check(good)["passed"] and not as_check(bad)["passed"]
    assert "cảnh 2: tiger" in as_check(bad)["message"] and as_check(None)["actual"] == "not reviewed"
    print(json.dumps(as_check(bad), ensure_ascii=False))
