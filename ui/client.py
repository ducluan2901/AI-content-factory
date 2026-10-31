"""Shared by every console page: the API call wrapper and local-time display. The console only talks HTTP."""
import os
from datetime import datetime, timedelta, timezone

import httpx
import streamlit as st

API = os.environ.get("FACTORY_API", "http://127.0.0.1:8000")
VN = timezone(timedelta(hours=7))  # Asia/Ho_Chi_Minh has no DST; avoids the tzdata package Windows needs for zoneinfo


def api(method: str, path: str, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", timeout=60, **kw)
    except httpx.HTTPError as e:
        st.error(f"API not reachable at {API}: {e}. Start it with scripts\\run_api.ps1")
        st.stop()
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        st.error(f"{r.status_code}: {detail}")
        return None
    return r


def local(ts: str | None, minutes: bool = True) -> str:
    """UTC ISO-8601 from the database -> Vietnam time for display (DATABASE.md §1.6)."""
    if not ts:
        return ""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VN).strftime("%Y-%m-%d %H:%M" if minutes else "%Y-%m-%d")
