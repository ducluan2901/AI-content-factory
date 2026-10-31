"""Sprint 6: affiliate products, clicks, revenue and cost records, the honest summary, the disclosure rule. Offline."""
import pytest
from fastapi.testclient import TestClient

from app import revenue
from app.config import Settings
from app.db import ContentJob
from app.main import create_app
from app.providers import FixtureLLM, FixtureTTS
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "llm"


def make_client(tmp_path, **kw):
    s = Settings(_env_file=None, app_env="test", llm_provider="fixture", tts_provider="fixture", storage_dir=tmp_path,
                 database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}", **kw)
    return TestClient(create_app(s, llm=FixtureLLM(FIX / "script_vi.json"), tts=FixtureTTS("ffmpeg")))


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


def exported_job(client, cost=0.0, title="Video") -> str:
    with client.app.state.sm() as db:
        job = ContentJob(input_topic="t", title=title, format="gadget", status="EXPORTED", approval_id="apr_test", total_cost_usd=cost)
        db.add(job)
        db.commit()
        return job.id


def product(client, **kw) -> str:
    r = client.post("/affiliate/products", json={"name": "Đèn ngủ cảm biến", "program": "ShopeeAffiliate", "commission_pct": 8, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_status_transitions_are_one_way_and_final_states_are_final():
    revenue.check_transition("estimated", "pending")
    revenue.check_transition("pending", "approved")
    revenue.check_transition("approved", "paid")
    revenue.check_transition("pending", "rejected")
    for old, new in (("approved", "pending"), ("paid", "approved"), ("rejected", "pending"), ("paid", "rejected")):
        with pytest.raises(revenue.RevenueError):
            revenue.check_transition(old, new)


def test_a_post_with_an_affiliate_link_needs_a_disclosure(client):
    job_id, prod = exported_job(client), product(client)
    body = {"platform": "tiktok", "affiliate_product_id": prod}
    assert client.post(f"/jobs/{job_id}/publications", json=body).status_code == 422
    assert client.post(f"/jobs/{job_id}/publications", json=body | {"affiliate_product_id": "prod_missing", "disclosure_confirmed": True}).status_code == 404
    ok = client.post(f"/jobs/{job_id}/publications", json=body | {"disclosure_confirmed": True})
    assert ok.status_code == 201 and ok.json()["affiliate_product_id"] == prod and ok.json()["disclosure_confirmed"] is True
    assert client.post(f"/jobs/{job_id}/publications", json={"platform": "youtube_shorts"}).status_code == 201  # no link, no disclosure needed


def test_revenue_validation_and_record_rules(client):
    assert client.post("/revenue", json={"kind": "affiliate", "amount": -5}).status_code == 422
    assert client.post("/revenue", json={"kind": "lottery", "amount": 5}).status_code == 422
    assert client.post("/revenue", json={"kind": "service", "amount": 5, "currency": "EUR"}).status_code == 422
    assert client.post("/revenue", json={"kind": "service", "amount": 5, "occurred_on": "31/10/2026"}).status_code == 422
    assert client.post("/revenue", json={"kind": "service", "amount": 5, "orders": 2}).status_code == 422  # orders are an affiliate notion
    assert client.post("/revenue", json={"kind": "affiliate", "amount": 5, "product_id": "prod_missing"}).status_code == 404
    rec = client.post("/revenue", json={"kind": "affiliate", "amount": 120000, "orders": 3, "product_id": product(client)})
    assert rec.status_code == 201 and rec.json()["status"] == "pending" and rec.json()["occurred_on"]


def test_status_moves_forward_only_and_fixed_amounts_stay_fixed(client):
    rid = client.post("/revenue", json={"kind": "booking", "amount": 1000000, "status": "pending"}).json()["id"]
    assert client.patch(f"/revenue/{rid}", json={"amount": 1500000}).status_code == 200  # still open: may be corrected
    assert client.patch(f"/revenue/{rid}", json={"status": "approved"}).status_code == 200
    assert client.patch(f"/revenue/{rid}", json={"amount": 9}).status_code == 409  # approved amounts are fixed
    assert client.patch(f"/revenue/{rid}", json={"status": "pending"}).status_code == 409
    assert client.patch(f"/revenue/{rid}", json={"status": None}).status_code == 422
    assert client.patch(f"/revenue/{rid}", json={"status": "paid"}).status_code == 200
    assert client.patch(f"/revenue/{rid}", json={"status": "rejected"}).status_code == 409


def test_summary_counts_only_approved_and_paid_and_never_mixes_currencies(client):
    for kind, amount, cur, status in (("affiliate", 100, "VND", "pending"), ("affiliate", 200, "VND", "approved"), ("service", 500, "VND", "paid"),
                                      ("affiliate", 7, "USD", "approved"), ("affiliate", 999, "VND", "rejected"), ("cost", 150, "VND", "paid"),
                                      ("platform_ad", 50, "VND", "estimated")):
        client.post("/revenue", json={"kind": kind, "amount": amount, "currency": cur, "status": status})
    s = client.get("/revenue/summary").json()
    vnd, usd = (next(c for c in s["currencies"] if c["currency"] == cur) for cur in ("VND", "USD"))
    assert vnd["recognised_income"] == 700 and vnd["not_yet_earned_income"] == 150  # approved 200 + paid 500; pending 100 + estimated 50
    assert vnd["operating_costs"] == 150 and vnd["net_recognised"] == 550
    assert usd["recognised_income"] == 7 and usd["net_recognised"] == 7
    assert s["combined"] is None  # no exchange rate configured: nothing is converted


def test_a_configured_rate_adds_a_labelled_combined_figure_without_hiding_the_originals(tmp_path):
    with make_client(tmp_path, usd_vnd_rate=25000.0) as c:
        c.post("/revenue", json={"kind": "service", "amount": 1000000, "status": "paid"})
        c.post("/revenue", json={"kind": "affiliate", "amount": 2, "currency": "USD", "status": "approved"})
        exported_job(c, cost=0.04)
        s = c.get("/revenue/summary").json()
        assert [x["currency"] for x in s["currencies"]] == ["USD", "VND"]
        comb = s["combined"]
        assert comb["usd_vnd_rate_used"] == 25000.0 and comb["recognised_income_vnd"] == 1050000
        assert comb["operating_costs_vnd_including_llm"] == 1000  # the 0.04 USD of LLM cost, converted
        assert "per-currency" in comb["note"]


def test_clicks_are_not_orders_and_cost_per_video_uses_recorded_llm_cost(client):
    a, b = exported_job(client, cost=0.03, title="A"), exported_job(client, cost=0.05, title="B")
    prod = product(client)
    pub = client.post(f"/jobs/{a}/publications", json={"platform": "tiktok", "affiliate_product_id": prod, "disclosure_confirmed": True}).json()["id"]
    client.post("/affiliate/clicks", json={"product_id": prod, "publication_id": pub, "clicks": 120})
    s = client.get("/revenue/summary").json()
    assert s["clicks"] == 120 and s["affiliate_orders_not_rejected"] == 0 and s["orders_per_click"] is None  # clicks alone prove no order
    assert s["exported_videos"] == 2 and s["llm_cost_usd"] == 0.08 and s["llm_cost_per_exported_video_usd"] == 0.04
    client.post("/revenue", json={"kind": "affiliate", "amount": 90000, "orders": 3, "product_id": prod, "publication_id": pub, "status": "approved"})
    s = client.get("/revenue/summary").json()
    assert s["orders_per_click"] == round(3 / 120, 4)
    row = next(v for v in s["per_video"] if v["publication_id"] == pub)
    assert row["clicks"] == 120 and row["recognised_income"] == {"VND": 90000.0} and row["llm_cost_usd"] == 0.03
    listed = next(p for p in client.get("/affiliate/products").json() if p["id"] == prod)
    assert listed["clicks"] == 120 and listed["orders"] == 3


def test_negative_clicks_and_unknown_references_are_refused(client):
    prod = product(client)
    assert client.post("/affiliate/clicks", json={"product_id": prod, "clicks": -1}).status_code == 422
    assert client.post("/affiliate/clicks", json={"product_id": "prod_missing", "clicks": 1}).status_code == 404
    assert client.post("/affiliate/clicks", json={"product_id": prod, "clicks": 1, "publication_id": "pub_missing"}).status_code == 404
    assert client.patch(f"/affiliate/products/{prod}", json={"status": "paused"}).json()["status"] == "paused"
    assert client.patch(f"/affiliate/products/{prod}", json={"name": None}).status_code == 422
