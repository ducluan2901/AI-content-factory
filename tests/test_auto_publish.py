import json
from datetime import datetime, timedelta

import pytest

from scripts.auto_publish import VN, handoff, plan, quota_day_start, read_queue

AT = datetime(2026, 11, 8, 16, 0, tzinfo=VN)


def slot(day, hm):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=VN)


def test_queue_is_sorted_and_strict():
    q = read_queue("# comment\n2026-11-10 19:30,job_b\n\n2026-11-09 11:30,job_a  # dog\n")
    assert [j for _, j in q] == ["job_a", "job_b"] and q[0][0] == slot(9, "11:30")
    with pytest.raises(ValueError, match="line 1"):
        read_queue("tomorrow,job_x")


def test_quota_day_starts_at_midnight_pacific():
    assert quota_day_start(AT).astimezone(VN) == datetime(2026, 11, 8, 15, 0, tzinfo=VN)  # PST: 15:00 VN
    assert quota_day_start(datetime(2026, 11, 8, 14, 0, tzinfo=VN)).astimezone(VN) == datetime(2026, 11, 7, 15, 0, tzinfo=VN)


def test_plan_sends_in_slot_order_within_quota_and_holds_the_rest():
    q = [(slot(9, "11:30"), "a"), (slot(9, "19:30"), "b"), (slot(10, "11:30"), "c"), (slot(10, "19:30"), "d"),
         (slot(8, "15:20"), "late"), (slot(11, "11:30"), "draft"), (slot(9, "07:00"), "done")]
    jobs = {k: ("EXPORTED", "x", "vi") for k in "abcd"} | {"late": ("EXPORTED", "", "vi"), "draft": ("AWAITING_APPROVAL", "", "vi"), "done": ("EXPORTED", "", "vi")}
    send, held = plan(sorted(q), jobs, published={"done"}, used_today=1, at=AT)
    assert send == [(slot(9, "11:30"), "a"), (slot(9, "19:30"), "b"), (slot(10, "11:30"), "c")]  # 4 - 1 used = 3
    why = {j: r for _, j, r in held}
    assert "passed" in why["late"] and "not ready" in why["draft"] and "quota" in why["d"] and "done" not in why


def test_plan_holds_a_teaser_until_tomorrows_video_is_approved():
    q = [(slot(9, "19:30"), "p1"), (slot(10, "19:30"), "p2"), (slot(9, "11:30"), "m")]
    jobs = {"p1": ("EXPORTED", "Mèo nhà bạn ngủ kiểu nào? Phần 2 mai lên, theo dõi để không lỡ.", "vi"), "p2": ("AWAITING_APPROVAL", "Phần 2", "vi"), "m": ("EXPORTED", "Chó nhà bạn có làm vậy không? Mai: lưỡi mèo.", "vi")}
    send, held = plan(sorted(q), jobs, set(), 0, AT)
    assert send == [] and {j for _, j, _ in held} == {"p1", "p2", "m"}
    jobs["p2"] = ("EXPORTED", "Phần 2", "vi")
    assert (slot(9, "19:30"), "p1") in plan(sorted(q), jobs, set(), 0, AT)[0]
    jobs["p2"] = ("EXPORTED", "Phần 2", "en")  # another channel's video in that slot does not count as the follow-up
    assert (slot(9, "19:30"), "p1") not in plan(sorted(q), jobs, set(), 0, AT)[0]
    jobs["p2"] = ("render/final.mp4 is newer than the export", "Phần 2", "vi")  # follow-up approved but not sendable: hold the teaser
    assert (slot(9, "19:30"), "p1") not in plan(sorted(q), jobs, set(), 0, AT)[0]


def test_plan_never_sends_one_job_twice():
    q = read_queue("2026-11-09 19:30,a\n2026-11-10 19:30,a")
    send, held = plan(q, {"a": ("EXPORTED", "x", "vi")}, set(), 0, AT)
    assert send == [(slot(9, "19:30"), "a")] and "queued twice" in held[0][2]


def test_handoff_copies_once(tmp_path):
    export, dang = tmp_path / "export", tmp_path / "dang_bai"
    export.mkdir()
    dang.mkdir()
    (export / "video.mp4").write_bytes(b"v")
    (dang / "16_mui_cho.mp4").write_bytes(b"x")
    m = {"title": "Đuôi mèo nhà bạn nói gì", "caption": "Mèo vẫy đuôi?", "hashtags": ["#suthatthuvi", "#meo", "#viral"], "sources": ["Số 1: ICC"]}
    out = handoff(dang, "job_z", export, m, "https://youtube.com/shorts/abc", slot(9, "19:30"))
    assert out.name == "17_duoi_meo_nha_ban.mp4"
    text = (dang / "17_duoi_meo_nha_ban_caption.txt").read_text(encoding="utf-8-sig")
    assert "Sự thật thú vị về mèo" in text and "job: job_z" in text and "09/11 19:30" in text
    assert handoff(dang, "job_z", export, m, "u", slot(9, "19:30")) is None


def test_build_batch_reads_topics_and_skips_comments():
    from scripts.build_batch import topics_from
    assert topics_from("a\n\n# note\n b # keeps a hash\n") == ["a", "b # keeps a hash"]


def test_qa_fix_swaps_only_the_flagged_scene(tmp_path):
    from types import SimpleNamespace

    from scripts.qa_fix import swap_scene
    s = SimpleNamespace(storage_dir=tmp_path)
    stock = SimpleNamespace(name="pixabay", licence="pixabay", licence_url="u", attribution=lambda c: f"by {c.author}")
    new = SimpleNamespace(path=tmp_path / "stock_cache" / "pixabay-v9.mp4", source_id="9", source_url="p", author="Ann", duration_s=10.0)
    shot = {"kind": "stock_video", "source_id": "1", "path": "a", "clip_offset_s": 0.5}
    assets = [shot | {"scene_index": 0, "start_ms": 0, "end_ms": 2000}, shot | {"scene_index": 1, "start_ms": 2000, "end_ms": 4500, "source_id": "131824"},
              shot | {"scene_index": 1, "start_ms": 4500, "end_ms": 7000, "source_id": "131824"}, {"kind": "title_card", "scene_index": 1}]
    out = swap_scene(assets, 1, [new], stock, s)
    assert out[0] == assets[0] and out[3] == assets[3]  # other scenes and cards untouched
    assert [a["source_id"] for a in out[1:3]] == ["9", "9"] and [a["start_ms"] for a in out[1:3]] == [2000, 4500]
    assert out[1]["path"] == "stock_cache/pixabay-v9.mp4" and out[2]["clip_offset_s"] == 3.0  # second pass plays a later part


def test_qa_fix_drops_an_overused_clip_for_the_scenes_other_clips():
    from scripts.qa_fix import drop_overused
    a = {"kind": "stock_video", "source_provider": "pixabay", "source_url": "u", "author": "x", "licence": "pixabay", "licence_url": "l",
         "attribution_text": "t", "clip_offset_s": 0.5}
    assets = [a | {"scene_index": 1, "source_id": "131824", "path": "chow"}, a | {"scene_index": 1, "source_id": "5", "path": "five"},
              a | {"scene_index": 2, "source_id": "131824", "path": "chow"}]
    out = drop_overused(assets, 1)
    assert out[0]["source_id"] == "5" and out[0]["clip_offset_s"] == 2.5 and out[2]["source_id"] == "131824"  # scene 2 untouched
    assert drop_overused(assets, 2) is None  # no other clip to fall back on
