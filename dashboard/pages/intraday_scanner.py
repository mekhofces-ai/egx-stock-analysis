from __future__ import annotations

from datetime import datetime

import streamlit as st

from app.database import SessionLocal
from app.services.learning_system import run_intraday_rescan
from dashboard.pages.learning_common import page_header, show_df
from dashboard.ui_components import success_box


def render() -> None:
    _day, payload = page_header("Intraday Scanner", "intraday_scanner_date")
    if st.button("Run intraday scan now", type="primary"):
        with SessionLocal() as db:
            result = run_intraday_rescan(db, scan_type="manual_dashboard", scan_time=datetime.utcnow(), persist=True)
            db.commit()
        success_box(f"Scan saved. Events detected: {len(result.get('items', []))}.")
        show_df(result.get("items"), "New Scan Events")
    show_df(payload.get("intraday_scan"), "Latest Learning Scan Events")

