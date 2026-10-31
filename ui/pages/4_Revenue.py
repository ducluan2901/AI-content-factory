"""Dashboard 5 (plan §13): affiliate products, clicks, money in and out, and what actually counts as earned.
Every figure is typed in from a statement, a dashboard or an invoice; nothing is guessed."""
import streamlit as st

from client import api, local

KINDS = ["affiliate", "service", "booking", "platform_ad", "cost"]
STATUSES = ["estimated", "pending", "approved", "paid", "rejected"]
NEXT = {"estimated": ["pending", "approved", "paid", "rejected"], "pending": ["approved", "paid", "rejected"], "approved": ["paid", "rejected"]}
st.set_page_config(page_title="Revenue", layout="wide")
st.title("Revenue and cost")
st.caption("Only approved and paid money counts as earned. Clicks are not orders. Amounts in different currencies are never added together.")

summary = api("GET", "/revenue/summary").json()
products = api("GET", "/affiliate/products").json()
records = api("GET", "/revenue").json()
pubs = api("GET", "/publications").json()

st.subheader("Where things stand")
if summary["currencies"]:
    st.dataframe([{"currency": c["currency"], "earned (approved + paid)": c["recognised_income"], "not yet earned (estimated + pending)": c["not_yet_earned_income"],
                   "operating costs": c["operating_costs"], "net earned": c["net_recognised"]} for c in summary["currencies"]], hide_index=True, use_container_width=True)
else:
    st.info("No money recorded yet.")
m1, m2, m3, m4 = st.columns(4)
m1.metric("LLM cost so far", f"${summary['llm_cost_usd']}")
m2.metric("LLM cost per exported video", "-" if summary["llm_cost_per_exported_video_usd"] is None else f"${summary['llm_cost_per_exported_video_usd']}")
m3.metric("Affiliate clicks", summary["clicks"])
m4.metric("Affiliate orders", summary["affiliate_orders_not_rejected"], help="Orders you recorded, excluding rejected ones")
if summary["combined"]:
    c = summary["combined"]
    st.info(f"At your configured rate of {c['usd_vnd_rate_used']:.0f} VND per USD: earned {c['recognised_income_vnd']:,} VND, costs {c['operating_costs_vnd_including_llm']:,} VND "
            f"(including LLM), net {c['net_vnd']:,} VND. {c['note']}.")
for note in summary["notes"]:
    st.caption(note)

with st.sidebar:
    st.subheader("Affiliate product")
    with st.form("product"):
        name = st.text_input("Name")
        program = st.text_input("Program / marketplace")
        code = st.text_input("Product code")
        link = st.text_input("Affiliate link")
        pct = st.number_input("Commission %", 0.0, 100.0, 0.0)
        if st.form_submit_button("Save product") and api("POST", "/affiliate/products", json={"name": name, "program": program or None, "code": code or None, "link": link or None, "commission_pct": pct or None}):
            st.rerun()
    st.subheader("Record money")
    with st.form("money"):
        kind = st.selectbox("Kind", KINDS)
        amount = st.number_input("Amount (positive; a cost is money out)", 0.0, step=1000.0)
        cur = st.selectbox("Currency", ["VND", "USD"])
        status = st.selectbox("Status", STATUSES, index=1)
        orders = st.number_input("Orders (affiliate only)", 0)
        prod = st.selectbox("Product (affiliate)", [None] + [p["id"] for p in products], format_func=lambda i: "-" if i is None else next(p["name"] for p in products if p["id"] == i))
        pub = st.selectbox("Post it came from", [None] + [p["id"] for p in pubs], format_func=lambda i: "-" if i is None else next(f"{p['title']} · {p['platform']}" for p in pubs if p["id"] == i))
        day = st.text_input("Date (YYYY-MM-DD, empty = today)")
        note = st.text_input("Note")
        if st.form_submit_button("Save") and api("POST", "/revenue", json={
                "kind": kind, "amount": amount, "currency": cur, "status": status, "orders": orders or None if kind == "affiliate" else None,
                "product_id": prod if kind == "affiliate" else None, "publication_id": pub, "occurred_on": day or None, "note": note or None}):
            st.rerun()
    st.subheader("Record clicks")
    with st.form("clicks"):
        cp = st.selectbox("Product", [p["id"] for p in products], format_func=lambda i: next(p["name"] for p in products if p["id"] == i)) if products else None
        cpub = st.selectbox("Post", [None] + [p["id"] for p in pubs], format_func=lambda i: "-" if i is None else next(f"{p['title']} · {p['platform']}" for p in pubs if p["id"] == i), key="click_pub")
        n = st.number_input("NEW clicks since your last entry", 0, help="Not the running total shown on the program's dashboard: entries are added together.")
        if st.form_submit_button("Save clicks") and cp and (r := api("POST", "/affiliate/clicks", json={"product_id": cp, "publication_id": cpub, "clicks": n})):
            st.sidebar.success(f"Saved. This product now totals {r.json()['product_total_clicks']} clicks; if that is more than the dashboard shows, you entered a running total.")

st.subheader("Affiliate products")
if products:
    st.dataframe([{k: p[k] for k in ("name", "program", "code", "commission_pct", "status", "clicks", "orders", "link")} for p in products], hide_index=True, use_container_width=True)
else:
    st.info("No products yet. Add one in the sidebar.")

st.subheader("Records")
if records:
    st.dataframe([{"date": r["occurred_on"], "kind": r["kind"], "status": r["status"], "amount": r["amount"], "currency": r["currency"], "orders": r["orders"], "note": r["note"], "id": r["id"]} for r in records],
                 hide_index=True, use_container_width=True)
    open_recs = [r for r in records if r["status"] in NEXT]
    if open_recs:
        c1, c2, c3 = st.columns([3, 2, 1])
        rid = c1.selectbox("Move a record forward", [r["id"] for r in open_recs], format_func=lambda i: next(f"{r['occurred_on']} {r['kind']} {r['amount']} {r['currency']} ({r['status']})" for r in open_recs if r["id"] == i))
        cur_status = next(r["status"] for r in open_recs if r["id"] == rid)
        to = c2.selectbox("To", NEXT[cur_status])
        if c3.button("Update") and api("PATCH", f"/revenue/{rid}", json={"status": to}):
            st.rerun()

st.subheader("Per video")
if summary["per_video"]:
    st.dataframe([{"video": v["title"], "platform": v["platform"], "format": v["format"], "LLM cost $ (this post's share)": v["llm_cost_usd"], "posts of this video": v["posts_of_this_video"], "earned": v["recognised_income"] or "-", "clicks": v["clicks"]} for v in summary["per_video"]],
                 hide_index=True, use_container_width=True)
else:
    st.info("No posts recorded yet (Analytics page).")
