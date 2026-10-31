"""Trend Radar and Content Planner: formats, the TrendScore formula, and idea -> job creation (plan §7.3-7.4)."""
import math
import re
import xml.etree.ElementTree as ET

import httpx
from sqlalchemy.orm import Session

from app.db import SCORE_COMPONENTS, Channel, ContentIdea, ContentJob, Trend

# code -> (Vietnamese label, duration window in s). The script guidance per format lives in app/prompts/format_<code>.md
FORMATS = {
    "comedy": {"label": "Hài tình huống / relatable", "window": (20, 35)},
    "facts": {"label": "Chuyện lạ / bí ẩn / sự thật thú vị", "window": (30, 45)},
    "gadget": {"label": "Sản phẩm độc lạ / gadget", "window": (25, 40)},
}

WEIGHTS = {"velocity": 0.30, "retention_potential": 0.25, "channel_fit": 0.20, "monetization_potential": 0.15, "production_feasibility": 0.10}
NEUTRAL = 50.0  # a missing component counts as neutral and lowers confidence; it never fakes a signal


def default_duration(fmt: str) -> int:
    lo, hi = FORMATS[fmt]["window"]
    return (lo + hi) // 2


def trend_score(components: dict[str, float | None]) -> dict:
    """Weighted 0-100 score with a per-component breakdown the UI can show (plan §7.4)."""
    breakdown, score, known = [], 0.0, 0.0
    for name in SCORE_COMPONENTS:
        value, w = components.get(name), WEIGHTS[name]
        provided = value is not None
        v = float(value) if provided else NEUTRAL
        if not 0 <= v <= 100:
            raise ValueError(f"{name} must be between 0 and 100")
        score += w * v
        known += w if provided else 0
        breakdown.append({"component": name, "weight": w, "value": v, "contribution": round(w * v, 2), "provided": provided})
    return {"score": round(score, 1), "confidence": round(known, 2), "is_estimated": known < 1, "breakdown": breakdown}


def rescore(trend: Trend) -> None:
    r = trend_score({c: getattr(trend, c) for c in SCORE_COMPONENTS})
    trend.trend_score, trend.confidence, trend.is_estimated = r["score"], r["confidence"], r["is_estimated"]


GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo={geo}"


def parse_google_trends(xml_text: str) -> list[dict]:
    """Google's Daily Search Trends RSS -> trend rows. approx_traffic ("200+", "20.000+") becomes velocity on a log
    scale (1K -> 60, 20K -> 86, 100K+ -> 100); the other components stay unknown, so the row is marked estimated."""
    rows = []
    for item in ET.fromstring(xml_text).iter("item"):
        def text(tag):  # the ht: namespace varies by region; match on the local tag name
            return next((e.text or "" for e in item.iter() if e.tag.split("}")[-1] == tag), "").strip()
        n = int(re.sub(r"\D", "", text("approx_traffic")) or 0)
        rows.append({"title": text("title"), "summary": text("news_item_title") or None, "source": "google_trends",
                     "source_url": text("news_item_url") or None, "velocity": round(min(100.0, 20 * math.log10(n)), 1) if n >= 10 else None})
    return rows


def fetch_google_trends(geo: str) -> list[dict]:
    try:
        return parse_google_trends(httpx.get(GOOGLE_TRENDS_RSS.format(geo=geo), timeout=20).raise_for_status().text)
    except (httpx.HTTPError, ET.ParseError) as e:
        raise ValueError(f"Google Trends not readable: {e}") from e


def job_from_idea(db: Session, idea: ContentIdea, profile: str) -> ContentJob:
    """Turn a planned idea into a pipeline job; the idea and its trend keep the link for traceability."""
    channel = db.get(Channel, idea.channel_id)
    if channel is None:
        raise ValueError("idea has no channel")
    topic = idea.title if not idea.premise else f"{idea.title}. {idea.premise}"
    if idea.hook:
        topic += f" Hook idea: {idea.hook}"
    job = ContentJob(
        title=idea.title[:120], channel_id=channel.id, idea_id=idea.id, format=idea.format,
        input_kind="product" if idea.product_desc else "topic", input_topic=topic, input_product_desc=idea.product_desc,
        language=channel.language, target_duration_s=idea.target_duration_s, profile=profile, status="QUEUED",
    )
    db.add(job)
    idea.status = "scripted"
    if idea.trend_id and (trend := db.get(Trend, idea.trend_id)) and trend.status in ("new", "reviewed"):
        trend.status = "selected"
    db.commit()
    return job
