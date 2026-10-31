"""Dashboard 2 (plan §13): channels, the idea backlog, and the jobs each idea produced."""
import os

import httpx
import streamlit as st

API = os.environ.get("FACTORY_API", "http://127.0.0.1:8000")
IDEA_STATUSES = ["backlog", "selected", "scripted", "dropped"]
st.set_page_config(page_title="Content Planner", layout="wide")


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
channels = api("GET", "/channels").json()
st.title("Content Planner")

with st.sidebar:
    st.subheader("New channel")
    with st.form("channel"):
        name = st.text_input("Name")
        market = st.selectbox("Market", ["vn", "global"])
        language = st.selectbox("Language", ["vi", "en"])
        positioning = st.text_area("Positioning", height=60)
        tiktok = st.text_input("TikTok handle")
        if st.form_submit_button("Create channel") and api("POST", "/channels", json={"name": name, "market": market, "language": language, "positioning": positioning or None, "handles": {"tiktok": tiktok} if tiktok else {}}):
            st.success("Created")
            st.rerun()
    st.divider()
    st.subheader("New idea")
    with st.form("idea"):
        ch = st.selectbox("Channel", [c["id"] for c in channels], format_func=lambda i: next(c["name"] for c in channels if c["id"] == i)) if channels else None
        title = st.text_input("Title")
        fmt = st.selectbox("Format", list(formats), format_func=lambda k: formats[k]["label"])
        hook = st.text_input("Hook")
        premise = st.text_area("Premise / story", height=80)
        product = st.text_area("Product facts (gadget format only)", height=60)
        dur = st.number_input("Target length (s), 0 = format default", 0, 90, 0)
        viral = st.slider("Viral score", 0, 100, 50)
        revenue = st.slider("Revenue score", 0, 100, 30)
        if st.form_submit_button("Save idea"):
            if not ch:
                st.error("Create a channel first")
            elif api("POST", "/ideas", json={"channel_id": ch, "title": title, "format": fmt, "hook": hook or None, "premise": premise or None,
                                             "product_desc": product or None, "target_duration_s": dur or None, "viral_score": viral, "revenue_score": revenue}):
                st.success("Saved")

st.subheader("Channels")
if channels:
    st.dataframe([{k: c[k] for k in ("id", "name", "market", "language", "status", "positioning", "is_sample")} for c in channels], hide_index=True, use_container_width=True)
else:
    st.info("No channels yet. Create one in the sidebar.")

st.subheader("Idea backlog")
c1, c2, c3 = st.columns(3)
f_status = c1.multiselect("Status", IDEA_STATUSES, default=["backlog", "selected", "scripted"])
f_channel = c2.selectbox("Channel", ["all"] + [c["id"] for c in channels], format_func=lambda i: "all channels" if i == "all" else next(c["name"] for c in channels if c["id"] == i))
f_format = c3.selectbox("Format", ["all"] + list(formats), format_func=lambda k: "all formats" if k == "all" else formats[k]["label"])
params = {k: v for k, v in (("channel_id", f_channel), ("format", f_format)) if v != "all"}
ideas = [i for i in api("GET", "/ideas", params=params).json() if i["status"] in f_status]
name_of = {c["id"]: c["name"] for c in channels}
if ideas:
    st.dataframe([{"id": i["id"], "status": i["status"], "format": i["format"], "channel": name_of.get(i["channel_id"], "?"), "title": i["title"],
                   "len s": i["target_duration_s"], "viral": i["viral_score"], "revenue": i["revenue_score"], "trend": i["trend_id"], "sample": i["is_sample"]} for i in ideas],
                 hide_index=True, use_container_width=True)
    sel = st.selectbox("Edit idea", [i["id"] for i in ideas], format_func=lambda x: next(i["title"] for i in ideas if i["id"] == x))
    idea = next(i for i in ideas if i["id"] == sel)
    e1, e2 = st.columns([2, 1])
    with e1:
        new_title = st.text_input("Title", idea["title"], key="e_title")
        new_hook = st.text_input("Hook", idea["hook"] or "", key="e_hook")
        new_premise = st.text_area("Premise", idea["premise"] or "", key="e_premise", height=80)
        new_notes = st.text_area("Notes", idea["notes"] or "", key="e_notes", height=60)
    with e2:
        new_status = st.selectbox("Status", IDEA_STATUSES, index=IDEA_STATUSES.index(idea["status"]), key="e_status")
        new_dur = st.number_input("Target length (s)", 15, 90, idea["target_duration_s"], key="e_dur")
        new_viral = st.number_input("Viral score", 0, 100, int(idea["viral_score"] or 0), key="e_viral")
        new_rev = st.number_input("Revenue score", 0, 100, int(idea["revenue_score"] or 0), key="e_rev")
    b1, b2 = st.columns(2)
    if b1.button("Save changes") and api("PATCH", f"/ideas/{sel}", json={"title": new_title, "hook": new_hook or None, "premise": new_premise or None, "notes": new_notes or None,
                                                                         "status": new_status, "target_duration_s": new_dur, "viral_score": new_viral, "revenue_score": new_rev}):
        st.success("Saved")
        st.rerun()
    if b2.button("Create video job from this idea", type="primary", disabled=idea["status"] == "dropped"):
        if r := api("POST", f"/ideas/{sel}/jobs"):
            st.success(f"Job {r.json()['id']} queued; follow it on the Home page")
else:
    st.info("No ideas match. Add one in the sidebar or from the Trend Radar page.")

st.subheader("Jobs by channel (content calendar, minimal)")
jobs = api("GET", "/jobs").json()
if jobs:
    st.dataframe([{"created": j["created_at"][:16], "channel": name_of.get(j["channel_id"], "-"), "format": j["format"], "status": j["status"], "stage": j["current_stage"],
                   "title": j["title"], "len s": j["target_duration_s"], "idea": j["idea_id"], "cost $": round(j["total_cost_usd"], 4)} for j in jobs], hide_index=True, use_container_width=True)
