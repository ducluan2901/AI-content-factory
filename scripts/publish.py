"""Post approved videos to TikTok, YouTube Shorts and Facebook Reels from a browser window on this computer.

Only EXPORTED jobs (a person approved them in the console) are posted, each platform once: a Publication row is
recorded after every post and a job/platform pair that has one is skipped. The browser keeps its own sign-ins in
storage/browser_profile; sign in once with --login, by hand. The script never sees a password. A second set of
accounts (the English channel) gets its own profile folder with --profile: sign in there once, and pass it on every run.

    python scripts/publish.py --login                  # once: sign in to the three sites, then press Enter
    python scripts/publish.py --login --profile browser_profile_en   # the English channel's accounts, once
    python scripts/publish.py --dry-run                # fill everything, stop before each Post button
    python scripts/publish.py                          # post every approved video not yet posted
    python scripts/publish.py --platforms youtube_shorts --job job_01...

Platform pages change without notice: every step waits for a known element and stops with a message, never guessing.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Channel, ContentJob, Publication, make_sessionmaker, now  # noqa: E402

PLATFORMS = ("tiktok", "youtube_shorts", "facebook_reels")
URLS = {"tiktok": "https://www.tiktok.com/tiktokstudio/upload", "youtube_shorts": "https://studio.youtube.com",
        "facebook_reels": "https://www.facebook.com/reels/create"}
TIKTOK_ONLY = "#learnontiktok"


class Stop(Exception):
    """A page did not look as expected: nothing more is clicked on it."""


# ---------------------------------------------------------------- texts per platform


# TikTok search matches the first caption line; people type the subject with diacritics, so the topic tag is spelled out
_PET = "Sự thật thú vị về {0} – {0} nhà bạn"
SEARCH_LINES = {"#cho": _PET.format("chó"), "#meo": _PET.format("mèo"), "#chovameo": _PET.format("chó mèo"),
                "#cavang": _PET.format("cá vàng"), "#doimat": "Sự thật thú vị về đôi mắt của bạn", "#bachtuoc": "Sự thật thú vị về bạch tuộc"}


def tiktok_caption(m: dict) -> str:
    # shortcut: tags outside SEARCH_LINES get no search line; add new subjects there
    search = next((SEARCH_LINES[t.lower()] + "\n" for t in m["hashtags"] if t.lower() in SEARCH_LINES), "")
    return f"{search}{m['caption']} " + " ".join(m["hashtags"])


SERIES = "Thú cưng nhà bạn"


def youtube_title(m: dict) -> str:
    tail = f" | {SERIES} #Shorts" if "series" in (m.get("topic") or "").lower() else " #Shorts"  # same name on every series video
    return f"{m['title'][:100 - len(tail)].rstrip()}{tail}"  # YouTube titles stop at 100 characters


def _tags(m: dict, instead: str) -> str:
    return " ".join(instead if t == TIKTOK_ONLY else t for t in m["hashtags"])


def _sources(m: dict) -> str:
    return "\n\nNguồn:\n" + "\n".join(m["sources"]) if m.get("sources") else ""


def youtube_description(m: dict) -> str:
    return f"{m['caption']}{_sources(m)}\n\n{_tags(m, '#shorts')}"


def facebook_description(m: dict) -> str:
    return f"{m['caption']}{_sources(m)}\n\n{_tags(m, '#reels')}"


# ---------------------------------------------------------------- what to post


def pending(db, platforms, job_ids=None) -> list[tuple[ContentJob, str]]:
    """Approved, exported jobs and the platforms they are not yet published on, oldest first."""
    q = select(ContentJob).where(ContentJob.status == "EXPORTED").order_by(ContentJob.created_at)
    if job_ids:
        q = q.where(ContentJob.id.in_(job_ids))
    done = {(p.job_id, p.platform) for p in db.scalars(select(Publication))}
    return [(j, p) for j in db.scalars(q) for p in platforms if (j.id, p) not in done]


# ---------------------------------------------------------------- browser steps


def _type_caption(page, box, text: str, tags: list[str]) -> None:
    """TikTok's hashtag suggestions swallow a tag now and then: type each with a pause, then add back any missing."""
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.type(text + " ")
    for t in tags:
        page.keyboard.type(t)
        page.wait_for_timeout(1300)
        page.keyboard.type(" ")
    page.keyboard.press("Escape")
    for t in tags:
        if not re.search(re.escape(t) + r"\b", box.inner_text()):
            box.click()
            page.keyboard.press("Control+End")
            page.keyboard.type(t)
            page.wait_for_timeout(1500)
            page.keyboard.type(" ")
            page.keyboard.press("Escape")
    missing = [t for t in tags if not re.search(re.escape(t) + r"\b", box.inner_text())]
    if missing:
        raise Stop(f"TikTok dropped hashtags {missing}")


def post_tiktok(page, video: Path, m: dict, dry: bool) -> str | None:
    page.goto(URLS["tiktok"])
    if "/login" in page.url:
        raise Stop("TikTok is signed out: run --login")
    page.locator("input[type=file]").first.set_input_files(str(video), timeout=60_000)
    page.get_by_text(re.compile(r"Uploaded|Đã tải lên")).first.wait_for(timeout=300_000)
    _type_caption(page, page.locator("[contenteditable=true]").first, m["caption"], m["hashtags"])
    post = page.locator('button[data-e2e="post_video_button"]')
    if dry:
        return None
    post.click()
    page.wait_for_url("**/tiktokstudio/content**", timeout=120_000)
    return None  # TikTok shows the link only on the post itself; the content list has it once review ends


def post_youtube(page, video: Path, m: dict, dry: bool, channel: str) -> str | None:
    page.goto(f"https://studio.youtube.com/channel/{channel}" if channel else URLS["youtube_shorts"])  # a brand channel of the same Google account opens by id
    page.wait_for_url(re.compile(r"/channel/UC[\w-]+"), timeout=60_000)
    found = re.search(r"/channel/(UC[\w-]+)", page.url).group(1)
    if channel and found != channel:  # the personal channel was signed in once instead of the brand channel
        raise Stop(f"YouTube is on channel {found}, not {channel}: switch account in the --login window")
    page.goto(f"https://studio.youtube.com/channel/{found}/videos/upload?d=ud")
    page.locator("input[type=file]").first.set_input_files(str(video), timeout=60_000)
    dialog = page.locator("ytcp-uploads-dialog")
    boxes = dialog.locator("#textbox[contenteditable=true]")
    boxes.nth(0).wait_for(timeout=120_000)
    for i, text in ((0, youtube_title(m)), (1, youtube_description(m))):
        boxes.nth(i).click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.type(text)
    dialog.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').click()
    for _ in range(3):
        dialog.locator("#next-button").click()
        page.wait_for_timeout(1500)
    dialog.locator('tp-yt-paper-radio-button[name="PUBLIC"]').click()
    try:  # the copyright check runs while the video processes
        dialog.get_by_text(re.compile(r"No issues found|Không tìm thấy vấn đề")).wait_for(timeout=600_000)
    except Exception as e:  # noqa: BLE001 - left as a private draft for a person to look at
        raise Stop("YouTube checks did not come back clean: the video stays a private draft") from e
    if dry:
        return None
    dialog.locator("#done-button").click()
    link = page.get_by_text(re.compile(r"https://youtube\.com/shorts/[\w-]+")).first
    link.wait_for(timeout=120_000)
    return re.search(r"https://youtube\.com/shorts/[\w-]+", link.inner_text()).group(0)


def post_facebook(page, video: Path, m: dict, dry: bool) -> str | None:
    page.goto(URLS["facebook_reels"])
    page.locator("input[type=file]").first.set_input_files(str(video), timeout=60_000)
    for _ in range(2):
        nxt = page.get_by_role("button", name=re.compile(r"^(Next|Tiếp)$"))
        nxt.wait_for(timeout=180_000)
        nxt.click()
        page.wait_for_timeout(2500)
    if page.get_by_text(re.compile(r"Who can see your future posts|Ai có thể xem bài viết")).count():
        raise Stop("Facebook asks for the default audience of future posts: choose it once by hand, then run again")
    box = page.locator('[contenteditable=true][aria-label^="Describe your reel"], [contenteditable=true][aria-label^="Mô tả"]').first
    box.click()
    page.keyboard.type(facebook_description(m))
    page.keyboard.press("Escape")
    ai = page.get_by_role("switch", name=re.compile(r"AI")).first  # Meta asks for a label on realistic AI audio
    if ai.get_attribute("aria-checked") != "true":
        ai.click()
    if ai.get_attribute("aria-checked") != "true":
        raise Stop("could not switch on the Facebook AI label")
    post = page.get_by_role("button", name=re.compile(r"^(Post|Đăng)$"))
    page.wait_for_function("b => b.getAttribute('aria-disabled') !== 'true'", arg=post.element_handle(), timeout=300_000)
    if dry:
        return None
    post.click()
    page.get_by_text(re.compile(r"shared with|being processed|đã được chia sẻ|đang được xử lý")).first.wait_for(timeout=120_000)
    return None


# ---------------------------------------------------------------- run


def browser(s: Settings, pw, profile: str):
    """The installed Chrome when there is one: Google refuses sign-in in the bare test browser ("browser not secure")."""
    opts = {"headless": False, "viewport": {"width": 1280, "height": 900}, "ignore_default_args": ["--enable-automation"]}
    try:
        return pw.chromium.launch_persistent_context(str(s.storage_dir / profile), channel="chrome", **opts)
    except Exception:  # noqa: BLE001 - no Chrome installed: Playwright's own Chromium
        return pw.chromium.launch_persistent_context(str(s.storage_dir / profile), **opts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--login", action="store_true", help="open the three sites to sign in by hand")
    ap.add_argument("--dry-run", action="store_true", help="fill everything, stop before each Post button")
    ap.add_argument("--platforms", default=",".join(PLATFORMS))
    ap.add_argument("--job", action="append", help="only this job (repeatable)")
    ap.add_argument("--mark-posted", action="store_true", help="record these jobs as already posted by hand, post nothing")
    ap.add_argument("--profile", default="browser_profile", help="browser profile folder under storage/, one per set of accounts")
    args = ap.parse_args()
    s = Settings()
    platforms = [p for p in args.platforms.split(",") if p]
    if bad := set(platforms) - set(PLATFORMS):
        ap.error(f"unknown platforms {bad}; use {PLATFORMS}")
    if args.mark_posted:  # videos posted by hand before this script: never post them a second time
        if not args.job:
            ap.error("--mark-posted needs --job")
        with make_sessionmaker(s.database_url, create=False)() as db:
            for job, platform in pending(db, platforms, args.job):
                db.add(Publication(job_id=job.id, channel_id=job.channel_id, platform=platform, published_at=now(), notes="posted by hand"))
                print(f"marked {job.id} {platform}")
            db.commit()
        return 0

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = browser(s, pw, args.profile)
        if args.login:
            for p in PLATFORMS:
                ctx.new_page().goto(URLS[p])
            input("Sign in to TikTok, YouTube (the channel you post to) and Facebook in the window, then press Enter here... ")
            ctx.close()
            return 0
        sm = make_sessionmaker(s.database_url, create=False)
        with sm() as db:
            todo = pending(db, platforms, args.job)
            print(f"{len(todo)} post(s) to make" + (" (dry run)" if args.dry_run else ""))
            failed = 0
            for job, platform in todo:
                m = json.loads((s.storage_dir / "jobs" / job.id / "export" / "manifest.json").read_text(encoding="utf-8"))
                video = s.storage_dir / "jobs" / job.id / "export" / "video.mp4"
                page = ctx.new_page()
                ch = db.get(Channel, job.channel_id) if job.channel_id else None
                yt_channel = (json.loads(ch.handles_json).get("youtube") if ch else None) or s.youtube_channel_id
                try:
                    if platform == "tiktok":
                        url = post_tiktok(page, video, m, args.dry_run)
                    elif platform == "youtube_shorts":
                        url = post_youtube(page, video, m, args.dry_run, yt_channel)
                    else:
                        url = post_facebook(page, video, m, args.dry_run)
                except Exception as e:  # noqa: BLE001 - one failed post must not stop the others; nothing is recorded for it
                    failed += 1
                    print(f"FAILED {job.id} {platform}: {e}")
                    continue
                if args.dry_run:
                    print(f"ready  {job.id} {platform} (not posted)")
                    continue
                db.add(Publication(job_id=job.id, channel_id=job.channel_id, platform=platform, url=url, published_at=now(),
                                   notes="posted by scripts/publish.py"))
                db.commit()  # recorded at once: a crash later must not post this again
                print(f"posted {job.id} {platform} {url or ''}")
                page.close()
        if args.dry_run:
            input("Dry run done: look at the filled pages, then press Enter to close the browser... ")
        ctx.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
