"""Dashboard 4 (plan §13): record where an exported video was posted by hand, enter its numbers at 24h / 72h / 7d,
see how each video classifies against recent ones, and which format has a signal. Nothing here is scraped or estimated."""
import streamlit as st

from client import api, local

PLATFORMS = ["tiktok", "youtube_shorts", "facebook_reels"]
MILESTONES = ["24h", "72h", "7d"]
LABEL = {"winner": "WINNER", "promising": "promising", "average": "average", "loser": "loser"}
st.set_page_config(page_title="Analytics", layout="wide")
st.title("Analytics")
st.caption("The system never posts for you. Upload by hand, record it here, then copy the platform's numbers in at each milestone.")

jobs = [j for j in api("GET", "/jobs").json() if j["status"] == "EXPORTED"]
pubs = api("GET", "/publications").json()

due = api("GET", "/analytics/due").json()
if due:
    st.warning(f"{len(due)} milestone(s) waiting for numbers")
    st.dataframe([{"video": d["title"], "platform": d["platform"], "milestone": d["milestone"], "was due": local(d["due_at"]), "overdue h": d["overdue_hours"]} for d in due],
                 hide_index=True, use_container_width=True)

with st.sidebar:
    st.subheader("Record a post")
    if not jobs:
        st.info("No exported videos yet. A video must be approved and exported before it can be recorded as published.")
    else:
        with st.form("pub"):
            job_id = st.selectbox("Exported video", [j["id"] for j in jobs], format_func=lambda i: next(j["title"] for j in jobs if j["id"] == i))
            platform = st.selectbox("Platform", PLATFORMS)
            url = st.text_input("Post URL")
            when = st.text_input("Went live (UTC, ISO-8601; empty = now)")
            if st.form_submit_button("Save") and api("POST", f"/jobs/{job_id}/publications", json={"platform": platform, "url": url or None, "published_at": when or None}):
                st.success("Recorded")
                st.rerun()

if not pubs:
    st.info("Nothing recorded yet.")
    st.stop()

st.subheader("Videos")
rows = []
for p in pubs:
    row = {"video": p["title"], "platform": p["platform"], "live": local(p["published_at"])}
    for ms in MILESTONES:
        m = p["metrics"].get(ms)
        row[ms] = f"{m['views']} views · {LABEL[m['classification']['label']]}" if m else "-"
    rows.append(row)
st.dataframe(rows, hide_index=True, use_container_width=True)

st.subheader("Enter numbers")
sel = st.selectbox("Post", [p["id"] for p in pubs], format_func=lambda i: next(f"{p['title']} · {p['platform']}" for p in pubs if p["id"] == i))
pub = next(p for p in pubs if p["id"] == sel)
ms = st.selectbox("Milestone", MILESTONES)
cur = pub["metrics"].get(ms, {})
with st.form("metrics"):
    c1, c2, c3 = st.columns(3)
    views = c1.number_input("Views", 0, value=int(cur.get("views", 0)))
    watch = c2.number_input("Average watch time (s), 0 = unknown", 0.0, value=float(cur.get("avg_watch_s") or 0.0))
    comp = c3.number_input("Completion % (watched to the end), -1 = unknown", -1.0, 100.0, float(cur["completion_pct"]) if cur.get("completion_pct") is not None else -1.0)
    d1, d2, d3, d4, d5 = st.columns(5)
    likes, comments, shares, saves, follows = (d1.number_input("Likes", 0, value=int(cur.get("likes", 0))), d2.number_input("Comments", 0, value=int(cur.get("comments", 0))),
                                               d3.number_input("Shares", 0, value=int(cur.get("shares", 0))), d4.number_input("Saves", 0, value=int(cur.get("saves", 0))),
                                               d5.number_input("New followers", 0, value=int(cur.get("follows", 0))))
    if st.form_submit_button("Save numbers") and api("PUT", f"/publications/{sel}/metrics/{ms}", json={
            "views": views, "avg_watch_s": watch or None, "completion_pct": None if comp < 0 else comp,
            "likes": likes, "comments": comments, "shares": shares, "saves": saves, "follows": follows}):
        st.success("Saved")
        st.rerun()

if pub["metrics"]:
    st.subheader("How this video did")
    for k, m in pub["metrics"].items():
        c = m["classification"]
        rate = "-" if c["engagement_rate"] is None else f"{c['engagement_rate'] * 100:.1f}%"
        st.markdown(f"**{k}: {LABEL[c['label']]}** — {c['reason']}. Engagement {rate}; compared with {c['baseline_n']} recent video(s)"
                    + (f", median {c['baseline_median_views']:.0f} views." if c["baseline_median_views"] is not None else "."))
    st.caption("Winner = views above the median of the last 10 videos on this channel and platform, with completion >= 25% or engagement >= 3%. "
               "Fewer than 3 comparable videos can never be called a winner. 'Commercial winner' needs click and order data (affiliate module, later).")

st.subheader("Which format has a signal")
ms2 = st.selectbox("Milestone", MILESTONES, index=2, key="fmt_ms")
fmts = api("GET", "/analytics/formats", params={"milestone": ms2}).json()
if fmts:
    st.dataframe(fmts, hide_index=True, use_container_width=True)
    st.caption("Small samples: judge a format by the 'videos' column first.")
else:
    st.info(f"No numbers entered at {ms2} yet.")
