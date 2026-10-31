"""Dashboard 1 (plan §13): trends, their scores with the component breakdown, and review status."""
import os

import httpx
import streamlit as st

API = os.environ.get("FACTORY_API", "http://127.0.0.1:8000")
STATUSES = ["new", "reviewed", "selected", "rejected"]
COMPONENTS = ["velocity", "retention_potential", "channel_fit", "monetization_potential", "production_feasibility"]
st.set_page_config(page_title="Trend Radar", layout="wide")


def api(method, path, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", timeout=30, **kw)
    except httpx.HTTPError as e:
        st.error(f"API not reachable at {API}: {e}")
        st.stop()
    if r.status_code >= 400:
        st.error(f"{r.status_code}: {r.json().get('detail', r.text)}")
        return None
    return r


formats = api("GET", "/formats").json()
st.title("Trend Radar")

with st.sidebar:
    st.subheader("Add a trend")
    with st.form("trend"):
        title = st.text_input("Title")
        summary = st.text_area("What is trending and why", height=80)
        source = st.text_input("Source (site or 'manual')", "manual")
        source_url = st.text_input("Source URL")
        market = st.selectbox("Market", ["vn", "global"])
        language = st.selectbox("Language", ["vi", "en"])
        fmt = st.selectbox("Format", list(formats), format_func=lambda k: formats[k]["label"])
        st.caption("Scores 0-100. Leave a slider at -1 when you have no evidence; the score then marks it as estimated.")
        comps = {c: st.slider(c.replace("_", " "), -1, 100, -1) for c in COMPONENTS}
        notes = st.text_area("Notes / evidence", height=60)
        if st.form_submit_button("Save trend"):
            body = {"title": title, "summary": summary or None, "source": source or "manual", "source_url": source_url or None,
                    "market": market, "language": language, "format": fmt, "notes": notes or None} | {c: v for c, v in comps.items() if v >= 0}
            if api("POST", "/trends", json=body):
                st.success("Saved")

c1, c2, c3 = st.columns(3)
f_status = c1.multiselect("Status", STATUSES, default=["new", "reviewed", "selected"])
f_market = c2.selectbox("Market", ["all", "vn", "global"])
f_format = c3.selectbox("Format", ["all"] + list(formats), format_func=lambda k: "all formats" if k == "all" else formats[k]["label"])
params = {k: v for k, v in (("market", f_market), ("format", f_format)) if v != "all"}
trends = [t for t in api("GET", "/trends", params=params).json() if t["status"] in f_status]

if not trends:
    st.info("No trends match. Add one in the sidebar, or run scripts/seed_demo.py for sample data (clearly marked as samples).")
    st.stop()

st.dataframe(
    [{"id": t["id"], "score": t["trend_score"], "conf": t["confidence"], "est": t["is_estimated"], "status": t["status"], "format": t["format"],
      "market": t["market"], "title": t["title"], "source": t["source"], "sample": t["is_sample"], "observed": (t["observed_at"] or "")[:10]} for t in trends],
    hide_index=True, use_container_width=True)

sel = st.selectbox("Inspect", [t["id"] for t in trends], format_func=lambda i: next(t["title"] for t in trends if t["id"] == i))
t = next(x for x in trends if x["id"] == sel)
if t["is_sample"]:
    st.warning("Sample data: scores are illustrative, not measured.")
left, right = st.columns([2, 1])
with left:
    st.subheader(t["title"])
    st.write(t["summary"] or "")
    if t["source_url"]:
        st.write(f"Source: {t['source']} · {t['source_url']} · observed {t['observed_at'][:16]}")
    st.markdown(f"**TrendScore {t['trend_score']}** · confidence {t['confidence']} · {'estimated' if t['is_estimated'] else 'all components provided'}")
    st.dataframe(t["breakdown"], hide_index=True, use_container_width=True)
    if t["notes"]:
        st.caption(t["notes"])
with right:
    st.subheader("Review")
    new_status = st.selectbox("Status", STATUSES, index=STATUSES.index(t["status"]))
    new_comps = {c: st.number_input(c.replace("_", " "), 0, 100, int(t[c]) if t[c] is not None else 50, key=f"n_{c}") for c in COMPONENTS}
    if st.button("Save review") and api("PATCH", f"/trends/{sel}", json={"status": new_status} | new_comps):
        st.success("Updated")
        st.rerun()
    st.divider()
    st.subheader("Turn into an idea")
    channels = api("GET", "/channels").json()
    if not channels:
        st.info("Create a channel first (Content Planner page).")
    else:
        ch = st.selectbox("Channel", [c["id"] for c in channels], format_func=lambda i: next(c["name"] for c in channels if c["id"] == i))
        idea_title = st.text_input("Idea title", t["title"])
        hook = st.text_input("Hook")
        if st.button("Create idea") and api("POST", "/ideas", json={"channel_id": ch, "trend_id": sel, "title": idea_title, "format": t["format"], "hook": hook or None}):
            st.success("Idea created; see Content Planner")
