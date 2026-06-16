from __future__ import annotations

import plotly.express as px
import streamlit as st

from app.config import RESEARCH_DISCLAIMER
from app.database import SessionLocal
from app.services.market_heatmap import latest_market_heatmap_data, top_gainer_loser_summary
from app.services.tradingview_screener import run_tradingview_screening
from dashboard.ui_components import empty_state, professional_table, section_title, success_box, warning_box


def _format_symbol(row: dict | None) -> str:
    if not row:
        return "-"
    change = row.get("change_percent")
    return f"{row.get('symbol')} ({change:.2f}%)" if change is not None else str(row.get("symbol") or "-")


def render() -> None:
    st.title("Market Heatmap")
    st.caption(RESEARCH_DISCLAIMER)
    st.info("Sector heatmap and top gainers/losers use the latest stored TradingView EGX screening snapshot.")

    c1, c2 = st.columns([1, 3])
    if c1.button("Refresh TradingView snapshot"):
        with SessionLocal() as db, st.spinner("Running TradingView screening..."):
            result = run_tradingview_screening(db, limit=500)
        success_box(f"Stored screening run {result.get('run_id')} with {result.get('symbols_count')} symbols.")

    with SessionLocal() as db:
        data = latest_market_heatmap_data(db)

    run = data.get("run")
    stocks = data.get("stocks")
    sectors = data.get("sectors")
    top_gainers = data.get("top_gainers")
    top_losers = data.get("top_losers")
    summary = top_gainer_loser_summary(data)

    if not run or stocks.empty:
        warning_box("No market heatmap data is available yet. Run TradingView screening first.")
        return

    c2.caption(f"Latest run: {run.id} | status: {run.provider_status} | symbols: {run.symbols_count} | time: {run.created_at}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Stocks", len(stocks))
    m2.metric("Sectors", sectors["sector"].nunique() if not sectors.empty else 0)
    m3.metric("Top gainer", _format_symbol(summary["top_gainer"]))
    m4.metric("Top loser", _format_symbol(summary["top_loser"]))

    sector_filter = st.multiselect("Filter sectors", sorted(stocks["sector"].dropna().unique().tolist()))
    filtered = stocks[stocks["sector"].isin(sector_filter)] if sector_filter else stocks

    section_title("Stock Heatmap By Sector")
    if filtered.empty:
        empty_state("No stocks match the selected sectors.")
    else:
        fig = px.treemap(
            filtered,
            path=["sector", "symbol"],
            values="heatmap_size",
            color="change_percent",
            color_continuous_scale=["#dc2626", "#f8fafc", "#16a34a"],
            color_continuous_midpoint=0,
            hover_data={
                "company_name": True,
                "change_percent": ":.2f",
                "close": ":.2f",
                "volume": ":,.0f",
                "final_score": ":.0f",
                "recommendation": True,
            },
        )
        fig.update_layout(margin=dict(t=10, l=10, r=10, b=10), height=560)
        st.plotly_chart(fig, use_container_width=True)

    section_title("Sector Performance")
    if sectors.empty:
        empty_state("No sector summary available.")
    else:
        sector_fig = px.bar(
            sectors.sort_values("avg_change_percent", ascending=True),
            x="avg_change_percent",
            y="sector",
            orientation="h",
            color="avg_change_percent",
            color_continuous_scale=["#dc2626", "#f8fafc", "#16a34a"],
            color_continuous_midpoint=0,
            hover_data=["symbols", "gainers", "losers", "total_volume", "avg_score"],
        )
        sector_fig.update_layout(height=max(360, len(sectors) * 30), margin=dict(t=10, l=10, r=10, b=10))
        st.plotly_chart(sector_fig, use_container_width=True)
        professional_table(sectors, height=320, search_key="market_heatmap_sector_search")

    tabs = st.tabs(["Top Gainers", "Top Losers", "All Stocks"])
    visible = ["symbol", "company_name", "sector", "change_percent", "close", "volume", "final_score", "recommendation", "tv_vote", "rsi"]
    with tabs[0]:
        professional_table(top_gainers[[col for col in visible if col in top_gainers.columns]], height=420, search_key="market_heatmap_gainers_search")
    with tabs[1]:
        professional_table(top_losers[[col for col in visible if col in top_losers.columns]], height=420, search_key="market_heatmap_losers_search")
    with tabs[2]:
        professional_table(filtered[[col for col in visible if col in filtered.columns]], height=520, search_key="market_heatmap_all_search")
