"""scripts/publish.py without a browser: the texts each platform gets, and which approved videos are still to post."""
import importlib.util
from pathlib import Path

from app.db import Approval, ContentJob, Publication, make_sessionmaker

spec = importlib.util.spec_from_file_location("publish", Path(__file__).parent.parent / "scripts" / "publish.py")
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)

M = {"title": "3 điều con chó nhà bạn đang nói với bạn, số 3 nằm ngay trên mặt nó", "caption": "Chó nhà bạn hay xin gì?",
     "hashtags": ["#suthatthuvi", "#khamphathegioi", "#learnontiktok", "#cho", "#viral"],
     "sources": ["Số 1: American Kennel Club", "Số 2: National Geographic"]}


def test_each_platform_gets_its_own_tags_and_the_sources():
    assert publish.tiktok_caption(M) == "Sự thật thú vị về chó – chó nhà bạn\nChó nhà bạn hay xin gì? #suthatthuvi #khamphathegioi #learnontiktok #cho #viral"
    assert publish.youtube_description(M) == ("Chó nhà bạn hay xin gì?\n\nNguồn:\nSố 1: American Kennel Club\nSố 2: National Geographic"
                                             "\n\n#suthatthuvi #khamphathegioi #shorts #cho #viral")
    assert publish.facebook_description(M).endswith("#suthatthuvi #khamphathegioi #reels #cho #viral")
    assert publish.facebook_description(M | {"sources": []}) == "Chó nhà bạn hay xin gì?\n\n#suthatthuvi #khamphathegioi #reels #cho #viral"
    long = publish.youtube_title(M | {"title": "x" * 120})
    assert len(long) <= 100 and long.endswith(" #Shorts")


def test_only_exported_jobs_are_posted_and_each_platform_once(tmp_path):
    sm = make_sessionmaker(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    with sm() as db:
        db.add_all([ContentJob(id="j_new", input_topic="t", status="AWAITING_APPROVAL"),
                    ContentJob(id="j_ok", input_topic="t", status="EXPORTED", approval_id="apr_ok"),
                    ContentJob(id="j_two", input_topic="t", status="EXPORTED", approval_id="apr_two")])
        db.commit()
        db.add_all([Approval(id="apr_ok", job_id="j_ok", decision="approved", approved_by="founder"),
                    Approval(id="apr_two", job_id="j_two", decision="approved", approved_by="founder")])
        db.commit()
        db.add(Publication(job_id="j_ok", platform="tiktok", published_at="2026-11-03T00:00:00Z"))
        db.commit()
        todo = {(j.id, p) for j, p in publish.pending(db, publish.PLATFORMS)}
        assert todo == {("j_ok", "youtube_shorts"), ("j_ok", "facebook_reels"),
                        ("j_two", "tiktok"), ("j_two", "youtube_shorts"), ("j_two", "facebook_reels")}  # never the unapproved one
        assert {(j.id, p) for j, p in publish.pending(db, ["tiktok"], ["j_ok", "j_two"])} == {("j_two", "tiktok")}


def test_youtube_title_names_the_series_only_for_series_topics():
    series = publish.youtube_title({"title": "x" * 120, "topic": "Tai chó. Series Thú cưng nhà bạn đang giấu gì. Mai: lưỡi mèo"})
    assert series.endswith(" | Thú cưng nhà bạn #Shorts") and len(series) <= 100
    assert publish.youtube_title({"title": "Mắt bạn", "topic": "mắt"}) == "Mắt bạn #Shorts"


def test_tiktok_caption_has_no_search_line_for_unknown_subjects():
    assert publish.tiktok_caption({"caption": "Cá voi hát.", "hashtags": ["#cavoi", "#viral"]}) == "Cá voi hát. #cavoi #viral"
