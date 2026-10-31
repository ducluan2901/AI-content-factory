"""Operator console. HTTP client of the API only: it never touches the database or the pipeline."""
import os

import httpx
import streamlit as st

API = os.environ.get("FACTORY_API", "http://127.0.0.1:8000")
st.set_page_config(page_title="AI Content Factory", layout="wide")


def api(method: str, path: str, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", timeout=60, **kw)
    except httpx.HTTPError as e:
        st.error(f"API not reachable at {API}: {e}. Start it with scripts\\run_api.ps1")
        st.stop()
    if r.status_code >= 400:
        st.error(f"{r.status_code}: {r.json().get('detail', r.text)}")
        return None
    return r


st.title("AI Content Factory")

with st.sidebar:
    st.subheader("New video")
    with st.form("new"):
        topic = st.text_area("Topic", "Tại sao AI agent sẽ thay đổi cách làm việc năm 2026")
        product = st.text_area("Product description (optional)", "")
        lang = st.selectbox("Language", ["vi", "en"])
        dur = st.slider("Target length (s)", 45, 60, 55)
        if st.form_submit_button("Submit") and (r := api("POST", "/jobs", json={"topic": topic, "product_desc": product or None, "language": lang, "target_duration_s": dur})):
            st.session_state.job = r.json()["id"]
            st.success("Queued")
    health = api("GET", "/health").json()
    st.caption(f"API {'ok' if health['ok'] else 'NOT ready'} · env {health['app_env']} · LLM {health['llm_provider']} · TTS {health['tts_provider']}")
    if not health["ok"]:
        st.json(health["checks"])

jobs = api("GET", "/jobs").json()
if not jobs:
    st.info("No jobs yet. Submit a topic from the sidebar.")
    st.stop()

st.subheader("Queue")
st.dataframe([{"id": j["id"], "status": j["status"], "stage": j["current_stage"], "title": j["title"], "cost $": round(j["total_cost_usd"], 4), "created": j["created_at"][:19]} for j in jobs], hide_index=True, use_container_width=True)

ids = [j["id"] for j in jobs]
default = ids.index(st.session_state["job"]) if st.session_state.get("job") in ids else 0
job_id = st.selectbox("Inspect job", ids, index=default, format_func=lambda i: f"{i} · {next(j['status'] for j in jobs if j['id'] == i)}")
st.button("Refresh")
d = api("GET", f"/jobs/{job_id}").json()
job, out = d["job"], d["outputs"]

c1, c2 = st.columns([1, 2])
with c1:
    st.metric("Status", job["status"])
    if job["error_summary"]:
        st.error(job["error_summary"])
    if "RENDER" in out:
        st.video(api("GET", f"/files/{out['RENDER']['video_path']}").content)
        st.caption(f"{out['RENDER']['width']}x{out['RENDER']['height']} · {out['RENDER']['duration_s']}s · {out['RENDER']['video_codec']}/{out['RENDER']['audio_codec']}")

with c2:
    if job["status"] == "AWAITING_APPROVAL":
        st.subheader("Approval")
        warns = [c for c in out["QUALITY"]["checks"] if not c["passed"]]
        for c in warns:
            st.warning(f"{c['rule']}: {c['message']} (expected {c['expected']}, got {c['actual']})")
        name = st.text_input("Your name", key="by")
        notes = st.text_area("Notes", key="notes")
        ack = st.checkbox(f"I reviewed the video and the {len(warns)} warning(s) above", disabled=not warns) if warns else True
        a, b = st.columns(2)
        if a.button("Approve and export", type="primary", disabled=not (name and ack)):
            if r := api("POST", f"/jobs/{job_id}/approve", json={"approved_by": name, "notes": notes}):
                st.success(f"Exported to {r.json()['export']['zip_path']}")
        if b.button("Reject", disabled=not name):
            if api("POST", f"/jobs/{job_id}/reject", json={"approved_by": name, "notes": notes}):
                st.info("Rejected")
    if job["status"] in ("FAILED", "NEEDS_REVISION", "REJECTED"):
        stages = ["SCRIPT", "VOICE", "VISUALS", "SUBTITLES", "RENDER", "QUALITY"]
        frm = st.selectbox("Re-run from stage", ([None] if job["status"] == "FAILED" else []) + stages, format_func=lambda x: "resume where it failed" if x is None else x)
        if st.button("Re-run") and api("POST", f"/jobs/{job_id}/run", json={"from_stage": frm}):
            st.success("Re-queued")
    if job["status"] == "EXPORTED":
        st.success("Exported. The bundle is in storage/jobs/<id>/export and storage/jobs/<id>.zip")

    if "SCRIPT" in out:
        sc = out["SCRIPT"]
        st.subheader(sc["title"])
        st.write(sc["caption"])
        st.write(" ".join(sc["hashtags"]))
        st.caption(f"{len(sc['scenes'])} scenes · {sc['syllable_count']} syllables · estimated {sc['estimated_duration_s']}s")
        for i, scene in enumerate(sc["scenes"]):
            st.markdown(f"**{i + 1}. {scene['on_screen_text']}**  \n{scene['narration']}")
    if "QUALITY" in out:
        st.subheader("Quality checks")
        st.dataframe(out["QUALITY"]["checks"], hide_index=True, use_container_width=True)
    if "VISUALS" in out:
        st.subheader("Assets and licences")
        st.dataframe([{k: a[k] for k in ("order_index", "kind", "source_provider", "licence", "font", "path")} for a in out["VISUALS"]["assets"]], hide_index=True, use_container_width=True)

st.subheader("Stage runs (audit trail)")
st.dataframe([{k: r[k] for k in ("stage", "attempt", "status", "provider", "model", "duration_ms", "cost_usd", "superseded_by", "error_type", "error_message")} for r in d["runs"]], hide_index=True, use_container_width=True)
if d["approvals"]:
    st.subheader("Approvals")
    st.dataframe(d["approvals"], hide_index=True, use_container_width=True)
