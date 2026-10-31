"""Manual post tracking and the plan's video classification (30-day plan §4.2-4.3).

Every number comes from a person copying it off a platform. Nothing is scraped, estimated or filled in.
"commercial winner" needs click and order data, which arrives with the affiliate module (Sprint 6); it is not
computed here and no video is labelled with it.
"""
from datetime import datetime, timedelta, timezone
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import MILESTONES, ContentJob, Publication, VideoMetric

MILESTONE_AGE = {"24h": timedelta(hours=24), "72h": timedelta(hours=72), "7d": timedelta(days=7)}
COMPLETION_TARGET = 25.0  # % of viewers who finish (plan §4.2, for 20-35 s videos)
ENGAGEMENT_TARGET = 0.03  # (likes + comments + shares) / views (plan §4.2)
BASELINE_SIZE = 10  # plan §4.3: the median of the last 10 videos of the same channel
MIN_BASELINE = 3  # fewer comparable videos than this cannot support the word "winner"


def parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def engagement_rate(m: VideoMetric) -> float | None:
    return (m.likes + m.comments + m.shares) / m.views if m.views else None


def classify(db: Session, pub: Publication, m: VideoMetric) -> dict:
    """winner | promising | average | loser for one video at one milestone, with the numbers behind the label.

    The baseline is the other videos on the same channel AND platform at the same milestone (views are not
    comparable across platforms, plan §4.2), most recent first, up to BASELINE_SIZE.
    """
    rows = db.execute(
        select(VideoMetric.views).join(Publication, Publication.id == VideoMetric.publication_id)
        .where(VideoMetric.milestone == m.milestone, Publication.id != pub.id, Publication.platform == pub.platform,
               Publication.channel_id == pub.channel_id)
        .order_by(Publication.published_at.desc()).limit(BASELINE_SIZE)
    ).all()
    views = [r[0] for r in rows]
    med = median(views) if views else None
    rate = engagement_rate(m)
    retention_ok = m.completion_pct is not None and m.completion_pct >= COMPLETION_TARGET
    engagement_ok = rate is not None and rate >= ENGAGEMENT_TARGET
    good = retention_ok or engagement_ok
    enough = len(views) >= MIN_BASELINE
    if enough and m.views > med and good:
        label, why = "winner", "views above the median of recent videos, and retention or engagement is on target"
    elif not good:
        label, why = "loser", "neither completion nor engagement reaches the target"
    elif retention_ok and not (enough and m.views > med):
        label, why = "promising", "retention is on target but views are not above the median" if enough else \
            "retention is on target; too few comparable videos to compare views"
    else:
        label, why = "average", "engagement is on target but views are not above the median" if enough else \
            "engagement is on target; too few comparable videos to compare views"
    return {"label": label, "reason": why, "baseline_n": len(views), "baseline_median_views": med,
            "engagement_rate": None if rate is None else round(rate, 4), "retention_ok": retention_ok, "engagement_ok": engagement_ok}


def due_milestones(db: Session, now: datetime | None = None) -> list[dict]:
    """Milestones whose time has come and that still have no numbers, oldest first."""
    now = now or datetime.now(timezone.utc)
    have = {(r[0], r[1]) for r in db.execute(select(VideoMetric.publication_id, VideoMetric.milestone))}
    out = []
    for pub in db.scalars(select(Publication)):
        title = db.get(ContentJob, pub.job_id).title
        for ms in MILESTONES:
            due_at = parse_ts(pub.published_at) + MILESTONE_AGE[ms]
            if now >= due_at and (pub.id, ms) not in have:
                out.append({"publication_id": pub.id, "job_id": pub.job_id, "title": title, "platform": pub.platform, "milestone": ms,
                            "due_at": due_at.isoformat(timespec="minutes"), "overdue_hours": round((now - due_at).total_seconds() / 3600, 1)})
    return sorted(out, key=lambda d: d["due_at"])


def format_summary(db: Session, milestone: str = "7d", channel_id: str | None = None) -> list[dict]:
    """Per format: how many videos have numbers at this milestone and how they did. This is the 'which format has
    a signal' view the plan asks for; with few videos the figures are small-sample and are shown with their count."""
    stmt = (select(ContentJob.format, VideoMetric, Publication).join(Publication, Publication.job_id == ContentJob.id)
            .join(VideoMetric, VideoMetric.publication_id == Publication.id).where(VideoMetric.milestone == milestone))
    if channel_id:
        stmt = stmt.where(Publication.channel_id == channel_id)
    groups: dict[str, list[tuple[VideoMetric, Publication]]] = {}
    for fmt, m, pub in db.execute(stmt):
        groups.setdefault(fmt, []).append((m, pub))
    out = []
    for fmt, items in sorted(groups.items()):
        labels = [classify(db, pub, m)["label"] for m, pub in items]
        comps = [m.completion_pct for m, _ in items if m.completion_pct is not None]
        rates = [r for m, _ in items if (r := engagement_rate(m)) is not None]
        out.append({"format": fmt, "videos": len(items), "median_views": median(m.views for m, _ in items),
                    "mean_completion_pct": round(mean(comps), 1) if comps else None,
                    "mean_engagement_rate": round(mean(rates), 4) if rates else None,
                    "winners": labels.count("winner"), "promising": labels.count("promising"),
                    "average": labels.count("average"), "losers": labels.count("loser")})
    return out
