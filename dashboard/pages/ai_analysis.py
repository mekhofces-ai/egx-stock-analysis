from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Stock
from app.services.ai_analysis_service import get_all_ai_analyses, get_ai_analysis_for_symbol, get_market_overview
from app.services.ai_llm_service import latest_opinions
from dashboard.ui_components import (
    empty_state,
    metric_card,
    professional_table,
    section_title,
    signal_badge,
    risk_badge,
    warning_box,
)


def _display_analysis_detail(data: dict, title: str) -> None:
    score = data.get("score")
    sig = data.get("signal", "N/A")
    st.markdown(
        f"""
        <div class="egx-box" style="margin:2px 0;padding:8px 12px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-weight:700;">{title}</span>
                <span>
                    {signal_badge(sig)}
                    {f'<span class="small-muted">Score: {score:.1f}</span>' if score is not None else '<span class="small-muted">No data</span>'}
                </span>
            </div>
            {f'<div class="small-muted" style="margin-top:4px;">{data.get("reason", "")}</div>' if data.get("reason") else ""}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render() -> None:
    st.title("AI-Powered Analysis")
    st.caption("Multi-dimensional AI analysis combining technical, financial, news, telegram, strategy signals, and LLM-generated opinions.")

    overview = get_market_overview()
    if overview.get("total_symbols", 0) == 0:
        empty_state("No active symbols found. Import stocks first.")
        return

    total = overview["total_symbols"]
    strong_buys = overview["strong_buys"]
    buys = overview["buys"]
    watches = overview["watches"]
    neutrals = overview["neutrals"]
    avoids = overview["avoids"]
    sells = overview["sells"]
    avg_score = overview["avg_score"]

    cols = st.columns(7)
    with cols[0]:
        metric_card("Symbols", str(total))
    with cols[1]:
        metric_card("Strong Buy", str(strong_buys), f"{strong_buys/total*100:.0f}%" if total else "")
    with cols[2]:
        metric_card("Buy", str(buys), f"{buys/total*100:.0f}%" if total else "")
    with cols[3]:
        metric_card("Watch", str(watches), f"{watches/total*100:.0f}%" if total else "")
    with cols[4]:
        metric_card("Neutral", str(neutrals), f"{neutrals/total*100:.0f}%" if total else "")
    with cols[5]:
        metric_card("Avoid", str(avoids), f"{avoids/total*100:.0f}%" if total else "")
    with cols[6]:
        metric_card("Sell", str(sells), f"{sells/total*100:.0f}%" if total else "")

    st.markdown(
        f'<div class="egx-box">Average AI Score: <strong>{avg_score:.1f}%</strong></div>',
        unsafe_allow_html=True,
    )

    with st.expander("Latest LLM Opinions", expanded=True):
        opinions = latest_opinions(limit=20)
        if opinions:
            for op in opinions:
                with st.container():
                    cols2 = st.columns([1, 3, 1])
                    with cols2[0]:
                        st.markdown(f"**{op['symbol']}**")
                        st.markdown(f"{signal_badge(op.get('ai_signal', 'N/A'))}", unsafe_allow_html=True)
                    with cols2[1]:
                        st.markdown(f"_{op.get('ai_opinion', '')}_")
                        st.caption(f"Drivers: {', '.join(op.get('ai_key_drivers', [])[:3]) or 'N/A'}")
                        if op.get("ai_reasoning"):
                            st.caption(op["ai_reasoning"][:200] + ("..." if len(op.get("ai_reasoning", "")) > 200 else ""))
                    with cols2[2]:
                        st.caption(f"Score: {op.get('ai_score', 'N/A')}")
                        st.caption(f"Conf: {op.get('ai_confidence', 'N/A')}")
                        if op.get("ai_entry_zone"):
                            st.caption(f"Entry: {op['ai_entry_zone']}")
                    st.divider()
        else:
            empty_state("No LLM opinions yet. Run automation with ENABLE_AI_ANALYSIS=true.")

    section_title("Top Opportunities (AI-Ranked)")
    top = overview.get("top_opportunities", [])
    if top:
        df = pd.DataFrame(top)
        display = df[[c for c in ["symbol", "ai_score", "ai_signal", "sector", "last_price"] if c in df.columns]].copy()
        if not display.empty:
            display.columns = [c.replace("_", " ").title() for c in display.columns]
            professional_table(display)
    else:
        empty_state("No analysis results yet.")

    with st.expander("Deep Dive: Symbol Detail", expanded=False):
        with SessionLocal() as db:
            symbols = db.scalars(select(Stock.symbol).where(Stock.is_active == True).order_by(Stock.symbol.asc())).all()
        selected = st.selectbox("Choose a symbol", symbols or ["COMI"])
        if selected:
            detail = get_ai_analysis_for_symbol(selected)
            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                metric_card("AI Score", f'{detail["ai_score"]:.1f}%')
                st.markdown(f'<div style="margin:6px 0;">Signal: {signal_badge(detail["ai_signal"])}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="small-muted">Last Price: {detail.get("last_price") or "N/A"}</div>', unsafe_allow_html=True)
            with c2:
                if detail.get("combined_analysis"):
                    ca = detail["combined_analysis"]
                    metric_card("Combined Score", f'{ca["score"]:.1f}%' if ca.get("score") else "N/A")
                    st.markdown(f'<div style="margin:6px 0;">{signal_badge(ca.get("recommendation", ""))}</div>', unsafe_allow_html=True)
            with c3:
                if detail.get("opportunity"):
                    opp = detail["opportunity"]
                    metric_card("Opportunity", f'{opp["score"]:.1f}%' if opp.get("score") else "N/A")
                    st.markdown(f'<div style="margin:6px 0;">{signal_badge(opp.get("recommendation", ""))}</div>', unsafe_allow_html=True)

            section_title("LLM Opinion")
            ai_op = detail.get("ai_opinion")
            if ai_op:
                st.markdown(
                    f"""
                    <div class="egx-box" style="background:#f0f7ff;border-color:#93c5fd;">
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <span><strong>AI Signal:</strong> {signal_badge(ai_op.get("signal", ""))}</span>
                            <span class="small-muted">Score: {ai_op.get("score", "N/A")} | Conf: {ai_op.get("confidence", "N/A")} | {ai_op.get("model_used", "")}</span>
                        </div>
                        <div style="margin-top:8px;"><em>"{ai_op.get("opinion", "")}"</em></div>
                        {f'<div class="small-muted" style="margin-top:6px;"><strong>Reasoning:</strong> {ai_op["reasoning"]}</div>' if ai_op.get("reasoning") else ""}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if ai_op.get("key_drivers"):
                    st.markdown(f"**Key Drivers:** {', '.join(ai_op['key_drivers'])}")
                if ai_op.get("risks"):
                    st.markdown(f"**Risks:** {', '.join(ai_op['risks'])}")
                if ai_op.get("entry_zone") or ai_op.get("stop_loss"):
                    st.markdown(f"**Entry Zone:** {ai_op.get('entry_zone', 'N/A')} | **Stop:** {ai_op.get('stop_loss', 'N/A')} | **Targets:** {ai_op.get('target_1', 'N/A')}/{ai_op.get('target_2', 'N/A')}")
                if ai_op.get("tokens_used"):
                    st.caption(f"Tokens: {ai_op['tokens_used']} | Horizon: {ai_op.get('time_horizon', 'N/A')} | Catalyst: {ai_op.get('catalyst', 'N/A')}")
            else:
                warning_box("No LLM opinion available yet. Enable ENABLE_AI_ANALYSIS=true and run automation.")

            section_title("Component Breakdown")
            _display_analysis_detail(detail["technical"], "Technical Analysis")
            _display_analysis_detail(detail["financial"], "Financial Analysis")
            _display_analysis_detail(detail["news"], "News Sentiment")
            _display_analysis_detail(detail["telegram"], "Telegram Signals")
            _display_analysis_detail(detail["strategy"], "Strategy Signals")

            if detail.get("final_decision"):
                fd = detail["final_decision"]
                section_title("Final Decision")
                st.markdown(
                    f"""
                    <div class="egx-box">
                        <div style="display:flex;gap:16px;flex-wrap:wrap;">
                            <div><strong>Signal:</strong> {signal_badge(fd.get("signal", ""))}</div>
                            <div><strong>Score:</strong> {fd.get("score", 0):.1f}%</div>
                            <div><strong>Risk:</strong> {risk_badge(fd.get("risk_level", ""))}</div>
                            <div><strong>Regime:</strong> {fd.get("market_regime", "N/A")}</div>
                            <div><strong>Entry:</strong> {fd.get("entry_price", "N/A")}</div>
                            <div><strong>Stop:</strong> {fd.get("stop_loss", "N/A")}</div>
                            <div><strong>TP1/TP2:</strong> {fd.get("take_profit_1", "N/A")}/{fd.get("take_profit_2", "N/A")}</div>
                        </div>
                        {f'<div class="small-muted" style="margin-top:6px;">{fd.get("reason", "")}</div>' if fd.get("reason") else ""}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    with st.expander("Worst Performers", expanded=False):
        worst = overview.get("worst_performers", [])
        if worst:
            df = pd.DataFrame(worst)
            display = df[[c for c in ["symbol", "ai_score", "ai_signal", "sector"] if c in df.columns]].copy()
            if not display.empty:
                display.columns = [c.replace("_", " ").title() for c in display.columns]
                professional_table(display)
        else:
            empty_state("No data.")

    with st.expander("Raw Data: All Analyses", expanded=False):
        try:
            all_analyses = get_all_ai_analyses()
            if all_analyses:
                df = pd.DataFrame(all_analyses)
                cols = ["symbol", "ai_score", "ai_signal", "sector", "last_price"]
                display = df[[c for c in cols if c in df.columns]].copy()
                if not display.empty:
                    professional_table(display)
        except Exception:
            empty_state("Could not load full analysis data.")
