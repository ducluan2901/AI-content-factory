"""Sprint 1: TrendScore formula, channels / trends / ideas API, idea -> job traceability. Offline, no pipeline run."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import Trend, make_sessionmaker
from app.main import create_app
from app import planner
from app.planner import FORMATS, WEIGHTS, default_duration, trend_score
from app.providers import FixtureLLM, FixtureTTS

FIX = Path(__file__).parent / "fixtures" / "llm"


def test_weights_sum_to_one_and_formula_matches_plan():
    assert round(sum(WEIGHTS.values()), 6) == 1.0
    r = trend_score({"velocity": 80, "retention_potential": 60, "channel_fit": 70, "monetization_potential": 40, "production_feasibility": 90})
    assert r["score"] == 68.0 and r["confidence"] == 1.0 and r["is_estimated"] is False
    assert sum(b["contribution"] for b in r["breakdown"]) == pytest.approx(68.0)


def test_missing_components_lower_confidence_not_score_signal():
    r = trend_score({"velocity": 100})
    assert r["is_estimated"] is True and r["confidence"] == 0.3
    assert r["score"] == 30 + 50 * 0.7  # unknown parts count as neutral 50
    assert [b["provided"] for b in r["breakdown"]] == [True, False, False, False, False]
    with pytest.raises(ValueError):
        trend_score({"velocity": 101})


def test_format_defaults_sit_inside_their_window():
    for fmt, spec in FORMATS.items():
        lo, hi = spec["window"]
        assert lo <= default_duration(fmt) <= hi and 15 <= lo and hi <= 90


def test_db_rejects_out_of_range_component(tmp_path):
    sm = make_sessionmaker(f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    with sm() as db:
        db.add(Trend(title="x", retention_potential=-1))
        with pytest.raises(IntegrityError):
            db.commit()


@pytest.fixture
def client(tmp_path):
    s = Settings(_env_file=None, app_env="test", llm_provider="fixture", tts_provider="fixture", storage_dir=tmp_path,
                 database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    app = create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS("ffmpeg"))
    with TestClient(app) as c:
        yield c


def test_trend_idea_job_flow(client):
    ch = client.post("/channels", json={"name": "Kênh A", "market": "vn", "language": "vi", "handles": {"tiktok": "@kenh_a"}})
    assert ch.status_code == 201, ch.text
    ch_id = ch.json()["id"]

    t = client.post("/trends", json={"title": "Mèo phản ứng với dưa chuột", "source": "manual", "source_url": "https://example.com/x",
                                     "format": "comedy", "velocity": 90, "channel_fit": 80})
    assert t.status_code == 201, t.text
    t = t.json()
    assert t["is_estimated"] is True and t["status"] == "new"
    assert len(t["breakdown"]) == 5 and t["breakdown"][0]["component"] == "velocity"

    assert client.patch(f"/trends/{t['id']}", json={"status": "reviewed", "retention_potential": 70, "monetization_potential": 30, "production_feasibility": 95}).json()["is_estimated"] is False
    assert client.patch(f"/trends/{t['id']}", json={"velocity": 500}).status_code == 422
    assert [x["id"] for x in client.get("/trends", params={"format": "comedy"}).json()] == [t["id"]]
    assert client.get("/trends", params={"format": "gadget"}).json() == []

    idea = client.post("/ideas", json={"channel_id": ch_id, "trend_id": t["id"], "title": "Mèo nhà tôi và quả dưa chuột", "format": "comedy",
                                       "hook": "Đừng bao giờ thử điều này với mèo của bạn", "viral_score": 75})
    assert idea.status_code == 201, idea.text
    idea = idea.json()
    assert idea["target_duration_s"] == default_duration("comedy") and idea["status"] == "backlog"
    assert client.post("/ideas", json={"channel_id": "ch_missing", "title": "no channel"}).status_code == 404
    assert client.patch(f"/ideas/{idea['id']}", json={"status": "selected", "premise": "Mèo nhảy dựng lên vì tưởng dưa chuột là rắn."}).json()["status"] == "selected"
    assert [x["id"] for x in client.get("/ideas", params={"channel_id": ch_id, "status": "selected"}).json()] == [idea["id"]]

    job = client.post(f"/ideas/{idea['id']}/jobs")
    assert job.status_code == 201, job.text
    job = job.json()
    assert job["idea_id"] == idea["id"] and job["channel_id"] == ch_id and job["format"] == "comedy"
    assert job["language"] == "vi" and job["target_duration_s"] == idea["target_duration_s"]
    assert "dưa chuột" in job["input_topic"] and "Hook idea" in job["input_topic"]
    assert client.get("/ideas", params={"status": "scripted"}).json()[0]["id"] == idea["id"]
    assert client.get(f"/trends/{t['id']}").json()["status"] == "selected"

    assert client.patch(f"/ideas/{idea['id']}", json={"status": "dropped"}).status_code == 200
    assert client.post(f"/ideas/{idea['id']}/jobs").status_code == 409


def test_job_without_duration_takes_format_default(client):
    job = client.post("/jobs", json={"topic": "Một chiếc đèn ngủ tự tắt khi bạn ngủ", "format": "gadget"}).json()
    assert job["target_duration_s"] == default_duration("gadget") and job["format"] == "gadget"
    assert client.post("/jobs", json={"topic": "quá ngắn", "target_duration_s": 10}).status_code == 422
    assert client.get("/formats").json()["comedy"]["window"] == [20, 35]


def test_migrate_fresh_db_and_stamp_legacy_db(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from app.db import migrate
    fresh = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    head = migrate(fresh)
    tables = inspect(create_engine(fresh)).get_table_names()
    assert {"channel", "trend", "content_idea", "content_job", "stage_run", "approval", "alembic_version"} <= set(tables)
    legacy = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    make_sessionmaker(legacy)  # create_all, as the week-1 code did
    assert migrate(legacy) == head
    with create_engine(legacy).connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar() == head


def test_files_endpoint_stays_inside_storage(client, tmp_path):
    (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
    (tmp_path.parent / "secret.env").write_text("ANTHROPIC_API_KEY=x", encoding="utf-8")
    assert client.get("/files/ok.txt").text == "fine"
    assert client.get("/files/..%2Fsecret.env").status_code == 404
    assert client.get(f"/files/{(tmp_path.parent / 'secret.env').as_posix()}").status_code == 404


def test_patch_null_on_required_field_is_422(client):
    ch = client.post("/channels", json={"name": "A"}).json()["id"]
    idea = client.post("/ideas", json={"channel_id": ch, "title": "Một ý tưởng"}).json()["id"]
    assert client.patch(f"/ideas/{idea}", json={"title": None}).status_code == 422
    assert client.patch(f"/ideas/{idea}", json={"status": None}).status_code == 422
    assert client.patch(f"/ideas/{idea}", json={"hook": None}).status_code == 200  # optional fields may be cleared
    tr = client.post("/trends", json={"title": "Một trend", "velocity": 50}).json()["id"]
    assert client.patch(f"/trends/{tr}", json={"status": None}).status_code == 422
    assert client.patch(f"/trends/{tr}", json={"velocity": None}).json()["is_estimated"] is True  # unknown is allowed


def test_ui_local_time_is_vietnam_time():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ui"))
    from client import local
    assert local("2026-10-31T01:00:00.000+00:00") == "2026-10-31 08:00"
    assert local("2026-10-31T23:30:00+00:00", minutes=False) == "2026-11-01"  # crosses midnight in Vietnam
    assert local(None) == ""


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>augsburg đấu với freiburg</title><ht:approx_traffic>200+</ht:approx_traffic><description/>
<ht:news_item><ht:news_item_title>Nhận định Augsburg vs Freiburg</ht:news_item_title><ht:news_item_url>https://x.vn/a</ht:news_item_url></ht:news_item></item>
<item><title>giá vàng hôm nay</title><ht:approx_traffic>20.000+</ht:approx_traffic></item>
</channel></rss>"""


def test_google_trends_feed_becomes_estimated_rows_and_imports_once(client, monkeypatch):
    rows = planner.parse_google_trends(RSS)
    assert [r["title"] for r in rows] == ["augsburg đấu với freiburg", "giá vàng hôm nay"]
    assert rows[0]["summary"] == "Nhận định Augsburg vs Freiburg" and rows[0]["source_url"] == "https://x.vn/a" and rows[0]["velocity"] == 46.0
    assert rows[1]["velocity"] == 86.0 and rows[1]["summary"] is None
    monkeypatch.setattr(planner, "fetch_google_trends", lambda geo: rows)
    assert client.post("/trends/import/google").json() == {"fetched": 2, "added": 2}
    assert client.post("/trends/import/google").json() == {"fetched": 2, "added": 0}  # same day again: nothing doubled
    got = [t for t in client.get("/trends").json() if t["source"] == "google_trends"]
    assert len(got) == 2 and all(t["is_estimated"] and t["market"] == "vn" and t["language"] == "vi" for t in got)
    monkeypatch.setattr(planner, "fetch_google_trends", lambda geo: (_ for _ in ()).throw(ValueError("down")))
    assert client.post("/trends/import/google").status_code == 502
