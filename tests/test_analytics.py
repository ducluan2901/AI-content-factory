"""Sprint 5: manual publication records, milestone metrics, classification, due list, per-format summary. Offline."""
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import analytics
from app.config import Settings
from app.db import Channel, ContentJob, Publication, VideoMetric, make_sessionmaker
from app.main import create_app
from app.providers import FixtureLLM, FixtureTTS

FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe")
FIX = Path(__file__).parent / "fixtures" / "llm"
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="FFmpeg not found")


@pytest.fixture
def db(tmp_path):
    sm = make_sessionmaker(f"sqlite:///{(tmp_path / 'a.db').as_posix()}")
    with sm() as session:
        yield session


def add_pub(db, channel, fmt="comedy", platform="tiktok", views=1000, completion=30.0, likes=50, comments=5, shares=5,
            milestone="7d", published=None, job_status="EXPORTED"):
    job = ContentJob(input_topic="t", title=f"{fmt} video", format=fmt, status=job_status, channel_id=channel.id,
                     approval_id="apr_test" if job_status == "EXPORTED" else None)
    db.add(job)
    db.flush()
    pub = Publication(job_id=job.id, channel_id=channel.id, platform=platform, published_at=(published or datetime.now(timezone.utc)).isoformat())
    db.add(pub)
    db.flush()
    m = None
    if views is not None:
        m = VideoMetric(publication_id=pub.id, milestone=milestone, views=views, completion_pct=completion, likes=likes, comments=comments, shares=shares)
        db.add(m)
    db.commit()
    return pub, m


def test_classification_labels_and_reasons(db):
    ch = Channel(name="A")
    db.add(ch)
    db.commit()
    for v in (1000, 2000, 3000, 4000, 5000):  # baseline: median 3000
        add_pub(db, ch, views=v, completion=10, likes=1, comments=0, shares=0)
    cases = {
        "winner": dict(views=9000, completion=40, likes=10),
        "promising": dict(views=500, completion=40, likes=1, comments=0, shares=0),
        "loser": dict(views=9000, completion=10, likes=1, comments=0, shares=0),  # lots of views, nobody stays or reacts
        "average": dict(views=500, completion=10, likes=400, comments=50, shares=50),  # engaged, but below the median
    }
    for expected, kw in cases.items():
        pub, m = add_pub(db, ch, **kw)
        c = analytics.classify(db, pub, m)
        assert c["label"] == expected, (expected, c)
        assert c["baseline_n"] >= 5 and c["baseline_median_views"] >= 1000


def test_too_few_comparable_videos_never_produces_a_winner(db):
    ch = Channel(name="A")
    db.add(ch)
    db.commit()
    add_pub(db, ch, views=100)
    pub, m = add_pub(db, ch, views=999999, completion=90)
    c = analytics.classify(db, pub, m)
    assert c["baseline_n"] == 1 and c["label"] == "promising" and "too few" in c["reason"]


def test_baseline_ignores_other_platforms_other_channels_and_itself(db):
    a, b = Channel(name="A"), Channel(name="B")
    db.add_all([a, b])
    db.commit()
    for _ in range(4):
        add_pub(db, a, platform="youtube_shorts", views=10)  # same channel, other platform
        add_pub(db, b, platform="tiktok", views=10)  # other channel
    pub, m = add_pub(db, a, platform="tiktok", views=500)
    assert analytics.classify(db, pub, m)["baseline_n"] == 0  # not even itself


def test_due_milestones_lists_only_elapsed_and_missing_ones(db):
    ch = Channel(name="A")
    db.add(ch)
    db.commit()
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    add_pub(db, ch, views=None, published=now - timedelta(hours=30))  # 24h is due, 72h and 7d are not
    pub2, _ = add_pub(db, ch, views=5, milestone="24h", published=now - timedelta(days=8))  # 24h done; 72h and 7d are due
    due = analytics.due_milestones(db, now)
    assert sorted((d["milestone"], d["publication_id"] == pub2.id) for d in due) == [("24h", False), ("72h", True), ("7d", True)]
    assert all(d["overdue_hours"] > 0 for d in due) and [d["due_at"] for d in due] == sorted(d["due_at"] for d in due)


def test_format_summary_groups_by_format(db):
    ch = Channel(name="A")
    db.add(ch)
    db.commit()
    for v in (100, 300):
        add_pub(db, ch, fmt="comedy", views=v, completion=30)
    add_pub(db, ch, fmt="gadget", views=50, completion=10, likes=0, comments=0, shares=0)
    rows = {r["format"]: r for r in analytics.format_summary(db, "7d")}
    assert rows["comedy"]["videos"] == 2 and rows["comedy"]["median_views"] == 200 and rows["comedy"]["mean_completion_pct"] == 30
    assert rows["gadget"]["videos"] == 1 and rows["gadget"]["losers"] == 1
    assert analytics.format_summary(db, "24h") == []  # no numbers entered at that milestone


# ---------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path):
    s = Settings(_env_file=None, app_env="test", llm_provider="fixture", tts_provider="fixture", storage_dir=tmp_path,
                 ffmpeg_path=FFMPEG or "ffmpeg", ffprobe_path=FFPROBE or "ffprobe", database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    with TestClient(create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS(FFMPEG or "ffmpeg"))) as c:
        yield c


def exported_job(client, status="EXPORTED") -> str:
    with client.app.state.sm() as db:
        job = ContentJob(input_topic="t", title="Video", format="facts", status=status, approval_id="apr_test" if status == "EXPORTED" else None)
        db.add(job)
        db.commit()
        return job.id


def test_only_an_exported_video_can_be_recorded_as_published(client):
    assert client.post(f"/jobs/{exported_job(client, 'AWAITING_APPROVAL')}/publications", json={"platform": "tiktok"}).status_code == 409
    job_id = exported_job(client)
    assert client.post(f"/jobs/{job_id}/publications", json={"platform": "myspace"}).status_code == 422
    assert client.post(f"/jobs/{job_id}/publications", json={"platform": "tiktok", "published_at": "not a date"}).status_code == 422
    r = client.post(f"/jobs/{job_id}/publications", json={"platform": "tiktok", "url": "https://tiktok.com/@x/video/1"})
    assert r.status_code == 201 and r.json()["metrics"] == {}


def test_metrics_validate_and_a_second_entry_corrects_the_first(client):
    pub = client.post(f"/jobs/{exported_job(client)}/publications", json={"platform": "youtube_shorts"}).json()["id"]
    url = f"/publications/{pub}/metrics"
    assert client.put(f"{url}/48h", json={"views": 5}).status_code == 422
    assert client.put(f"{url}/24h", json={"views": 5, "completion_pct": 120}).status_code == 422
    assert client.put(f"{url}/24h", json={"views": -1}).status_code == 422
    assert client.put(f"{url}/24h", json={"views": 100, "completion_pct": 30, "likes": 4}).status_code == 200
    r = client.put(f"{url}/24h", json={"views": 150, "completion_pct": 33, "likes": 9})  # correction, not a duplicate
    assert r.status_code == 200 and r.json()["metrics"]["24h"]["views"] == 150
    got = client.get("/publications").json()
    assert len(got) == 1 and list(got[0]["metrics"]) == ["24h"] and got[0]["metrics"]["24h"]["classification"]["label"] in ("promising", "loser", "average")
    assert client.put("/publications/pub_missing/metrics/24h", json={"views": 1}).status_code == 404


def test_due_and_formats_endpoints(client):
    job_id = exported_job(client)
    long_ago = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    pub = client.post(f"/jobs/{job_id}/publications", json={"platform": "tiktok", "published_at": long_ago}).json()["id"]
    assert [d["milestone"] for d in client.get("/analytics/due").json()] == ["24h", "72h", "7d"]
    client.put(f"/publications/{pub}/metrics/7d", json={"views": 1000, "completion_pct": 28})
    assert [d["milestone"] for d in client.get("/analytics/due").json()] == ["24h", "72h"]
    assert client.get("/analytics/formats", params={"milestone": "7d"}).json()[0]["format"] == "facts"
    assert client.get("/analytics/formats", params={"milestone": "1y"}).status_code == 422


@needs_ffmpeg
def test_a_real_pipeline_export_can_then_be_recorded(client):
    job_id = client.post("/jobs", json={"topic": "x y z", "review_script": False, "target_duration_s": 55}).json()["id"]
    end = time.time() + 90
    while client.get(f"/jobs/{job_id}").json()["job"]["status"] in ("QUEUED", "RUNNING") and time.time() < end:
        time.sleep(0.3)
    assert client.post(f"/jobs/{job_id}/publications", json={"platform": "tiktok"}).status_code == 409  # awaiting approval: no
    assert client.post(f"/jobs/{job_id}/approve", json={"approved_by": "founder"}).status_code == 200
    assert client.post(f"/jobs/{job_id}/publications", json={"platform": "tiktok"}).status_code == 201
