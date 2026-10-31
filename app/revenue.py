"""Affiliate, revenue and cost reporting (30-day plan §3, §8, §11, §13 dashboard 5).

Everything comes from rows a person typed in from a statement or an invoice, plus the LLM cost the pipeline
recorded per job. Rules that keep the report honest:
  * Amounts are never converted between currencies unless USD_VND_RATE is set, and then the converted figure is
    shown next to, never instead of, the per-currency totals.
  * An estimated or pending amount is never counted as earned: only approved and paid are "recognised".
  * Clicks are not orders, and click-to-order is shown only when both numbers exist.
"""
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import AffiliateClick, AffiliateProduct, ContentJob, Publication, RevenueRecord

RECOGNISED = ("approved", "paid")
ORDER = {"estimated": 0, "pending": 1, "approved": 2, "paid": 3}


class RevenueError(Exception):
    pass


def check_transition(old: str, new: str) -> None:
    """estimated -> pending -> approved -> paid, one way; any open state can end as rejected. Paid and rejected are final."""
    if old == new:
        return
    if old in ("paid", "rejected"):
        raise RevenueError(f"a {old} record is final; add a new record instead")
    if new == "rejected":
        return
    if ORDER[new] < ORDER[old]:
        raise RevenueError(f"status cannot move backwards from {old} to {new}")


def summary(db: Session, usd_vnd_rate: float | None = None) -> dict:
    # ---- money by currency
    by_cur: dict[str, dict] = {}
    for r in db.scalars(select(RevenueRecord)):
        c = by_cur.setdefault(r.currency, {"currency": r.currency, "income": defaultdict(lambda: defaultdict(float)), "costs": defaultdict(float)})
        if r.kind == "cost":
            c["costs"][r.status] += r.amount
        else:
            c["income"][r.kind][r.status] += r.amount

    # ---- LLM cost the pipeline recorded, always USD and always shown as its own line
    llm_usd = db.scalar(select(func.coalesce(func.sum(ContentJob.total_cost_usd), 0.0))) or 0.0
    exported = db.scalar(select(func.count()).select_from(ContentJob).where(ContentJob.status == "EXPORTED")) or 0

    currencies = []
    for cur, c in sorted(by_cur.items()):
        income = {k: {s: round(v, 2) for s, v in sorted(d.items())} for k, d in sorted(c["income"].items())}
        recognised = round(sum(d.get(s, 0.0) for d in c["income"].values() for s in RECOGNISED), 2)
        pending = round(sum(d.get(s, 0.0) for d in c["income"].values() for s in ("estimated", "pending")), 2)
        costs = round(sum(v for s, v in c["costs"].items() if s != "rejected"), 2)
        currencies.append({"currency": cur, "income_by_kind_and_status": income, "recognised_income": recognised,
                           "not_yet_earned_income": pending, "operating_costs": costs,
                           "net_recognised": round(recognised - costs, 2)})

    combined = None
    if usd_vnd_rate:
        def to_vnd(cur: str, amount: float) -> float:
            return amount * (usd_vnd_rate if cur == "USD" else 1.0)
        rec = sum(to_vnd(c["currency"], c["recognised_income"]) for c in currencies)
        cost = sum(to_vnd(c["currency"], c["operating_costs"]) for c in currencies) + llm_usd * usd_vnd_rate
        combined = {"vnd": True, "usd_vnd_rate_used": usd_vnd_rate, "recognised_income_vnd": round(rec),
                    "operating_costs_vnd_including_llm": round(cost), "net_vnd": round(rec - cost),
                    "note": "converted at the single rate you configured; the per-currency figures above are the ones to trust"}

    # ---- clicks and orders
    clicks = db.scalar(select(func.coalesce(func.sum(AffiliateClick.clicks), 0))) or 0
    orders = db.scalar(select(func.coalesce(func.sum(RevenueRecord.orders), 0)).where(RevenueRecord.kind == "affiliate", RevenueRecord.status != "rejected")) or 0

    # ---- per video: income attributed to its post, against its own LLM cost
    videos = []
    for pub in db.scalars(select(Publication)):
        job = db.get(ContentJob, pub.job_id)
        inc = {}
        for r in db.scalars(select(RevenueRecord).where(RevenueRecord.publication_id == pub.id, RevenueRecord.kind != "cost", RevenueRecord.status.in_(RECOGNISED))):
            inc[r.currency] = round(inc.get(r.currency, 0.0) + r.amount, 2)
        vclicks = db.scalar(select(func.coalesce(func.sum(AffiliateClick.clicks), 0)).where(AffiliateClick.publication_id == pub.id)) or 0
        videos.append({"publication_id": pub.id, "title": job.title, "platform": pub.platform, "format": job.format,
                       "llm_cost_usd": round(job.total_cost_usd, 4), "recognised_income": inc, "clicks": vclicks})

    return {
        "currencies": currencies,
        "llm_cost_usd": round(llm_usd, 4),
        "exported_videos": exported,
        # production cost per exported video (LLM only; manual costs you typed in are in operating_costs)
        "llm_cost_per_exported_video_usd": round(llm_usd / exported, 4) if exported else None,
        "combined": combined,
        "clicks": clicks,
        "affiliate_orders_not_rejected": orders,
        "orders_per_click": round(orders / clicks, 4) if clicks and orders else None,
        "per_video": videos,
        "notes": ["recognised = approved or paid; estimated and pending are shown as not yet earned",
                  "clicks are not orders; orders_per_click needs both numbers",
                  "the monthly subscription is not part of any per-video cost unless you enter it as a cost record"],
    }


def product_list(db: Session) -> list[dict]:
    out = []
    for p in db.scalars(select(AffiliateProduct).order_by(AffiliateProduct.created_at.desc())):
        clicks = db.scalar(select(func.coalesce(func.sum(AffiliateClick.clicks), 0)).where(AffiliateClick.product_id == p.id)) or 0
        orders = db.scalar(select(func.coalesce(func.sum(RevenueRecord.orders), 0)).where(RevenueRecord.product_id == p.id, RevenueRecord.kind == "affiliate", RevenueRecord.status != "rejected")) or 0
        out.append({c.name: getattr(p, c.name) for c in p.__table__.columns} | {"clicks": clicks, "orders": orders})
    return out
