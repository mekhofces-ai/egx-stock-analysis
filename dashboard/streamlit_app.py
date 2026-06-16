from __future__ import annotations

import io
import hmac
import logging
import os
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DISCLAIMER, RISK_NOTE, get_settings
from app.database import SessionLocal, init_db
from app.intelligence.portfolio_bot import get_portfolio_settings, portfolio_value
from app.intelligence.risk_quality import analyze_market_regime
from app.models import (
    AppSetting,
    AutomationSetting,
    AutomationRun,
    AutomationState,
    BacktestQueue,
    BotUser,
    ChannelPerformance,
    DailyEGXReportRow,
    DailyEGXReportUpload,
    ExtractedSignal,
    FinalAnalysis,
    Opportunity,
    Stock,
    StockCombinedAnalysis,
    StrategyBacktest,
    StrategyBacktestSummary,
    StrategyBacktestTrade,
    StrategyCliV6Result,
    StrategyDefinition,
    StrategyResult,
    TelegramMediaAnalysis,
    TelegramMessage,
    TelegramMessageSymbol,
    TelegramSentAlert,
    TelegramSource,
    TelegramSubscriber,
    TradingViewScreeningResult,
    TradingViewScreeningRun,
)
from app.services.alerts import alerts_configured, send_buy_recommendation_alerts, send_pending_buy_signal_alerts
from app.services.analysis_runner import analyze_symbol_manually, format_alert
from app.services.automation_runner import get_automation_status, run_automation_cycle, set_automation_enabled
from app.services.backtest_cli_v6 import run_cli_v6_backtest_universe
from app.services.backtest_queue import enqueue_backtest, process_backtest_queue, queue_status_rows
from app.services.backtest_engine import run_universe_backtests as run_reviewed_universe_backtests
from app.services.dynamic_settings import automation_snapshot, get_setting_value, list_settings, set_setting
from app.services.daily_egx_report import import_report_bytes, latest_report_rows, summarize_latest_report
from app.services.image_analyzer import analyze_existing_images, analyze_pending_media
from app.services.ingestion import run_ingestion_cycle
from app.services.market_daily_evaluation import evaluate_daily_market
from app.services.market_depth import build_market_depth_screener
from app.services.market_data.tradingview_screener import TradingViewScreenerProvider
from app.services.opportunity_engine import refresh_opportunities, send_buy_alerts as send_opportunity_buy_alerts, send_strategy_notifications
from app.services.performance_tracker import update_channel_performance
from app.services.reports import send_daily_report, send_night_opportunity_report
from app.services.screener_recommendations import build_final_recommendations
from app.services.source_importer import import_sources_from_df, read_sources_file
from app.services.stock_analysis_engine import (
    build_combined_analysis,
    latest_related_media,
    latest_related_telegram,
    refresh_combined_analysis,
)
from app.services.strategy import run_strategy_for_symbol, run_strategy_universe
from app.services.strategy_registry import (
    latest_strategy_results,
    list_strategies,
    run_all_enabled_strategies,
    run_strategy as run_registered_strategy,
    set_strategy_enabled,
)
from app.services.subscribers import send_alert_to_subscribers_sync, send_message_to_chat_sync
from app.services.strategies.cli_v6_egx import run_cli_v6_universe
from app.services.tradingview_screener import run_tradingview_screening
from dashboard.pages import (
    accuracy_dashboard as intelligence_accuracy_page,
    accuracy_lab as intelligence_accuracy_lab_page,
    admin_settings as intelligence_admin_settings_page,
    ai_analysis as intelligence_ai_analysis_page,
    analysis_comparison as intelligence_comparison_page,
    backtesting_page as intelligence_backtesting_page,
    bot_status as intelligence_bot_status_page,
    daily_opportunities as intelligence_daily_opportunities_page,
    daily_reports as intelligence_daily_reports_page,
    daily_market_evaluation as intelligence_daily_market_evaluation_page,
    data_sources as intelligence_data_sources_page,
    daily_prediction_review as intelligence_daily_prediction_review_page,
    executive_overview as intelligence_executive_overview_page,
    financial_analysis as intelligence_financial_analysis_page,
    last7_audit as intelligence_last7_audit_page,
    live_trades as intelligence_live_trades_page,
    intraday_scanner as intelligence_intraday_scanner_page,
    market_heatmap as intelligence_market_heatmap_page,
    market_regime as intelligence_market_regime_page,
    missed_opportunities as intelligence_missed_opportunities_page,
    missed_opportunity_diagnosis as intelligence_missed_opportunity_diagnosis_page,
    mistake_review as intelligence_mistake_review_page,
    news_analysis as intelligence_news_analysis_page,
    news_impact as intelligence_news_impact_page,
    portfolio_bot_page as intelligence_portfolio_page,
    recommendation_performance as intelligence_recommendation_performance_page,
    reports_center as intelligence_reports_center_page,
    risk_audit_center as intelligence_risk_audit_page,
    risk_expectancy as intelligence_risk_expectancy_page,
    pump_risk_monitor as intelligence_pump_risk_monitor_page,
    recommendation_quality as intelligence_recommendation_quality_page,
    setup_control_center as intelligence_setup_control_center_page,
    stock_full_analysis as intelligence_stock_full_page,
    source_accuracy as intelligence_source_accuracy_page,
    strategy_learning_center as intelligence_strategy_learning_center_page,
    system_health_admin as intelligence_system_health_admin_page,
    telegram_intelligence as intelligence_telegram_intelligence_page,
    telegram_performance as intelligence_telegram_performance_page,
    trading_alerts as intelligence_trading_alerts_page,
    trading_control_center as intelligence_trading_control_page,
    why_not_selected as intelligence_why_not_selected_page,
    walk_forward_testing as intelligence_walk_forward_testing_page,
)
from dashboard.ui_components import data_gap_box, inject_professional_css, install_dataframe_search_patch, key_value_table, professional_table, reset_table_search_key_counts


st.set_page_config(page_title="EGX Telegram Analyst", layout="wide", initial_sidebar_state="expanded")
install_dataframe_search_patch()
reset_table_search_key_counts()
settings = get_settings()
init_db(seed=True)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def require_dashboard_auth() -> None:
    if not _env_enabled("DASHBOARD_AUTH_ENABLED", False):
        return
    expected = (
        os.getenv("DASHBOARD_PASSWORD")
        or os.getenv("DASHBOARD_ACCESS_CODE")
        or (str(settings.telegram_bot_private_chat_id) if settings.telegram_bot_private_chat_id is not None else "")
    )
    if not expected:
        st.error("Dashboard public access is blocked because DASHBOARD_PASSWORD is not configured.")
        st.stop()
    if st.session_state.get("egx_dashboard_authenticated"):
        return
    st.title("EGX Intelligence Login")
    st.caption("Protected dashboard access. Live trading remains disabled.")
    access_code = st.text_input("Access code", type="password", key="egx_dashboard_access_code")
    if st.button("Unlock Dashboard", type="primary"):
        if hmac.compare_digest(access_code or "", expected):
            st.session_state["egx_dashboard_authenticated"] = True
            st.rerun()
        st.error("Invalid access code.")
    st.stop()


require_dashboard_auth()


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg: #f6f7fb;
            --panel: #ffffff;
            --ink: #172033;
            --muted: #697386;
            --line: #dfe4ec;
            --blue: #205493;
            --cyan: #0f8b8d;
            --green: #15803d;
            --amber: #b7791f;
            --red: #b42318;
        }
        .stApp { background: var(--bg); color: var(--ink); }
        [data-testid="stSidebar"] {
            background: #111827;
            min-width: 236px !important;
            max-width: 268px !important;
        }
        [data-testid="stSidebar"] * { color: #e5e7eb; }
        [data-testid="stSidebar"] [role="radiogroup"] label {
            border-radius: 8px; padding: 6px 8px; margin: 2px 0;
        }
        h1, h2, h3 { color: var(--ink); letter-spacing: 0; }
        h1 { font-size: clamp(28px, 4vw, 44px); line-height: 1.08; }
        h2 { font-size: clamp(22px, 3vw, 32px); }
        div[data-testid="stMetric"] {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 14px 16px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, .05);
        }
        .block-container { padding-top: 1.35rem; padding-bottom: 2rem; }
        .section {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 16px 18px;
            margin: 10px 0 16px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, .04);
        }
        .banner {
            border-radius: 8px;
            padding: 12px 14px;
            margin: 8px 0 14px;
            border: 1px solid var(--line);
            background: #eef6ff;
            color: #17324d;
        }
        .warn { background: #fff7ed; color: #7c2d12; border-color: #fed7aa; }
        .ok { background: #ecfdf3; color: #14532d; border-color: #bbf7d0; }
        .badge {
            display: inline-block;
            border-radius: 999px;
            padding: 3px 9px;
            font-size: 12px;
            font-weight: 700;
            border: 1px solid var(--line);
            background: #f8fafc;
            color: var(--ink);
        }
        .buy { background: #dcfce7; color: #14532d; border-color: #86efac; }
        .watch { background: #e0f2fe; color: #075985; border-color: #7dd3fc; }
        .neutral { background: #f1f5f9; color: #334155; border-color: #cbd5e1; }
        .avoid, .sell, .risk { background: #fee2e2; color: #7f1d1d; border-color: #fecaca; }
        .small-muted { color: var(--muted); font-size: 13px; }
        div.stButton > button, div.stDownloadButton > button {
            border-radius: 8px;
            border: 1px solid #174777;
            background: #205493;
            color: white;
            font-weight: 650;
        }
        div.stButton > button:hover, div.stDownloadButton > button:hover {
            border-color: #0f8b8d;
            color: white;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


apply_theme()
inject_professional_css(str(get_setting_value("ui_theme_mode", "light", "string") or "light"))


def to_df(rows: list[Any]) -> pd.DataFrame:
    data: list[dict[str, Any]] = []
    for row in rows:
        item = {key: value for key, value in vars(row).items() if not key.startswith("_")}
        data.append(item)
    return pd.DataFrame(data)


def read_rows(model, order_col=None, limit: int | None = None) -> pd.DataFrame:
    with SessionLocal() as db:
        stmt = select(model)
        if order_col is not None:
            stmt = stmt.order_by(order_col)
        if limit:
            stmt = stmt.limit(limit)
        return to_df(db.scalars(stmt).all())


def channel_label_map() -> dict[int, str]:
    with SessionLocal() as db:
        sources = db.scalars(select(TelegramSource).order_by(TelegramSource.username)).all()
    return {source.id: (source.title or source.username) for source in sources}


def with_channel_names(df: pd.DataFrame, drop_source_id: bool = True) -> pd.DataFrame:
    if df.empty or "source_id" not in df.columns:
        return df
    labels = channel_label_map()
    output = df.copy()
    output.insert(0, "channel", output["source_id"].map(lambda value: labels.get(int(value), "-") if pd.notna(value) else "-"))
    if drop_source_id:
        output = output.drop(columns=["source_id"])
    return output


def render_top_header() -> None:
    cairo_now = pd.Timestamp.now(tz=ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d %H:%M")
    with SessionLocal() as db:
        latest_run = db.scalar(select(AutomationRun).order_by(AutomationRun.started_at.desc()))
        try:
            portfolio_settings = get_portfolio_settings(db)
            values = portfolio_value(db, portfolio_settings)
        except Exception:
            values = {"total_value": 0, "profit_loss": 0, "profit_loss_pct": 0}
        try:
            daily_market = evaluate_daily_market(db, persist=True)
            db.commit()
            market = {
                "regime": daily_market.get("market_regime"),
                "market_score": daily_market.get("market_score"),
                "trade_permission": daily_market.get("trade_permission"),
            }
        except Exception:
            try:
                market = analyze_market_regime(db, persist=False)
            except Exception:
                market = {"regime": "unknown", "market_score": None}
    last_update = latest_run.finished_at or latest_run.started_at if latest_run else None
    st.markdown(
        f"""
        <div class="egx-topbar">
          <div class="egx-chip"><b>Cairo</b><br>{cairo_now}</div>
          <div class="egx-chip"><b>Market Regime</b><br>{str(market.get('regime') or 'unknown').replace('_', ' ').title()} {market.get('market_score') or ''}</div>
          <div class="egx-chip"><b>Portfolio Value</b><br>{values.get('total_value', 0):,.0f} EGP</div>
          <div class="egx-chip"><b>Daily P/L</b><br>{values.get('profit_loss', 0):,.0f} EGP ({values.get('profit_loss_pct', 0):.2f}%)</div>
          <div class="egx-chip"><b>Latest Data Update</b><br>{last_update.strftime('%Y-%m-%d %H:%M') if last_update else '-'}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=120, show_spinner=False)
def cached_recommendations(limit: int = 500) -> dict[str, Any]:
    with SessionLocal() as db:
        run = build_final_recommendations(db, limit=limit)
        return {
            "provider": run.provider,
            "provider_status": run.provider_status,
            "provider_warning": run.provider_warning,
            "generated_at": run.generated_at,
            "rows": run.rows,
        }


@st.cache_data(ttl=120, show_spinner=False)
def cached_tradingview_rows(limit: int = 500) -> tuple[str, str | None, pd.DataFrame]:
    with SessionLocal() as db:
        symbols = db.scalars(select(Stock.symbol).where(Stock.is_active.is_(True)).order_by(Stock.symbol)).all()
    provider = TradingViewScreenerProvider(settings)
    try:
        df = provider._scan(symbols=symbols, limit=limit)
        return "available", None, df
    except Exception as exc:
        return "unavailable", str(exc), pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def cached_strategy_universe(limit: int = 30) -> dict[str, Any]:
    with SessionLocal() as db:
        return run_strategy_universe(db, limit=limit)


def status_banner(status: str, warning: str | None = None) -> None:
    if status == "available":
        st.markdown('<div class="banner ok">TradingView screener is available for the current session.</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div class="banner warn">TradingView screener is unavailable. {warning or ""}</div>',
            unsafe_allow_html=True,
        )


def action_badge(action: str) -> str:
    css = {
        "BUY": "buy",
        "WATCH": "watch",
        "NEUTRAL": "neutral",
        "AVOID": "avoid",
        "SELL": "sell",
        "HIGH_RISK": "risk",
    }.get(action, "neutral")
    return f'<span class="badge {css}">{action}</span>'


def export_buttons(df: pd.DataFrame, base_name: str) -> None:
    if df.empty:
        return
    col1, col2 = st.columns([1, 5])
    col1.download_button("CSV", df.to_csv(index=False).encode("utf-8"), f"{base_name}.csv", "text/csv")
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=base_name[:31])
    col2.download_button("Excel", buffer.getvalue(), f"{base_name}.xlsx")


def _alert_result_text(label: str, result: dict[str, Any] | None) -> str:
    if not result:
        return f"{label}: skipped"
    if not result.get("configured"):
        return f"{label}: not configured"
    return (
        f"{label}: sent {result.get('sent', 0)}, "
        f"eligible {result.get('eligible', 0)}, "
        f"duplicates {result.get('skipped_duplicate', 0)}, "
        f"strategy skips {result.get('skipped_strategy', 0)}"
    )


def ingestion_result_text(result) -> str:
    return (
        f"Update complete. New messages: {result.inserted_messages}, new analyses: {result.new_analyses}. "
        f"{_alert_result_text('Signal alerts', result.signal_alerts)}. "
        f"{_alert_result_text('Recommendation alerts', result.recommendation_alerts)}."
    )


def refresh_now(label: str = "Fetch Telegram and update analysis now") -> None:
    if st.button(label):
        with st.spinner("Fetching Telegram, parsing signals, and updating recommendations..."):
            result = run_ingestion_cycle()
            cached_recommendations.clear()
            cached_tradingview_rows.clear()
            cached_strategy_universe.clear()
        st.success(ingestion_result_text(result))


def tradingview_widget(symbol: str, height: int = 520) -> None:
    tv_symbol = f"EGX:{symbol.upper()}"
    html = f"""
    <iframe
      src="https://www.tradingview.com/widgetembed/?symbol={tv_symbol}&interval=D&hidesidetoolbar=1&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=RSI@tv-basicstudies%1FMACD@tv-basicstudies&theme=light&style=1&timezone=Africa%2FCairo&withdateranges=1"
      style="width:100%; height:{height}px; border:1px solid #dfe4ec; border-radius:8px;"
      allowtransparency="true"
      frameborder="0">
    </iframe>
    """
    components.html(html, height=height + 10)


def recommendation_df() -> pd.DataFrame:
    data = cached_recommendations()
    return pd.DataFrame(data["rows"])


def page_home() -> None:
    st.title("EGX Telegram Analyst")
    with SessionLocal() as db:
        source_count = db.query(TelegramSource).count()
        active_sources = db.query(TelegramSource).filter(TelegramSource.is_active.is_(True)).count()
        message_count = db.query(TelegramMessage).count()
        signal_count = db.query(ExtractedSignal).count()
        analysis_count = db.query(FinalAnalysis).count()
        stock_count = db.query(Stock).count()

    cols = st.columns(6)
    cols[0].metric("Stocks", stock_count)
    cols[1].metric("Sources", source_count)
    cols[2].metric("Active", active_sources)
    cols[3].metric("Messages", message_count)
    cols[4].metric("Signals", signal_count)
    cols[5].metric("Analyses", analysis_count)

    data = cached_recommendations()
    status_banner(data["provider_status"], data["provider_warning"])
    df = pd.DataFrame(data["rows"])
    confirmed = df[df["telegram_signals"] > 0] if not df.empty and "telegram_signals" in df else pd.DataFrame()
    st.subheader("Telegram-Confirmed Recommendations")
    if confirmed.empty:
        st.info("No Telegram-confirmed screener recommendations yet.")
    else:
        columns = [
            "symbol",
            "final_recommendation",
            "final_score",
            "tv_vote",
            "telegram_vote",
            "telegram_signals",
            "telegram_buy",
            "rsi",
            "change_percent",
            "volume",
        ]
        st.dataframe(confirmed[columns].head(20), use_container_width=True, hide_index=True)
    st.caption(f"Disclaimer: {DISCLAIMER}")


def page_opportunities() -> None:
    st.title("Opportunities")
    st.info(
        "This page reads the opportunities table: an actionable shortlist built by the opportunity engine from combined analysis, strategy confirmation, TradingView data where available, backtests, Telegram signals, freshness, and risk. "
        "Daily Best Opportunities reads final_stock_decisions, so the rankings can be different by design."
    )
    left, mid, right = st.columns([1.2, 1.1, 2])
    if left.button("Refresh opportunities"):
        with st.spinner("Refreshing TradingView screening, strategy confirmation, and opportunity scores..."):
            with SessionLocal() as db:
                result = refresh_opportunities(db, limit=500, run_screening=True)
        cached_recommendations.clear()
        cached_tradingview_rows.clear()
        cached_strategy_universe.clear()
        st.success(f"Saved {result['saved']} opportunity rows. Provider status: {result.get('provider_status')}.")
    if mid.button("Send opportunity alerts"):
        with st.spinner("Sending unsent BUY opportunity alerts..."):
            with SessionLocal() as db:
                result = send_opportunity_buy_alerts(db)
        st.success(
            f"Eligible {result.get('eligible', 0)}, sent {result.get('sent', 0)}, "
            f"duplicates {result.get('skipped_duplicate', 0)}."
        )

    with SessionLocal() as db:
        rows = db.scalars(select(Opportunity).order_by(Opportunity.final_score.desc(), Opportunity.updated_at.desc()).limit(250)).all()
    if not rows:
        st.info("No opportunity rows stored yet. Refresh opportunities to build them from real system data.")
        return

    df = to_df(rows)
    buy_count = int((df["recommendation"] == "BUY").sum())
    watch_count = int((df["recommendation"] == "WATCH").sum())
    updated = df["updated_at"].max()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BUY", buy_count)
    c2.metric("WATCH", watch_count)
    c3.metric("Rows", len(df))
    c4.metric("Last update", str(updated)[:16])

    action_filter = right.multiselect("Recommendation", ["BUY", "WATCH", "NEUTRAL", "AVOID"], default=["BUY", "WATCH"])
    filtered = df[df["recommendation"].isin(action_filter)] if action_filter else df
    visible = [
        "symbol",
        "recommendation",
        "final_score",
        "confidence",
        "entry_price",
        "target_price",
        "stop_loss",
        "reason",
        "updated_at",
    ]
    st.dataframe(filtered[visible], use_container_width=True, hide_index=True)
    export_buttons(filtered[visible], "egx_opportunities")

    if filtered.empty:
        return
    selected = st.selectbox("Opportunity detail", filtered["symbol"].tolist())
    selected_row = filtered[filtered["symbol"] == selected].iloc[0].to_dict()
    components_json = selected_row.get("components_json") or {}
    components = components_json.get("components") or {}
    source_rows = [
        {"component": key, "score": value, "weight": (components_json.get("weights") or {}).get(key)}
        for key, value in components.items()
    ]
    cols = st.columns(4)
    cols[0].metric("Opportunity score", f"{selected_row['final_score']:.0f}")
    cols[1].metric("Risk score", f"{components_json.get('risk_score', 0):.0f}")
    cols[2].metric("Freshness", f"{components_json.get('freshness_score', 0):.0f}")
    cols[3].metric("Decision", selected_row["recommendation"])
    if source_rows:
        st.subheader("Score components")
        st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)
    st.subheader("Backtest contribution")
    backtests = components_json.get("backtests") or []
    if backtests:
        st.dataframe(pd.DataFrame(backtests), use_container_width=True, hide_index=True)
    else:
        st.info("No stored reviewed backtest is attached yet.")
    st.caption(f"Scores use only live/stored system sources. Disclaimer: {DISCLAIMER}")


def page_final_recommendations() -> None:
    st.title("Final Recommendations")
    top_left, top_right = st.columns([1.3, 1])
    if top_left.button("Refresh TradingView and Telegram comparison"):
        cached_recommendations.clear()
        cached_strategy_universe.clear()
    if top_right.button("Send BUY alerts now"):
        with st.spinner("Checking unsent BUY signals and recommendations..."):
            with SessionLocal() as db:
                signal_alerts = send_pending_buy_signal_alerts(db)
                recommendation_alerts = send_buy_recommendation_alerts(db)
        st.success(
            f"{_alert_result_text('Signal alerts', signal_alerts)}. "
            f"{_alert_result_text('Recommendation alerts', recommendation_alerts)}."
        )
    if not alerts_configured(settings):
        st.markdown(
            '<div class="banner warn">Telegram BUY alerts are not fully configured. Add TELEGRAM_BOT_TOKEN and activate at least one Telegram subscriber/admin user.</div>',
            unsafe_allow_html=True,
        )
    data = cached_recommendations()
    status_banner(data["provider_status"], data["provider_warning"])
    df = pd.DataFrame(data["rows"])
    if df.empty:
        st.warning("No recommendation rows are available.")
        return

    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.2, 1])
    action_filter = c1.multiselect("Action", ["BUY", "WATCH", "NEUTRAL", "AVOID", "SELL", "HIGH_RISK"], default=["BUY", "WATCH"])
    telegram_only = c2.toggle("Telegram confirmed", value=False)
    min_score = c3.slider("Minimum score", 0, 100, 55)
    row_limit = c4.slider("Rows", 10, 200, 50)

    filtered = df[df["final_score"] >= min_score]
    if action_filter:
        filtered = filtered[filtered["final_recommendation"].isin(action_filter)]
    if telegram_only:
        filtered = filtered[filtered["telegram_signals"] > 0]

    table_columns = [
        "symbol",
        "name",
        "final_recommendation",
        "final_score",
        "smart_action_now",
        "smart_plan",
        "smart_score_10",
        "tv_vote",
        "tv_score",
        "telegram_vote",
        "telegram_score",
        "telegram_signals",
        "telegram_buy",
        "telegram_sell",
        "rsi",
        "change_percent",
        "volume",
    ]
    st.dataframe(filtered[table_columns].head(row_limit), use_container_width=True, hide_index=True)
    export_buttons(filtered[table_columns], "egx_final_recommendations")

    symbols = filtered["symbol"].head(row_limit).tolist()
    if symbols:
        selected_symbol = st.selectbox("Recommendation detail", symbols)
        selected = filtered[filtered["symbol"] == selected_symbol].iloc[0].to_dict()
        st.markdown(action_badge(selected["final_recommendation"]), unsafe_allow_html=True)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Final score", f"{selected['final_score']:.0f}")
        col2.metric("TV score", f"{selected['tv_score']:.0f}")
        col3.metric("Telegram signals", int(selected["telegram_signals"]))
        col4.metric("RSI", f"{selected['rsi']:.1f}")
        st.write("Reasons")
        st.write(selected.get("reasons") or [])
        st.write("Warnings")
        st.write(selected.get("warnings") or [])
    st.caption(f"Generated at: {data['generated_at']} | Disclaimer: {DISCLAIMER}")


def page_all_stocks() -> None:
    st.title("All Stocks")
    df = recommendation_df()
    with SessionLocal() as db:
        stocks = read_rows(Stock, Stock.symbol)
    if df.empty:
        st.warning("No screener recommendation rows are available.")
        if not stocks.empty:
            st.dataframe(stocks, use_container_width=True, hide_index=True)
        return
    c1, c2, c3 = st.columns([1, 1, 2])
    action_filter = c1.multiselect("Recommendation", ["BUY", "WATCH", "NEUTRAL", "AVOID", "SELL", "HIGH_RISK"])
    smart_filter = c2.multiselect(
        "Smart action",
        ["BUY NOW", "BREAKOUT BUY", "WATCH EARLY BUY", "WAIT PULLBACK", "WATCH", "WAIT", "DO NOT BUY NOW"],
    )
    search = c3.text_input("Search symbol/name/sector")
    filtered = df.copy()
    if action_filter:
        filtered = filtered[filtered["final_recommendation"].isin(action_filter)]
    if smart_filter:
        filtered = filtered[filtered["smart_action_now"].isin(smart_filter)]
    if search:
        haystack = (
            filtered["symbol"].astype(str)
            + " "
            + filtered["name"].astype(str)
            + " "
            + filtered["sector"].astype(str)
        )
        filtered = filtered[haystack.str.contains(search, case=False, na=False)]
    cols = [
        "symbol",
        "name",
        "sector",
        "last_price",
        "final_recommendation",
        "final_score",
        "smart_action_now",
        "smart_plan",
        "smart_main_trend",
        "smart_score_10",
        "rsi",
        "change_percent",
        "volume",
        "telegram_signals",
        "tradingview_chart_url",
    ]
    st.dataframe(filtered[cols], use_container_width=True, hide_index=True)
    export_buttons(filtered[cols], "all_egx_stocks_smart_recommendations")
    st.caption(f"Every active EGX stock is listed with TradingView screener data when available. Disclaimer: {DISCLAIMER}")


def page_stock_detail() -> None:
    st.title("Stock Detail")
    df = recommendation_df()
    with SessionLocal() as db:
        stock_symbols = db.scalars(select(Stock.symbol).where(Stock.is_active.is_(True)).order_by(Stock.symbol)).all()
    if not stock_symbols:
        st.warning("No active stocks found.")
        return
    selected_symbol = st.selectbox("Stock", stock_symbols, index=stock_symbols.index("COMI") if "COMI" in stock_symbols else 0)
    row = df[df["symbol"] == selected_symbol].iloc[0].to_dict() if not df.empty and selected_symbol in set(df["symbol"]) else {}
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == selected_symbol))
        recent_signals = db.scalars(
            select(ExtractedSignal)
            .where(ExtractedSignal.stock_symbol == selected_symbol)
            .order_by(ExtractedSignal.created_at.desc())
            .limit(25)
        ).all()
        recent_analyses = db.scalars(
            select(FinalAnalysis)
            .where(FinalAnalysis.symbol == selected_symbol)
            .order_by(FinalAnalysis.created_at.desc())
            .limit(10)
        ).all()
        message_ids = [signal.telegram_message_id for signal in recent_signals if signal.telegram_message_id]
        chart_messages = []
        if message_ids:
            chart_messages = db.scalars(
                select(TelegramMessage)
                .where(TelegramMessage.id.in_(message_ids))
                .where(TelegramMessage.image_path.is_not(None))
                .order_by(TelegramMessage.created_at.desc())
            ).all()

    st.subheader(f"{selected_symbol} - {(stock.name_en if stock else row.get('name')) or ''}")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Last price", row.get("last_price", "-"))
    c2.metric("Final", row.get("final_recommendation", "-"))
    c3.metric("Score", f"{row.get('final_score', 0):.0f}" if row else "-")
    c4.metric("Smart", row.get("smart_action_now", "-"))
    c5.metric("RSI", f"{row.get('rsi', 0):.1f}" if row else "-")

    st.markdown(
        f"""
        <div class="section">
        <b>Smart PRO action:</b> {row.get("smart_action_now", "-")}<br>
        <b>Plan:</b> {row.get("smart_plan", "-")} | <b>Trend:</b> {row.get("smart_main_trend", "-")} |
        <b>Pressure:</b> {row.get("smart_pressure", "-")} | <b>Volume:</b> {row.get("smart_volume_status", "-")}<br>
        <b>Buy zone:</b> {row.get("smart_buy_zone", "-")} |
        <b>Entry:</b> {row.get("smart_suggested_entry", "-")} |
        <b>Stop:</b> {row.get("smart_suggested_stop", "-")}<br>
        <b>Targets:</b> scalp {row.get("smart_target_scalp", "-")}, swing {row.get("smart_target_swing", "-")}, long {row.get("smart_target_long", "-")}<br>
        <b>Advice:</b> {row.get("smart_advice", "-")}
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Chart", "Recommendations", "Strategy", "Telegram Signals", "Telegram Images"])
    with tab1:
        tradingview_widget(selected_symbol)
        st.link_button("Open full TradingView chart", row.get("tradingview_chart_url") or f"https://www.tradingview.com/chart/?symbol=EGX%3A{selected_symbol}")
    with tab2:
        detail = pd.DataFrame([row]) if row else pd.DataFrame()
        if detail.empty:
            st.info("No TradingView recommendation row is available for this stock.")
        else:
            st.dataframe(detail, use_container_width=True, hide_index=True)
            st.write("Reasons")
            st.write(row.get("reasons") or [])
            st.write("Warnings")
            st.write(row.get("warnings") or [])
        if recent_analyses:
            st.write("Last saved analyses")
            analysis_df = with_channel_names(to_df(recent_analyses))
            visible_cols = [col for col in analysis_df.columns if col not in {"id", "technical_analysis_id", "extracted_signal_id"}]
            st.dataframe(analysis_df[visible_cols], use_container_width=True, hide_index=True)
    with tab3:
        with SessionLocal() as db:
            strategy = run_strategy_for_symbol(db, selected_symbol)
        frame_df = pd.DataFrame(strategy["timeframes"])
        st.markdown(action_badge(strategy["strategy_action"]), unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Strategy score", f"{strategy['strategy_score']:.0f}")
        c2.metric("BUY frames", strategy["buy_timeframes"])
        c3.metric("WATCH frames", strategy["watch_timeframes"])
        c4.metric("Data quality", strategy.get("data_quality") or "-")
        cols = [
            "timeframe",
            "action",
            "score",
            "trend",
            "last_price",
            "reference_price",
            "price_difference_percent",
            "entry",
            "stop",
            "target",
            "win_rate",
            "trades",
            "total_return_pct",
            "max_drawdown_pct",
            "provider",
            "data_quality",
            "is_mock",
            "error",
        ]
        st.dataframe(frame_df[[col for col in cols if col in frame_df.columns]], use_container_width=True, hide_index=True)
    with tab4:
        if recent_signals:
            signal_df = with_channel_names(to_df(recent_signals))
            visible_cols = [col for col in signal_df.columns if col not in {"id", "telegram_message_id"}]
            st.dataframe(signal_df[visible_cols], use_container_width=True, hide_index=True)
        else:
            st.info("No Telegram signals found for this stock yet.")
    with tab5:
        if chart_messages:
            image_df = with_channel_names(to_df(chart_messages))
            visible_cols = [col for col in image_df.columns if col not in {"id", "raw_json"}]
            st.dataframe(image_df[visible_cols], use_container_width=True, hide_index=True)
            image_path = chart_messages[0].image_path
            if image_path and Path(image_path).exists():
                st.image(str(Path(image_path).resolve()), caption=f"Latest Telegram image for {selected_symbol}")
        else:
            st.info("No Telegram chart images tied to this stock yet.")
    st.caption(f"Smart PRO is a decision overlay derived from available screener/Telegram data. Disclaimer: {DISCLAIMER}")


def page_tradingview_screener() -> None:
    st.title("TradingView EGX Screener")
    top_a, top_b = st.columns([1, 3])
    if top_a.button("Run and store screening"):
        with st.spinner("Running TradingView screening and storing snapshot..."):
            with SessionLocal() as db:
                result = run_tradingview_screening(db, limit=500)
        cached_tradingview_rows.clear()
        cached_recommendations.clear()
        st.success(f"Stored screening run {result['run_id']} with {result['symbols_count']} rows. Status: {result['provider_status']}.")
    if top_b.button("Refresh live screener"):
        cached_tradingview_rows.clear()
        cached_strategy_universe.clear()

    with SessionLocal() as db:
        latest_run = db.scalar(select(TradingViewScreeningRun).order_by(TradingViewScreeningRun.created_at.desc()))
        stored_rows = []
        if latest_run:
            stored_rows = db.scalars(
                select(TradingViewScreeningResult)
                .where(TradingViewScreeningResult.run_id == latest_run.id)
                .order_by(TradingViewScreeningResult.final_score.desc())
                .limit(250)
            ).all()
    if latest_run and stored_rows:
        st.subheader("Latest stored screening")
        cols = st.columns(4)
        cols[0].metric("Run", latest_run.id)
        cols[1].metric("Status", latest_run.provider_status)
        cols[2].metric("Symbols", latest_run.symbols_count)
        cols[3].metric("Completed", str(latest_run.completed_at or latest_run.created_at)[:16])
        stored_df = to_df(stored_rows)
        stored_visible = [
            "symbol",
            "recommendation",
            "final_score",
            "tv_vote",
            "telegram_vote",
            "close",
            "change_percent",
            "rsi",
            "volume",
            "technical_rating",
            "moving_averages_rating",
            "oscillators_rating",
            "created_at",
        ]
        st.dataframe(stored_df[stored_visible], use_container_width=True, hide_index=True)
        export_buttons(stored_df[stored_visible], "tradingview_screening_snapshot")
        if latest_run.provider_warning:
            st.warning(latest_run.provider_warning)
    st.subheader("Live screener")
    status, warning, df = cached_tradingview_rows(500)
    status_banner(status, warning)
    if df.empty:
        return
    for col in ["volume", "change_percent", "RSI", "Recommend.All"]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    c1, c2, c3 = st.columns(3)
    filter_name = c1.selectbox(
        "Filter",
        [
            "Top volume",
            "Strong technical buy",
            "RSI oversold",
            "RSI overbought",
            "Breakout candidates",
            "Unusual volume",
            "Telegram hype + technical confirmation",
        ],
    )
    limit = c2.slider("Rows", 10, 250, 75)
    search = c3.text_input("Search symbol/name")

    filtered = df.copy()
    if filter_name == "Strong technical buy":
        filtered = filtered[filtered["Recommend.All"].fillna(0) >= 0.3].sort_values("Recommend.All", ascending=False)
    elif filter_name == "RSI oversold":
        filtered = filtered[filtered["RSI"].fillna(100) <= 30].sort_values("RSI")
    elif filter_name == "RSI overbought":
        filtered = filtered[filtered["RSI"].fillna(0) >= 70].sort_values("RSI", ascending=False)
    elif filter_name in {"Breakout candidates", "Unusual volume"}:
        filtered = filtered[filtered["change_percent"].fillna(0) > 1.5].sort_values(["volume", "change_percent"], ascending=False)
    elif filter_name == "Telegram hype + technical confirmation":
        with SessionLocal() as db:
            hype_symbols = set(
                symbol for symbol in db.scalars(select(ExtractedSignal.stock_symbol).where(ExtractedSignal.hype_words.is_not(None))).all() if symbol
            )
        filtered = filtered[filtered["symbol"].isin(hype_symbols)]
    else:
        filtered = filtered.sort_values("volume", ascending=False)
    if search:
        mask = filtered["symbol"].astype(str).str.contains(search, case=False, na=False) | filtered["description"].astype(str).str.contains(search, case=False, na=False)
        filtered = filtered[mask]
    st.dataframe(filtered.head(limit), use_container_width=True, hide_index=True)
    export_buttons(filtered.head(limit), "tradingview_egx_screener")
    st.caption(f"TradingView usage may be unofficial and fragile. Disclaimer: {DISCLAIMER}")


def page_strategy_backtest() -> None:
    st.title("Strategy Backtest")
    c1, c2, c3, c4 = st.columns([1, 1, 1.2, 2])
    limit = c1.slider("Symbols", 5, 100, int(settings.strategy_symbol_limit))
    run_scan = c2.button("Run strategy scan")
    run_reviewed = c3.button("Run reviewed backtests")
    manual_symbols = c4.text_input("Symbols", placeholder="COMI, HRHO, TMGH")
    selected_symbols = [item.strip().upper() for item in manual_symbols.split(",") if item.strip()] if manual_symbols.strip() else None
    rows: list[dict[str, Any]] = []
    if run_scan:
        cached_strategy_universe.clear()
        with st.spinner("Running 15m, 1h, 4h, and 1D strategy frames..."):
            with SessionLocal() as db:
                data = run_strategy_universe(db, limit=len(selected_symbols) if selected_symbols else limit, symbols=selected_symbols)
        rows = data.get("rows", [])
        st.success(f"Strategy scan completed for {len(rows)} symbol(s).")
    if run_reviewed:
        with st.spinner("Running reviewed no-lookahead backtests and storing results..."):
            with SessionLocal() as db:
                reviewed = run_reviewed_universe_backtests(db, limit=limit, symbols=selected_symbols)
        st.success(f"Stored reviewed backtests for {len(reviewed.get('rows', []))} symbols.")

    if not rows:
        with SessionLocal() as db:
            stored = latest_strategy_results(db, symbol=selected_symbols[0] if selected_symbols and len(selected_symbols) == 1 else None, limit=limit * 2)
        grouped: dict[str, dict[str, Any]] = {}
        for row in stored:
            if row.get("strategy_code") != "strategy_legacy":
                continue
            symbol = row["symbol"]
            if symbol in grouped:
                continue
            details = row.get("details_json") or {}
            grouped[symbol] = {
                "symbol": symbol,
                "name": details.get("name"),
                "sector": details.get("sector"),
                "strategy_action": row.get("recommendation") or row.get("signal") or "NEUTRAL",
                "strategy_score": row.get("score") or 0,
                "buy_timeframes": details.get("buy_timeframes", 0),
                "watch_timeframes": details.get("watch_timeframes", 0),
                "available_timeframes": details.get("available_timeframes", 0),
                "reference_price": details.get("reference_price"),
                "reference_provider": details.get("reference_provider"),
                "data_quality": details.get("data_quality"),
                "uses_mock_data": details.get("uses_mock_data", False),
                "timeframes": details.get("timeframes", []),
            }
        rows = list(grouped.values())
    if not rows:
        st.info("No stored legacy strategy rows are available yet. Click Run strategy scan to create them.")
        return

    summary_rows = [
        {
            "symbol": row["symbol"],
            "name": row.get("name"),
            "sector": row.get("sector"),
            "strategy_action": row["strategy_action"],
            "strategy_score": row["strategy_score"],
            "buy_timeframes": row["buy_timeframes"],
            "watch_timeframes": row["watch_timeframes"],
            "available_timeframes": row["available_timeframes"],
            "reference_price": row.get("reference_price"),
            "reference_provider": row.get("reference_provider"),
            "data_quality": row.get("data_quality"),
            "uses_mock_data": row["uses_mock_data"],
        }
        for row in rows
    ]
    summary = pd.DataFrame(summary_rows)
    action_filter = st.multiselect("Action", ["BUY", "WATCH", "NEUTRAL", "AVOID"], default=["BUY", "WATCH"])
    filtered = summary[summary["strategy_action"].isin(action_filter)] if action_filter else summary
    st.dataframe(filtered, use_container_width=True, hide_index=True)
    export_buttons(filtered, "egx_strategy_backtest_summary")

    selected = st.selectbox("Strategy detail", filtered["symbol"].tolist() if not filtered.empty else summary["symbol"].tolist())
    selected_row = next(row for row in rows if row["symbol"] == selected)
    st.markdown(action_badge(selected_row["strategy_action"]), unsafe_allow_html=True)
    detail = pd.DataFrame(selected_row["timeframes"])
    detail_cols = [
        "timeframe",
        "action",
        "score",
        "trend",
        "last_price",
        "reference_price",
        "price_difference_percent",
        "entry",
        "stop",
        "target",
        "win_rate",
        "trades",
        "total_return_pct",
        "max_drawdown_pct",
        "provider",
        "data_quality",
        "is_mock",
        "as_of",
        "error",
    ]
    st.dataframe(detail[[col for col in detail_cols if col in detail.columns]], use_container_width=True, hide_index=True)

    st.subheader("Stored reviewed backtest")
    with SessionLocal() as db:
        stored_summaries = db.scalars(
            select(StrategyBacktestSummary)
            .where(StrategyBacktestSummary.symbol == selected)
            .order_by(StrategyBacktestSummary.score.desc())
        ).all()
        latest_backtest = db.scalar(
            select(StrategyBacktest)
            .where(StrategyBacktest.symbol == selected)
            .order_by(StrategyBacktest.completed_at.desc().nullslast(), StrategyBacktest.created_at.desc())
        )
        stored_trades = []
        if latest_backtest:
            stored_trades = db.scalars(
                select(StrategyBacktestTrade)
                .where(StrategyBacktestTrade.backtest_id == latest_backtest.id)
                .order_by(StrategyBacktestTrade.entry_time.desc())
                .limit(100)
            ).all()
    if stored_summaries:
        summary_df = to_df(stored_summaries)
        readable_cols = [
            "symbol",
            "strategy_name",
            "timeframe",
            "recommendation",
            "score",
            "win_rate",
            "profit_factor",
            "total_return",
            "max_drawdown",
            "trades_count",
            "latest_signal",
            "updated_at",
        ]
        professional_table(summary_df[[col for col in readable_cols if col in summary_df.columns]])
    else:
        st.info("No reviewed backtest summary stored for this symbol yet.")
    if latest_backtest and latest_backtest.equity_curve:
        curve = pd.DataFrame(latest_backtest.equity_curve)
        if not curve.empty and {"time", "equity"}.issubset(curve.columns):
            curve["time"] = pd.to_datetime(curve["time"])
            st.line_chart(curve.set_index("time")["equity"])
    if stored_trades:
        st.dataframe(to_df(stored_trades), use_container_width=True, hide_index=True)
    st.caption(f"Reviewed backtests enter at next-candle open and include commission/slippage. Disclaimer: {DISCLAIMER}")


def _latest_cli_v6_summary_df(limit: int = 500) -> pd.DataFrame:
    with SessionLocal() as db:
        rows = db.scalars(
            select(StrategyCliV6Result)
            .where(StrategyCliV6Result.timeframe == "summary")
            .order_by(StrategyCliV6Result.created_at.desc(), StrategyCliV6Result.id.desc())
            .limit(limit)
        ).all()
    df = to_df(rows)
    if df.empty:
        return df
    df = df.sort_values(["symbol", "created_at"], ascending=[True, False]).drop_duplicates("symbol", keep="first")
    return df.sort_values(["recommendation", "confidence", "created_at"], ascending=[True, False, False]).reset_index(drop=True)


def page_cli_v6_strategy() -> None:
    st.title("CLI v6 Strategy Results")
    c1, c2, c3 = st.columns([1, 1, 2])
    limit = c1.slider("Scan limit", 10, 300, int(settings.strategy_symbol_limit), key="cli_v6_scan_limit")
    run_all = c2.checkbox("All active stocks", value=True)
    manual_symbols = c3.text_input("Manual symbols", placeholder="COMI, HRHO, TMGH", key="cli_v6_symbols")
    if st.button("Run CLI v6 scan now"):
        selected = [item.strip().upper() for item in manual_symbols.split(",") if item.strip()] if manual_symbols.strip() else None
        with st.spinner("Running CLI v6 strategy on real OHLCV..."):
            with SessionLocal() as db:
                result = run_cli_v6_universe(db, symbols=selected, limit=None if run_all and not selected else limit)
        st.success(f"Run {result['run_id']} finished: {result['symbols_count']} symbol(s), status {result['status']}.")

    df = _latest_cli_v6_summary_df()
    if df.empty:
        st.info("No CLI v6 strategy rows are stored yet. Run the scan after importing TradingView/CSV OHLCV.")
        return

    strong_buy = int((df["recommendation"] == "STRONG BUY").sum())
    weak_buy = int((df["recommendation"] == "WEAK BUY").sum())
    strong_sell = int((df["recommendation"] == "STRONG SELL").sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Latest symbols", len(df))
    c2.metric("Strong buy", strong_buy)
    c3.metric("Weak buy", weak_buy)
    c4.metric("Strong sell", strong_sell)

    visible = [
        "symbol",
        "recommendation",
        "recommendation_ar",
        "confidence",
        "total_score",
        "bullish_count",
        "bearish_count",
        "neutral_count",
        "reason",
        "run_id",
        "created_at",
    ]
    st.dataframe(df[[col for col in visible if col in df.columns]], use_container_width=True, hide_index=True)
    export_buttons(df[[col for col in visible if col in df.columns]], "cli_v6_strategy_results")

    selected = st.selectbox("Symbol detail", df["symbol"].tolist(), key="cli_v6_detail")
    run_id = str(df[df["symbol"] == selected].iloc[0]["run_id"])
    with SessionLocal() as db:
        details = db.scalars(
            select(StrategyCliV6Result)
            .where(
                StrategyCliV6Result.symbol == selected,
                StrategyCliV6Result.run_id == run_id,
                StrategyCliV6Result.timeframe != "summary",
            )
            .order_by(StrategyCliV6Result.id.asc())
        ).all()
    detail_df = to_df(details)
    if not detail_df.empty:
        detail_cols = ["timeframe", "status", "total_score", "leading_score", "lagging_score", "recommendation", "confidence", "reason", "created_at"]
        st.dataframe(detail_df[[col for col in detail_cols if col in detail_df.columns]], use_container_width=True, hide_index=True)
    st.caption(f"CLI v6 uses completed candles only. Risk Note: {RISK_NOTE}")


def page_cli_v6_backtests() -> None:
    st.title("CLI v6 Backtest Results")
    c1, c2, c3 = st.columns([1, 1.2, 2])
    limit = c1.slider("Symbols", 5, 100, int(settings.strategy_symbol_limit), key="cli_v6_bt_limit")
    frames = c2.multiselect("Timeframes", ["15m", "1h", "4h", "1d"], default=["15m", "1h", "4h", "1d"])
    manual_symbols = c3.text_input("Manual symbols", placeholder="COMI, HRHO, TMGH", key="cli_v6_bt_symbols")
    if st.button("Run CLI v6 backtests"):
        selected = [item.strip().upper() for item in manual_symbols.split(",") if item.strip()] if manual_symbols.strip() else None
        with st.spinner("Running no-lookahead CLI v6 backtests..."):
            with SessionLocal() as db:
                result = run_cli_v6_backtest_universe(db, symbols=selected, timeframes=frames, limit=limit)
        st.success(f"Backtest run {result['run_id']} stored {len(result.get('rows', []))} row(s).")
        if result.get("errors"):
            st.warning(f"Skipped {len(result['errors'])} symbol/timeframe pair(s) due to missing or insufficient OHLCV.")

    with SessionLocal() as db:
        rows = db.scalars(
            select(StrategyBacktestSummary)
            .where(StrategyBacktestSummary.strategy_name == "CLI v6 EGX")
            .order_by(StrategyBacktestSummary.updated_at.desc())
            .limit(500)
        ).all()
    df = to_df(rows)
    if df.empty:
        st.info("No CLI v6 backtest summaries are stored yet.")
        return
    visible = [
        "symbol",
        "timeframe",
        "recommendation",
        "score",
        "total_return",
        "max_drawdown",
        "win_rate",
        "profit_factor",
        "trades_count",
        "avg_win",
        "avg_loss",
        "best_trade",
        "worst_trade",
        "latest_signal",
        "latest_recommendation",
        "run_id",
        "updated_at",
    ]
    st.dataframe(df[[col for col in visible if col in df.columns]], use_container_width=True, hide_index=True)
    export_buttons(df[[col for col in visible if col in df.columns]], "cli_v6_backtest_summary")

    selected = st.selectbox("Backtest trades", sorted(df["symbol"].dropna().unique().tolist()), key="cli_v6_bt_detail")
    with SessionLocal() as db:
        latest_backtest = db.scalar(
            select(StrategyBacktest)
            .where(StrategyBacktest.symbol == selected, StrategyBacktest.strategy_name == "CLI v6 EGX")
            .order_by(StrategyBacktest.completed_at.desc().nullslast(), StrategyBacktest.created_at.desc())
        )
        trades = []
        if latest_backtest:
            trades = db.scalars(
                select(StrategyBacktestTrade)
                .where(StrategyBacktestTrade.backtest_id == latest_backtest.id)
                .order_by(StrategyBacktestTrade.entry_time.desc())
                .limit(100)
            ).all()
    if latest_backtest and latest_backtest.equity_curve:
        curve = pd.DataFrame(latest_backtest.equity_curve)
        if not curve.empty and {"time", "equity"}.issubset(curve.columns):
            curve["time"] = pd.to_datetime(curve["time"])
            st.line_chart(curve.set_index("time")["equity"])
    if trades:
        st.dataframe(to_df(trades), use_container_width=True, hide_index=True)
    st.caption(f"Commission default is 0.0015 and slippage default is 0.002. Risk Note: {RISK_NOTE}")


def page_automation_monitor() -> None:
    st.title("Automation Monitor")
    with SessionLocal() as db:
        status = get_automation_status(db, settings=settings)
        runs = db.scalars(select(AutomationRun).order_by(AutomationRun.started_at.desc()).limit(50)).all()
        state_rows = db.scalars(select(AutomationState).order_by(AutomationState.key.asc())).all()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Enabled", "Yes" if status.get("enabled") else "No")
    c2.metric("Running", "Yes" if status.get("running") else "No")
    c3.metric("Interval seconds", status.get("interval_seconds"))
    c4.metric("Last alerts", status.get("last_alert_count") or 0)
    st.write(
        {
            "last_run": status.get("last_run_time"),
            "next_run": status.get("next_run_time"),
            "last_status": status.get("last_status"),
            "latest_error": status.get("last_error"),
        }
    )
    b1, b2, b3 = st.columns(3)
    if b1.button("Enable automation"):
        set_automation_enabled(True)
        st.success("Automation enabled.")
    if b2.button("Disable automation"):
        set_automation_enabled(False)
        st.success("Automation disabled.")
    if b3.button("Run one cycle now", disabled=bool(status.get("running"))):
        with st.spinner("Running one automation cycle..."):
            result = run_automation_cycle(settings=settings)
        st.success(f"Cycle {result.get('run_id')} finished with status {result.get('status')}.")

    st.subheader("Recent automation runs")
    runs_df = to_df(runs)
    if not runs_df.empty:
        visible = ["run_id", "started_at", "finished_at", "status", "duration_seconds", "symbols_processed", "opportunities_count", "alerts_sent", "error_message"]
        st.dataframe(runs_df[[col for col in visible if col in runs_df.columns]], use_container_width=True, hide_index=True)
    else:
        st.info("No automation runs have been stored yet.")
    st.subheader("Automation state")
    state_df = to_df(state_rows)
    if not state_df.empty:
        st.dataframe(state_df[["key", "value", "updated_at"]], use_container_width=True, hide_index=True)
    with SessionLocal() as db:
        alert_subscribers = db.query(TelegramSubscriber).filter(
            TelegramSubscriber.is_active.is_(True),
            TelegramSubscriber.can_receive_alerts.is_(True),
        ).count()
    st.caption(f"Active alert subscribers: {alert_subscribers}. Risk Note: {RISK_NOTE}")


def page_telegram_alerts_monitor() -> None:
    st.title("Telegram Alerts Monitor")
    with SessionLocal() as db:
        sent_alerts = db.scalars(select(TelegramSentAlert).order_by(TelegramSentAlert.sent_at.desc()).limit(200)).all()
        total_alerts = db.query(TelegramSentAlert).count()
        last_alert = sent_alerts[0] if sent_alerts else None
        alert_subscribers = db.query(TelegramSubscriber).filter(
            TelegramSubscriber.is_active.is_(True),
            TelegramSubscriber.can_receive_alerts.is_(True),
        ).count()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bot token configured", "Yes" if settings.telegram_bot_token else "No")
    c2.metric("Alert subscribers", alert_subscribers)
    c3.metric("Alerts sent", total_alerts)
    c4.metric("Latest alert", last_alert.sent_at.strftime("%Y-%m-%d %H:%M") if last_alert else "-")
    a1, a2 = st.columns(2)
    if a1.button("Send CLI v6 notifications now"):
        with SessionLocal() as db:
            result = send_strategy_notifications(db, settings=settings)
        st.success(f"Eligible {result.get('eligible', 0)}, sent {result.get('sent', 0)}, duplicates {result.get('skipped_duplicate', 0)}.")
    if a2.button("Send opportunity alerts now", key="alerts_monitor_opportunities"):
        with SessionLocal() as db:
            result = send_opportunity_buy_alerts(db, settings=settings)
        st.success(f"Eligible {result.get('eligible', 0)}, sent {result.get('sent', 0)}, duplicates {result.get('skipped_duplicate', 0)}.")
    df = to_df(sent_alerts)
    if df.empty:
        st.info("No Telegram alert rows are stored yet.")
    else:
        visible = ["symbol", "alert_type", "recommendation", "final_score", "alert_key", "sent_at"]
        st.dataframe(df[[col for col in visible if col in df.columns]], use_container_width=True, hide_index=True)
    st.caption(f"Chat IDs and tokens are intentionally hidden. Risk Note: {RISK_NOTE}")


def page_channels() -> None:
    st.title("Telegram Channels")
    refresh_now("Fetch all active channels now")
    with st.form("channels_form", clear_on_submit=True):
        left, right = st.columns([2, 1])
        usernames = left.text_area("Add channels", placeholder="@channel_one\n@channel_two")
        trust_score = right.slider("Trust score", 0, 100, 50)
        source_type = right.selectbox("Source type", ["channel", "group"])
        fetch_after_add = right.checkbox("Fetch after add", value=True)
        submitted = st.form_submit_button("Add / activate")
        if submitted and usernames.strip():
            added = 0
            with SessionLocal() as db:
                for raw in usernames.replace(",", "\n").splitlines():
                    username = raw.strip()
                    if not username:
                        continue
                    username = username if username.startswith("@") else f"@{username}"
                    source = db.scalar(select(TelegramSource).where(TelegramSource.username == username))
                    if source:
                        source.is_active = True
                        source.trust_score = float(trust_score)
                        source.source_type = source_type
                    else:
                        db.add(TelegramSource(username=username, title=username, source_type=source_type, trust_score=float(trust_score), is_active=True))
                    added += 1
                db.commit()
            st.success(f"Saved {added} channel(s).")
            if fetch_after_add:
                with st.spinner("Fetching new channel data now..."):
                    try:
                        result = run_ingestion_cycle()
                        cached_recommendations.clear()
                        cached_tradingview_rows.clear()
                        cached_strategy_universe.clear()
                        st.success(ingestion_result_text(result))
                    except Exception as exc:
                        st.warning(f"Saved channels, but immediate fetch failed: {exc}")

    with st.expander("Import channels from Excel or CSV", expanded=False):
        st.write("Accepted columns: username, channel, source, link, title, trust_score, source_type, is_active, notes.")
        source_file = st.file_uploader("Sources file", type=["xlsx", "xls", "csv"], key="source_import")
        fetch_after_import = st.checkbox("Fetch after import", value=True, key="source_import_fetch")
        if source_file and st.button("Import sources"):
            try:
                df_import = read_sources_file(source_file.name, source_file.getvalue())
                with SessionLocal() as db:
                    import_result = import_sources_from_df(db, df_import)
                st.success(
                    f"Imported sources. Inserted: {import_result.inserted}, updated: {import_result.updated}, skipped: {import_result.skipped}."
                )
                if fetch_after_import:
                    with st.spinner("Fetching imported sources now..."):
                        result = run_ingestion_cycle()
                        cached_recommendations.clear()
                        cached_tradingview_rows.clear()
                        cached_strategy_universe.clear()
                        st.success(ingestion_result_text(result))
            except Exception as exc:
                st.error(f"Import failed: {exc}")

    df = read_rows(TelegramSource, TelegramSource.username)
    if df.empty:
        st.info("No channels configured.")
        return
    editable_cols = ["username", "title", "source_type", "is_active", "trust_score", "last_message_id", "notes"]
    edited = st.data_editor(
        df[editable_cols],
        disabled=["username", "last_message_id"],
        use_container_width=True,
        hide_index=True,
        column_config={
            "is_active": st.column_config.CheckboxColumn("Active"),
            "trust_score": st.column_config.NumberColumn("Trust", min_value=0, max_value=100),
        },
    )
    c1, c2 = st.columns([1, 2])
    if c1.button("Save edits"):
        with SessionLocal() as db:
            for idx, row in edited.reset_index(drop=True).iterrows():
                source = db.get(TelegramSource, int(df.iloc[idx]["id"]))
                if source:
                    source.title = row.get("title")
                    source.source_type = row.get("source_type") or "channel"
                    source.is_active = bool(row.get("is_active"))
                    source.trust_score = float(row.get("trust_score") or 50)
                    source.notes = row.get("notes")
            db.commit()
        st.success("Saved.")
    delete_options = {
        f"{row['title'] or row['username']} ({row['username']})": int(row["id"])
        for _, row in df.iterrows()
    }
    delete_label = c2.selectbox("Delete channel", [""] + list(delete_options.keys()))
    if delete_label and c2.button("Delete selected"):
        with SessionLocal() as db:
            source = db.get(TelegramSource, delete_options[delete_label])
            if source:
                db.delete(source)
                db.commit()
        st.success("Deleted.")


def page_messages() -> None:
    st.title("Telegram Messages")
    df = read_rows(TelegramMessage, TelegramMessage.created_at.desc(), limit=1000)
    if df.empty:
        st.info("No messages yet.")
        return
    df = with_channel_names(df)
    search = st.text_input("Search messages")
    if search:
        df = df[df["text"].astype(str).str.contains(search, case=False, na=False)]
    columns = ["channel", "message_id", "message_date", "parsed", "image_path", "text"]
    st.dataframe(df[columns], use_container_width=True, hide_index=True)


def page_signals() -> None:
    st.title("Signals And Analysis")
    signals = with_channel_names(read_rows(ExtractedSignal, ExtractedSignal.created_at.desc(), limit=1000))
    analyses = with_channel_names(read_rows(FinalAnalysis, FinalAnalysis.created_at.desc(), limit=1000))
    tab1, tab2 = st.tabs(["Extracted signals", "Final analysis"])
    with tab1:
        if signals.empty:
            st.info("No signals yet.")
        else:
            visible_cols = [col for col in signals.columns if col not in {"id", "telegram_message_id"}]
            st.dataframe(signals[visible_cols], use_container_width=True, hide_index=True)
            export_buttons(signals[visible_cols], "extracted_signals")
    with tab2:
        if analyses.empty:
            st.info("No analyses yet.")
        else:
            visible_cols = [col for col in analyses.columns if col not in {"id", "technical_analysis_id", "extracted_signal_id"}]
            st.dataframe(analyses[visible_cols], use_container_width=True, hide_index=True)
            export_buttons(analyses[visible_cols], "final_analysis")


def page_chart_images() -> None:
    st.title("Chart Images")
    db_images = with_channel_names(read_rows(TelegramMessage, TelegramMessage.created_at.desc(), limit=1000))
    if not db_images.empty:
        db_images = db_images[db_images["image_path"].notna()]
    if st.button("Analyze saved image files"):
        results = analyze_existing_images()
        st.session_state["image_scan_results"] = results
    results = st.session_state.get("image_scan_results", [])
    if results:
        st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
    elif db_images.empty:
        st.info("No Telegram images have been saved yet.")
    else:
        st.dataframe(db_images[["channel", "message_id", "image_path", "image_metadata", "created_at"]], use_container_width=True, hide_index=True)
    st.caption("Image analysis stores metadata and chart-likelihood only; OCR/AI vision is prepared as a future integration.")


def page_manual_analyze() -> None:
    st.title("Manual Analyze Stock")
    with st.form("manual_analyze"):
        col1, col2, col3, col4 = st.columns(4)
        symbol = col1.text_input("Symbol", value="COMI").upper()
        direction = col2.selectbox("Direction", ["WATCH", "BUY", "SELL", "HOLD", "AVOID"])
        entry = col3.number_input("Entry", min_value=0.0, value=0.0)
        stop = col4.number_input("Stop loss", min_value=0.0, value=0.0)
        targets_text = st.text_input("Targets", placeholder="12.5, 13.2")
        submitted = st.form_submit_button("Analyze")
    if submitted and symbol:
        targets = [float(item.strip()) for item in targets_text.split(",") if item.strip()]
        with SessionLocal() as db:
            final = analyze_symbol_manually(
                db,
                symbol=symbol,
                direction=direction,
                entry_price=entry or None,
                stop_loss=stop or None,
                targets=targets,
            )
            payload = {
                "Symbol": final.symbol,
                "Input Direction": direction,
                "Decision": final.final_decision,
                "Confidence": f"{final.confidence_score:.0f}%",
                "Last Price": final.last_price if final.last_price is not None else "-",
                "Trend": final.trend or "-",
                "Entry": final.entry_zone or "-",
                "Stop Loss": final.stop_loss if final.stop_loss is not None else "-",
                "Targets": ", ".join(f"{target:.2f}" for target in (final.targets or [])) or "-",
            }
            key_value_table(payload)
            if final.reasons:
                st.markdown("**Reasons**")
                for reason in final.reasons:
                    st.write(f"- {reason}")
            if final.warnings:
                st.markdown("**Warnings**")
                for warning in final.warnings:
                    st.warning(warning)
            with st.expander("Telegram message preview"):
                st.write(format_alert(final))


def page_performance() -> None:
    st.title("Channel Performance")
    if st.button("Update performance"):
        with SessionLocal() as db:
            update_channel_performance(db)
        st.success("Performance refreshed.")
    df = read_rows(ChannelPerformance, ChannelPerformance.updated_at.desc(), limit=500)
    if df.empty:
        st.info("No performance rows yet.")
    else:
        df = with_channel_names(df)
        visible_cols = [col for col in df.columns if col != "id"]
        st.dataframe(df[visible_cols], use_container_width=True, hide_index=True)


def page_market_depth() -> None:
    st.title("Market Depth")
    data = build_market_depth_screener(settings=settings, limit=250)
    if data["status"] == "empty":
        st.info(data["warning"])
        st.caption("CSV columns: timestamp, source, symbol, side, level, price, quantity, num_orders. Side must be bid or ask.")
        return
    if data["status"] == "error":
        st.warning(data["warning"])
        return
    rows = data.get("rows", [])
    if not rows:
        st.info("No complete bid/ask symbols found.")
        return
    df = pd.DataFrame(rows)
    c1, c2, c3 = st.columns(3)
    signals = sorted(df["depth_signal"].dropna().unique())
    signal_filter = c1.multiselect("Depth signal", signals, default=signals)
    max_spread = c2.slider("Max spread %", 0.0, 10.0, 3.0, 0.1)
    search = c3.text_input("Search symbol", key="market_depth_symbol_search")
    filtered = df.copy()
    if signal_filter:
        filtered = filtered[filtered["depth_signal"].isin(signal_filter)]
    filtered = filtered[filtered["spread_pct"].fillna(99) <= max_spread]
    if search:
        filtered = filtered[filtered["symbol"].astype(str).str.contains(search, case=False, na=False)]
    st.dataframe(filtered, use_container_width=True, hide_index=True)
    export_buttons(filtered, "egx_market_depth_screener")
    st.caption(f"Depth rows come from files in {settings.market_depth_data_dir}. Use official/exported Thndr/Telda depth only. Disclaimer: {DISCLAIMER}")


def page_telegram_bot() -> None:
    st.title("Telegram Bot")
    with SessionLocal() as db:
        approved_bot_users = db.query(BotUser).filter(BotUser.is_active.is_(True)).count()
        pending_bot_users = db.query(BotUser).filter(BotUser.is_active.is_(False)).count()
        sent_alerts = db.query(TelegramSentAlert).count()
        last_alert = db.scalar(select(TelegramSentAlert).order_by(TelegramSentAlert.sent_at.desc()))
        users_df = to_df(db.scalars(select(BotUser).order_by(BotUser.updated_at.desc()).limit(100)).all())
        alert_subscribers = db.query(TelegramSubscriber).filter(
            TelegramSubscriber.is_active.is_(True),
            TelegramSubscriber.can_receive_alerts.is_(True),
        ).count()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Token configured", "Yes" if settings.telegram_bot_token else "No")
    c2.metric("Alert subscribers", alert_subscribers)
    c3.metric("Approved users", approved_bot_users)
    c4.metric("Pending users", pending_bot_users)

    if not settings.telegram_bot_token:
        st.markdown('<div class="banner warn">TELEGRAM_BOT_TOKEN is missing from .env.</div>', unsafe_allow_html=True)
    if alert_subscribers <= 0:
        st.markdown('<div class="banner warn">No active Telegram alert subscribers. Use the Telegram Users page or /subscribe to activate alerts.</div>', unsafe_allow_html=True)

    left, right = st.columns([1, 1])
    if left.button("Send test message"):
        try:
            from app.services.telegram_bot import send_private_message_sync

            send_private_message_sync("EGX bot test message. Automation is connected.", settings=settings)
            st.success("Test message sent.")
        except Exception as exc:
            st.error(f"Test message failed: {exc}")
    if right.button("Send opportunity alerts now"):
        with st.spinner("Checking unsent BUY opportunities..."):
            with SessionLocal() as db:
                result = send_opportunity_buy_alerts(db)
        st.success(
            f"Eligible {result.get('eligible', 0)}, sent {result.get('sent', 0)}, "
            f"duplicates {result.get('skipped_duplicate', 0)}."
        )

    st.subheader("Bot commands")
    commands = pd.DataFrame(
        [
            {"command": "/analysis SYMBOL", "scope": "stock"},
            {"command": "/financial SYMBOL", "scope": "financial"},
            {"command": "/news SYMBOL", "scope": "news"},
            {"command": "/opportunities", "scope": "market"},
            {"command": "/market", "scope": "market"},
            {"command": "/latest", "scope": "recommendations"},
            {"command": "/backtest SYMBOL", "scope": "strategy"},
            {"command": "/strategy SYMBOL", "scope": "strategy"},
            {"command": "/screening", "scope": "TradingView"},
            {"command": "/alerts", "scope": "admin"},
            {"command": "/watchlist", "scope": "market"},
            {"command": "/id", "scope": "access"},
            {"command": "/status", "scope": "system"},
        ]
    )
    st.dataframe(commands, use_container_width=True, hide_index=True)

    st.subheader("Users")
    if users_df.empty:
        st.info("No bot users have contacted the bot yet.")
    else:
        visible = ["chat_id", "username", "is_active", "created_at", "updated_at"]
        st.dataframe(users_df[visible], use_container_width=True, hide_index=True)
    st.caption(f"Sent opportunity alerts: {sent_alerts}. Last alert: {last_alert.sent_at if last_alert else '-'}. Disclaimer: {DISCLAIMER}")


def page_settings() -> None:
    st.title("Settings")
    with SessionLocal() as db:
        approved_bot_users = db.query(BotUser).filter(BotUser.is_active.is_(True)).count()
        pending_bot_users = db.query(BotUser).filter(BotUser.is_active.is_(False)).count()
        alert_subscribers = db.query(TelegramSubscriber).filter(
            TelegramSubscriber.is_active.is_(True),
            TelegramSubscriber.can_receive_alerts.is_(True),
        ).count()
        listener_status = db.scalar(select(AppSetting.value).where(AppSetting.key == "telegram_listener_status"))
    alert_status = {
        "TELEGRAM_ALERT_ENABLED": settings.telegram_alert_enabled,
        "TELEGRAM_ALERT_CONFIGURED": alerts_configured(settings),
        "TELEGRAM_BOT_TOKEN_CONFIGURED": bool(settings.telegram_bot_token),
        "TELEGRAM_ACTIVE_ALERT_SUBSCRIBERS": alert_subscribers,
        "TELEGRAM_BOT_EMBEDDED_ENABLED": settings.telegram_bot_embedded_enabled,
        "TELEGRAM_BOT_VERIFY_TLS": settings.telegram_bot_verify_tls,
        "BOT_ADMIN_CHAT_COUNT": len(settings.allowed_chat_ids),
        "BOT_APPROVED_USER_COUNT": approved_bot_users,
        "BOT_PENDING_USER_COUNT": pending_bot_users,
        "TELEGRAM_LISTENER_STATUS": listener_status or "not run yet",
        "TELEGRAM_ALERT_DECISIONS": sorted(settings.alert_decision_set),
        "TELEGRAM_ALERT_MIN_CONFIDENCE": settings.telegram_alert_min_confidence,
        "TELEGRAM_ALERT_RECOMMENDATIONS_ENABLED": settings.telegram_alert_recommendations_enabled,
        "TELEGRAM_ALERT_REQUIRE_TELEGRAM_CONFIRMATION": settings.telegram_alert_require_telegram_confirmation,
        "TELEGRAM_ALERT_SCAN_INTERVAL_MINUTES": settings.telegram_alert_scan_interval_minutes,
        "NIGHT_OPPORTUNITY_REPORT_ENABLED": settings.night_opportunity_report_enabled,
        "NIGHT_OPPORTUNITY_REPORT_HOUR": settings.night_opportunity_report_hour,
        "NIGHT_OPPORTUNITY_TOP_N": settings.night_opportunity_top_n,
        "MARKET_DATA_PROVIDER_PRIORITY": settings.market_data_provider_priority,
        "MARKET_DATA_ALLOW_MOCK": settings.market_data_allow_mock,
        "TRADINGVIEW_WS_URL": settings.tradingview_ws_url,
        "STRATEGY_ALLOW_MOCK_DATA": settings.strategy_allow_mock_data,
        "STRATEGY_PRICE_TOLERANCE_PERCENT": settings.strategy_price_tolerance_percent,
        "OPPORTUNITY_WEIGHT_RECOMMENDATION": settings.opportunity_weight_recommendation,
        "OPPORTUNITY_WEIGHT_STRATEGY": settings.opportunity_weight_strategy,
        "OPPORTUNITY_WEIGHT_BACKTEST": settings.opportunity_weight_backtest,
        "OPPORTUNITY_WEIGHT_TRADINGVIEW": settings.opportunity_weight_tradingview,
        "OPPORTUNITY_WEIGHT_TELEGRAM": settings.opportunity_weight_telegram,
    }
    st.subheader("Telegram BUY Notifications")
    if alert_status["TELEGRAM_ALERT_CONFIGURED"]:
        st.markdown('<div class="banner ok">Telegram BUY notifications are configured and active.</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            '<div class="banner warn">Telegram BUY notifications need TELEGRAM_BOT_TOKEN and at least one active Telegram subscriber/admin user.</div>',
            unsafe_allow_html=True,
        )
    c1, c_mid, c_night, c2 = st.columns([1, 1, 1, 2])
    if c1.button("Send pending BUY alerts now"):
        with st.spinner("Sending unsent BUY alerts..."):
            with SessionLocal() as db:
                signal_alerts = send_pending_buy_signal_alerts(db)
                recommendation_alerts = send_buy_recommendation_alerts(db)
        st.success(
            f"{_alert_result_text('Signal alerts', signal_alerts)}. "
            f"{_alert_result_text('Recommendation alerts', recommendation_alerts)}."
        )
    if c_mid.button("Send daily report now"):
        with st.spinner("Building and sending daily report..."):
            report_result = send_daily_report(settings=settings)
        st.success(f"Daily report sent. Length: {report_result['length']} characters.")
    if c_night.button("Send night opportunities now"):
        with st.spinner("Building and sending night opportunities..."):
            report_result = send_night_opportunity_report(settings=settings)
        st.success(f"Night opportunity report sent. Length: {report_result['length']} characters.")
    with c2:
        key_value_table(alert_status)

    st.subheader("Application")
    safe_settings = {
        "APP_ENV": settings.app_env,
        "EGX_DATABASE_URL": settings.database_url,
        "MARKET_DATA_PROVIDER_PRIORITY": settings.market_data_provider_priority,
        "MARKET_DATA_ALLOW_MOCK": settings.market_data_allow_mock,
        "ALLOW_INSECURE_MARKET_DATA_TLS": settings.allow_insecure_market_data_tls,
        "TRADINGVIEW_WS_URL": settings.tradingview_ws_url,
        "CSV_DATA_DIR": settings.csv_data_dir,
        "CSV_OHLCV_SAMPLE_PATH": settings.csv_ohlcv_sample_path,
        "MARKET_DEPTH_DATA_DIR": settings.market_depth_data_dir,
        "SCHEDULER_ENABLED": settings.scheduler_enabled,
        "TELEGRAM_FETCH_INTERVAL_MINUTES": settings.telegram_fetch_interval_minutes,
        "ANALYSIS_INTERVAL_MINUTES": settings.analysis_interval_minutes,
        "TELEGRAM_BOT_VERIFY_TLS": settings.telegram_bot_verify_tls,
        "TELEGRAM_BOT_EMBEDDED_ENABLED": settings.telegram_bot_embedded_enabled,
        "TELEGRAM_ALERT_SCAN_INTERVAL_MINUTES": settings.telegram_alert_scan_interval_minutes,
        "DAILY_REPORT_HOUR": settings.daily_report_hour,
        "DAILY_REPORT_TOP_N": settings.daily_report_top_n,
        "DAILY_REPORT_INCLUDE_STRATEGY": settings.daily_report_include_strategy,
        "NIGHT_OPPORTUNITY_REPORT_ENABLED": settings.night_opportunity_report_enabled,
        "NIGHT_OPPORTUNITY_REPORT_HOUR": settings.night_opportunity_report_hour,
        "NIGHT_OPPORTUNITY_TOP_N": settings.night_opportunity_top_n,
        "STRATEGY_TIMEFRAMES": settings.strategy_timeframes,
        "STRATEGY_SYMBOL_LIMIT": settings.strategy_symbol_limit,
        "STRATEGY_BACKTEST_BARS": settings.strategy_backtest_bars,
        "STRATEGY_ALLOW_MOCK_DATA": settings.strategy_allow_mock_data,
        "STRATEGY_MAX_DAILY_AGE_DAYS": settings.strategy_max_daily_age_days,
        "STRATEGY_MAX_INTRADAY_AGE_DAYS": settings.strategy_max_intraday_age_days,
        "STRATEGY_PRICE_TOLERANCE_PERCENT": settings.strategy_price_tolerance_percent,
        "OPPORTUNITY_WEIGHT_RECOMMENDATION": settings.opportunity_weight_recommendation,
        "OPPORTUNITY_WEIGHT_STRATEGY": settings.opportunity_weight_strategy,
        "OPPORTUNITY_WEIGHT_BACKTEST": settings.opportunity_weight_backtest,
        "OPPORTUNITY_WEIGHT_TRADINGVIEW": settings.opportunity_weight_tradingview,
        "OPPORTUNITY_WEIGHT_TELEGRAM": settings.opportunity_weight_telegram,
        "DISCLAIMER": DISCLAIMER,
    }
    settings_df = pd.DataFrame([{"Setting": key, "Value": "" if value is None else str(value)} for key, value in safe_settings.items()])
    professional_table(settings_df)


def page_telegram_users() -> None:
    st.title("Telegram Users")
    with SessionLocal() as db:
        rows = db.scalars(select(TelegramSubscriber).order_by(TelegramSubscriber.updated_at.desc())).all()
        total = len(rows)
        active = sum(1 for row in rows if row.is_active)
        alert_users = sum(1 for row in rows if row.is_active and row.can_receive_alerts)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Subscribers", total)
    c2.metric("Active", active)
    c3.metric("Alert receivers", alert_users)
    c4.metric("Bot token", "Configured" if settings.telegram_bot_token else "Missing")

    with st.form("telegram_subscriber_add", clear_on_submit=True):
        a1, a2, a3 = st.columns([1.2, 1.4, 1])
        chat_id = a1.text_input("Chat ID")
        display_name = a2.text_input("Display name")
        role = a3.selectbox("Role", ["user", "admin"])
        notes = st.text_area("Notes", height=70)
        submitted = st.form_submit_button("Add / update user")
        if submitted and chat_id.strip():
            with SessionLocal() as db:
                row = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == chat_id.strip()))
                if row:
                    row.display_name = display_name or row.display_name
                    row.role = role
                    row.notes = notes or row.notes
                    row.is_active = True
                    row.can_use_bot = True
                else:
                    db.add(
                        TelegramSubscriber(
                            chat_id=chat_id.strip(),
                            display_name=display_name or "Manual subscriber",
                            role=role,
                            is_active=True,
                            can_receive_alerts=True,
                            can_use_bot=True,
                            notes=notes,
                        )
                    )
                db.commit()
            st.success("Telegram user saved.")

    df = to_df(rows)
    if df.empty:
        st.info("No Telegram subscribers yet. They will appear after /start or /subscribe.")
        return
    editable_cols = [
        "chat_id",
        "display_name",
        "username",
        "role",
        "is_active",
        "can_receive_alerts",
        "can_use_bot",
        "allowed_symbols",
        "notes",
        "last_message_status",
        "last_message_error",
        "last_message_at",
        "updated_at",
    ]
    edited = st.data_editor(
        df[[col for col in editable_cols if col in df.columns]],
        disabled=["chat_id", "username", "last_message_status", "last_message_error", "last_message_at", "updated_at"],
        use_container_width=True,
        hide_index=True,
        column_config={
            "is_active": st.column_config.CheckboxColumn("Active"),
            "can_receive_alerts": st.column_config.CheckboxColumn("Alerts"),
            "can_use_bot": st.column_config.CheckboxColumn("Bot access"),
        },
    )
    s1, s2, s3 = st.columns(3)
    if s1.button("Save user edits"):
        with SessionLocal() as db:
            for idx, row in edited.reset_index(drop=True).iterrows():
                subscriber = db.get(TelegramSubscriber, int(df.iloc[idx]["id"]))
                if subscriber:
                    subscriber.display_name = row.get("display_name")
                    subscriber.role = row.get("role") or "user"
                    subscriber.is_active = bool(row.get("is_active"))
                    subscriber.can_receive_alerts = bool(row.get("can_receive_alerts"))
                    subscriber.can_use_bot = bool(row.get("can_use_bot"))
                    subscriber.allowed_symbols = row.get("allowed_symbols")
                    subscriber.notes = row.get("notes")
            db.commit()
        st.success("Saved user edits.")

    labels = {f"{row.get('display_name') or row.get('username') or row.get('chat_id')} ({row.get('chat_id')})": row.get("chat_id") for _, row in df.iterrows()}
    selected_label = s2.selectbox("Test user", [""] + list(labels.keys()))
    if selected_label and s2.button("Send test"):
        chat_id = labels[selected_label]
        try:
            send_message_to_chat_sync(chat_id, "EGX subscriber test message.")
            with SessionLocal() as db:
                sub = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == str(chat_id)))
                if sub:
                    sub.last_message_status = "ok"
                    sub.last_message_at = pd.Timestamp.utcnow().to_pydatetime()
                    db.commit()
            st.success("Test message sent.")
        except Exception as exc:
            st.error(f"Test failed: {exc}")
    if s3.button("Broadcast test to active users"):
        result = send_alert_to_subscribers_sync("EGX broadcast test message.")
        st.success(f"Eligible {result.get('eligible', 0)}, sent {result.get('sent', 0)}, failed {result.get('failed', 0)}.")

    deactivate_label = st.selectbox("Deactivate user", [""] + list(labels.keys()), key="telegram_user_deactivate")
    if deactivate_label and st.button("Deactivate selected user"):
        with SessionLocal() as db:
            row = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == str(labels[deactivate_label])))
            if row:
                row.is_active = False
                row.can_receive_alerts = False
                db.commit()
        st.success("User deactivated.")
    st.caption("Bot tokens and secret values are never displayed here.")


def page_automation_control() -> None:
    st.title("Automation Control")
    with SessionLocal() as db:
        snapshot = automation_snapshot(db, settings=settings)
        status = get_automation_status(db, settings=settings)
        runs = db.scalars(select(AutomationRun).order_by(AutomationRun.started_at.desc()).limit(75)).all()
        state_rows = db.scalars(select(AutomationState).order_by(AutomationState.key.asc())).all()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Enabled", "Yes" if snapshot["enabled"] else "No")
    c2.metric("Running", "Yes" if status.get("running") else "No")
    c3.metric("Interval", f"{snapshot['interval_seconds']} sec")
    c4.metric("Last alerts", status.get("last_alert_count") or 0)

    with st.form("automation_settings_form"):
        cols = st.columns(3)
        enabled = cols[0].toggle("Enable automation", value=bool(snapshot["enabled"]))
        interval = cols[1].number_input("Interval seconds", min_value=60, value=int(snapshot["interval_seconds"]), step=30)
        backtest_mode = cols[2].selectbox(
            "Backtest frequency",
            ["manual_only", "hourly", "daily", "opportunities_only"],
            index=["manual_only", "hourly", "daily", "opportunities_only"].index(str(snapshot["backtest_mode"])),
        )
        flags = {
            "automation_fetch_telegram": st.checkbox("Telegram fetch", value=bool(snapshot["fetch_telegram"])),
            "automation_run_tradingview": st.checkbox("TradingView screening", value=bool(snapshot["run_tradingview"])),
            "automation_fetch_financial_data": st.checkbox("Financial snapshots", value=bool(snapshot.get("fetch_financial_data", True))),
            "automation_fetch_news_data": st.checkbox("News RSS", value=bool(snapshot.get("fetch_news_data", True))),
            "automation_fetch_ohlcv_data": st.checkbox("OHLCV candles", value=bool(snapshot.get("fetch_ohlcv_data", True))),
            "automation_run_strategy_legacy": st.checkbox("Strategy 1 legacy", value=bool(snapshot["run_strategy_legacy"])),
            "automation_run_cli_v6": st.checkbox("Strategy 2 CLI v6", value=bool(snapshot["run_cli_v6"])),
            "automation_run_final_decisions": st.checkbox("Final decision engine", value=bool(snapshot.get("run_final_decisions", True))),
            "automation_run_opportunities": st.checkbox("Opportunity engine", value=bool(snapshot["run_opportunities"])),
            "automation_update_accuracy": st.checkbox("Accuracy learning", value=bool(snapshot.get("update_accuracy", True))),
            "automation_run_portfolio_bot": st.checkbox("Portfolio bot scan", value=bool(snapshot.get("run_portfolio_bot", False))),
            "portfolio_bot_auto_execute_paper_trades": st.checkbox(
                "Portfolio auto paper execution",
                value=bool(snapshot.get("portfolio_auto_execute", False)),
            ),
            "automation_send_alerts": st.checkbox("Telegram alerts", value=bool(snapshot["send_alerts"])),
            "telegram_analyze_images": st.checkbox("Image analysis/OCR", value=bool(snapshot["telegram_analyze_images"])),
            "telegram_download_media": st.checkbox("Download image media", value=bool(snapshot["telegram_download_media"])),
        }
        queue_limit = st.number_input("Backtest queue limit", min_value=1, max_value=50, value=int(snapshot["backtest_queue_limit"]))
        portfolio_limit = st.number_input(
            "Portfolio scan symbol limit",
            min_value=1,
            max_value=500,
            value=int(snapshot.get("portfolio_symbol_limit", 50)),
        )
        saved = st.form_submit_button("Save automation settings")
        if saved:
            with SessionLocal() as db:
                set_setting(db, "automation_enabled", "true" if enabled else "false", "bool")
                set_setting(db, "automation_interval_seconds", int(interval), "int")
                set_setting(db, "backtest_mode", backtest_mode, "string")
                set_setting(db, "backtest_queue_limit", int(queue_limit), "int")
                set_setting(db, "portfolio_bot_symbol_limit", int(portfolio_limit), "int")
                for key, value in flags.items():
                    set_setting(db, key, "true" if value else "false", "bool")
            st.success("Automation settings saved.")

    b1, b2, b3, b4, b5 = st.columns(5)
    if b1.button("Run one cycle now", disabled=bool(status.get("running"))):
        result = run_automation_cycle(settings=settings)
        st.success(f"Cycle {result.get('run_id')} finished: {result.get('status')}.")
    if b2.button("Telegram fetch only"):
        result = run_automation_cycle(settings=settings, task_filter="telegram_fetch", no_alerts=True, skip_backtests=True)
        st.success(f"Telegram task finished: {result.get('status')}.")
    if b3.button("Strategies only"):
        result = run_automation_cycle(settings=settings, task_filter="strategies", no_alerts=True, skip_backtests=True)
        st.success(f"Strategy task finished: {result.get('status')}.")
    if b4.button("Opportunities only"):
        result = run_automation_cycle(settings=settings, task_filter="opportunities", no_alerts=True, skip_backtests=True)
        st.success(f"Opportunity task finished: {result.get('status')}.")
    if b5.button("Send alerts now"):
        result = run_automation_cycle(settings=settings, task_filter="alerts", skip_backtests=True)
        st.success(f"Alerts task finished: {result.get('status')}.")
    c6, c7, c8, c9 = st.columns(4)
    if c6.button("Final decisions only"):
        result = run_automation_cycle(settings=settings, task_filter="final_decisions", no_alerts=True, skip_backtests=True)
        st.success(f"Final decision task finished: {result.get('status')}.")
    if c7.button("Accuracy learning only"):
        result = run_automation_cycle(settings=settings, task_filter="accuracy_learning", no_alerts=True, skip_backtests=True)
        st.success(f"Accuracy task finished: {result.get('status')}.")
    if c8.button("Portfolio scan only"):
        result = run_automation_cycle(settings=settings, task_filter="portfolio_bot", no_alerts=True, skip_backtests=True)
        st.success(f"Portfolio task finished: {result.get('status')}.")
    if c9.button("Dynamic data only"):
        result = run_automation_cycle(settings=settings, task_filter="dynamic_data", no_alerts=True, skip_backtests=True)
        st.success(f"Dynamic data task finished: {result.get('status')}.")

    runs_df = to_df(runs)
    st.subheader("Automation Runs")
    if runs_df.empty:
        st.info("No automation runs yet.")
    else:
        visible = [
            "run_id",
            "started_at",
            "finished_at",
            "status",
            "duration_seconds",
            "telegram_fetch_status",
            "strategy_status",
            "backtest_status",
            "opportunity_status",
            "symbols_processed",
            "opportunities_count",
            "alerts_sent",
            "error_message",
        ]
        st.dataframe(runs_df[[col for col in visible if col in runs_df.columns]], use_container_width=True, hide_index=True)
    state_df = to_df(state_rows)
    if not state_df.empty:
        with st.expander("Automation state"):
            st.dataframe(state_df[["key", "value", "updated_at"]], use_container_width=True, hide_index=True)


def page_strategies() -> None:
    st.title("Strategies")
    with SessionLocal() as db:
        strategies = pd.DataFrame(list_strategies(db))
    if strategies.empty:
        st.info("No strategies are registered.")
        return
    edited = st.data_editor(
        strategies[["strategy_code", "strategy_name", "description", "is_enabled", "default_timeframe", "updated_at"]],
        disabled=["strategy_code", "strategy_name", "description", "default_timeframe", "updated_at"],
        use_container_width=True,
        hide_index=True,
        column_config={"is_enabled": st.column_config.CheckboxColumn("Enabled")},
    )
    if st.button("Save strategy toggles"):
        with SessionLocal() as db:
            for _, row in edited.iterrows():
                set_strategy_enabled(db, row["strategy_code"], bool(row["is_enabled"]))
        st.success("Strategy toggles saved.")

    s1, s2, s3 = st.columns([1.2, 1.2, 2])
    selected_strategy = s1.selectbox("Run strategy", strategies["strategy_code"].tolist())
    symbol = s2.text_input("Symbol", value="COMI").upper()
    if s3.button("Run selected strategy"):
        with SessionLocal() as db:
            result = run_registered_strategy(selected_strategy, symbol=symbol, db=db, settings=settings)
        st.success(f"Run {result.get('run_id')} stored {len(result.get('rows', []))} summary row(s).")
    if s3.button("Run all enabled strategies"):
        with SessionLocal() as db:
            result = run_all_enabled_strategies(symbol=symbol, db=db, settings=settings)
        st.success(f"Run {result.get('run_id')} completed with {len(result.get('rows', []))} row(s).")

    with SessionLocal() as db:
        rows = pd.DataFrame(latest_strategy_results(db, symbol=symbol if symbol else None, limit=500))
    if rows.empty:
        st.info("No shared strategy results yet.")
    else:
        visible = ["strategy_code", "strategy_name", "symbol", "recommendation", "score", "confidence", "trend", "reason", "run_id", "created_at"]
        st.dataframe(rows[[col for col in visible if col in rows.columns]], use_container_width=True, hide_index=True)


def page_backtest_queue() -> None:
    st.title("Backtest Queue")
    q1, q2, q3 = st.columns([1, 2, 1])
    symbol = q1.text_input("Queue symbol", value="COMI").upper()
    reason = q2.text_input("Reason", value="Manual dashboard request")
    priority = q3.number_input("Priority", min_value=1, max_value=10, value=3)
    if st.button("Add to queue"):
        with SessionLocal() as db:
            enqueue_backtest(db, symbol, reason=reason, priority=int(priority), requested_by="dashboard")
            db.commit()
        st.success(f"{symbol} added to backtest queue.")
    if st.button("Run queue now"):
        with SessionLocal() as db:
            result = process_backtest_queue(db, limit=10, timeframes=["1d"])
        st.success(f"Processed {result.get('processed', 0)}, failed {result.get('failed', 0)}.")
        if result.get("errors"):
            st.warning("; ".join(result["errors"][:5]))
    with SessionLocal() as db:
        queue_df = pd.DataFrame(queue_status_rows(db, limit=300))
        summaries = to_df(
            db.scalars(select(StrategyBacktestSummary).order_by(StrategyBacktestSummary.updated_at.desc()).limit(300)).all()
        )
    tab1, tab2 = st.tabs(["Queue", "Latest backtests"])
    with tab1:
        if queue_df.empty:
            st.info("Backtest queue is empty.")
        else:
            st.dataframe(queue_df, use_container_width=True, hide_index=True)
    with tab2:
        if summaries.empty:
            st.info("No backtest summaries yet.")
        else:
            visible = ["symbol", "strategy_name", "timeframe", "recommendation", "score", "total_return", "max_drawdown", "win_rate", "profit_factor", "trades_count", "updated_at"]
            st.dataframe(summaries[[col for col in visible if col in summaries.columns]], use_container_width=True, hide_index=True)


def page_telegram_media_analysis() -> None:
    st.title("Telegram Media Analysis")
    if st.button("Analyze pending saved images"):
        with SessionLocal() as db:
            result = analyze_pending_media(db, limit=100)
        st.success(f"Processed {result.get('processed', 0)}, skipped {result.get('skipped', 0)}.")
        if result.get("errors"):
            st.warning("; ".join(result["errors"][:5]))
    with SessionLocal() as db:
        rows = to_df(db.scalars(select(TelegramMediaAnalysis).order_by(TelegramMediaAnalysis.created_at.desc()).limit(500)).all())
        symbols = to_df(db.scalars(select(TelegramMessageSymbol).order_by(TelegramMessageSymbol.created_at.desc()).limit(500)).all())
    tab1, tab2 = st.tabs(["Media OCR", "Detected symbols"])
    with tab1:
        if rows.empty:
            st.info("No media analysis rows yet.")
        else:
            visible = ["telegram_message_id", "media_path", "media_type", "status", "detected_symbols", "ocr_text", "error_message", "created_at"]
            st.dataframe(rows[[col for col in visible if col in rows.columns]], use_container_width=True, hide_index=True)
    with tab2:
        if symbols.empty:
            st.info("No Telegram symbol extraction rows yet.")
        else:
            visible = ["telegram_message_id", "symbol", "confidence", "source", "intent", "reason", "created_at"]
            st.dataframe(symbols[[col for col in visible if col in symbols.columns]], use_container_width=True, hide_index=True)


def page_daily_egx_report() -> None:
    st.title("Daily EGX Report")
    st.caption("Upload the daily EGX Excel report. It is stored as a dated source and blended into combined analysis and opportunities.")

    upload_col, action_col = st.columns([1.3, 1])
    with upload_col:
        report_file = st.file_uploader("Upload daily EGX report", type=["xlsx", "xls"], key="daily_egx_report_upload")
        notes = st.text_input("Notes", placeholder="Optional source note, session, or broker context")
    with action_col:
        combine_after = st.checkbox("Refresh combined analysis after import", value=True)
        opportunities_after = st.checkbox("Refresh opportunities after import", value=True)
        st.caption("The workbook is scored from real uploaded rows. No fake rows are created.")

    if report_file and st.button("Import daily report", type="primary"):
        with st.spinner("Importing workbook and scoring report rows..."):
            with SessionLocal() as db:
                result = import_report_bytes(db, report_file.getvalue(), filename=report_file.name, notes=notes)
                latest_rows = latest_report_rows(db, limit=1000)
                symbols = [row.symbol for row in latest_rows]
        st.success(
            f"Imported {result['rows_count']} row(s) from {result['filename']} "
            f"for {result.get('report_date') or 'unknown date'}."
        )
        if result.get("duplicate_file"):
            st.warning("This exact file was imported before. I stored it as a new historical upload anyway.")
        if combine_after and symbols:
            with st.spinner("Refreshing combined analysis using the new daily report..."):
                with SessionLocal() as db:
                    combined = refresh_combined_analysis(db, symbols=symbols, settings=settings, limit=len(symbols), run_missing=False)
            st.success(f"Combined analysis refreshed for {combined.get('count', 0)} symbol(s).")
        if opportunities_after:
            with st.spinner("Refreshing opportunities with the daily report component..."):
                with SessionLocal() as db:
                    opp = refresh_opportunities(db, settings=settings, limit=100, run_screening=False)
            st.success(f"Opportunities refreshed: {opp.get('saved', 0)} row(s).")

    with SessionLocal() as db:
        summary = summarize_latest_report(db)
        latest_rows_df = to_df(latest_report_rows(db, limit=1000))
        uploads_df = to_df(
            db.scalars(
                select(DailyEGXReportUpload)
                .order_by(DailyEGXReportUpload.created_at.desc())
                .limit(25)
            ).all()
        )

    if not summary.get("available"):
        st.info("No daily EGX report has been uploaded yet.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Latest report date", str(summary.get("report_date") or "-")[:10])
    c2.metric("Rows", summary.get("rows_count", 0))
    c3.metric("BUY", (summary.get("recommendations") or {}).get("BUY", 0))
    c4.metric("WATCH", (summary.get("recommendations") or {}).get("WATCH", 0))

    if latest_rows_df.empty:
        st.warning("Latest upload has no parsed stock rows.")
        return

    filter_cols = st.columns([1, 1, 1])
    rec_options = sorted(str(item) for item in latest_rows_df["recommendation"].dropna().unique())
    selected_recs = filter_cols[0].multiselect("Recommendation", rec_options, default=[item for item in rec_options if item in {"BUY", "WATCH"}])
    min_score = filter_cols[1].slider("Minimum report score", 0, 100, 55)
    symbol_search = filter_cols[2].text_input("Search symbol")

    filtered = latest_rows_df.copy()
    if selected_recs:
        filtered = filtered[filtered["recommendation"].isin(selected_recs)]
    filtered = filtered[filtered["report_score"].fillna(0) >= min_score]
    if symbol_search:
        filtered = filtered[filtered["symbol"].astype(str).str.contains(symbol_search, case=False, na=False)]

    tabs = st.tabs(["Leaderboard", "Score Mix", "Uploads History", "Raw Notes"])
    visible_cols = [
        "symbol",
        "recommendation",
        "report_score",
        "signal",
        "mode",
        "status_text",
        "short_term",
        "medium_term",
        "performance",
        "weight",
        "buy_price",
        "stop_loss",
        "target1",
        "target2",
        "risk_reward",
        "report_date",
    ]
    with tabs[0]:
        st.dataframe(filtered[[col for col in visible_cols if col in filtered.columns]], use_container_width=True, hide_index=True)
        export_buttons(filtered[[col for col in visible_cols if col in filtered.columns]], "daily_egx_report_leaderboard")
    with tabs[1]:
        mix = latest_rows_df.groupby("recommendation", dropna=False).size().reset_index(name="stocks")
        st.bar_chart(mix.set_index("recommendation"))
        metric_cols = st.columns(4)
        metric_cols[0].metric("Average score", f"{latest_rows_df['report_score'].mean():.0f}%")
        metric_cols[1].metric("Median score", f"{latest_rows_df['report_score'].median():.0f}%")
        metric_cols[2].metric("Buy-mode rows", int(latest_rows_df["mode"].astype(str).str.contains("Buy", case=False, na=False).sum()))
        metric_cols[3].metric("Sell-mode rows", int(latest_rows_df["mode"].astype(str).str.contains("Sell", case=False, na=False).sum()))
    with tabs[2]:
        if uploads_df.empty:
            st.info("No upload history yet.")
        else:
            visible_uploads = ["id", "source_name", "original_filename", "report_date", "rows_count", "status", "created_at", "notes"]
            st.dataframe(uploads_df[[col for col in visible_uploads if col in uploads_df.columns]], use_container_width=True, hide_index=True)
    with tabs[3]:
        notes_df = filtered[["symbol", "recommendation", "report_score", "final_arbitration"]].copy()
        notes_df = notes_df[notes_df["final_arbitration"].fillna("").astype(str).str.len() > 0]
        st.dataframe(notes_df, use_container_width=True, hide_index=True) if not notes_df.empty else st.info("No final arbitration notes in the filtered rows.")
    st.caption(f"Daily report is one input in the final score. Risk Note: {RISK_NOTE}")


def page_combined_analysis() -> None:
    st.title("Combined Analysis")
    with SessionLocal() as db:
        stock_symbols = db.scalars(select(Stock.symbol).where(Stock.is_active.is_(True)).order_by(Stock.symbol.asc())).all()
    selected = st.selectbox("Symbol", stock_symbols or ["COMI"], index=0)
    c1, c2, c3 = st.columns(3)
    if c1.button("Build combined analysis"):
        with SessionLocal() as db:
            payload = build_combined_analysis(db, selected, settings=settings, run_missing=True, persist=True)
        st.success(f"{selected}: {payload['final_recommendation']} score {payload['final_score']:.0f}%.")
    if c2.button("Refresh top combined analyses"):
        with SessionLocal() as db:
            result = refresh_combined_analysis(db, settings=settings, limit=int(settings.strategy_symbol_limit), run_missing=False)
        st.success(f"Refreshed {result.get('count', 0)} combined analysis row(s).")
    if c3.button("Send selected to Telegram"):
        from app.services.stock_analysis_engine import format_combined_analysis_report

        message = format_combined_analysis_report(selected, settings=settings, refresh=True)
        result = send_alert_to_subscribers_sync(message, settings=settings)
        st.success(f"Eligible {result.get('eligible', 0)}, sent {result.get('sent', 0)}, failed {result.get('failed', 0)}.")

    with SessionLocal() as db:
        row = db.scalar(select(StockCombinedAnalysis).where(StockCombinedAnalysis.symbol == selected))
        all_rows = to_df(db.scalars(select(StockCombinedAnalysis).order_by(StockCombinedAnalysis.final_score.desc()).limit(300)).all())
        messages = pd.DataFrame(latest_related_telegram(db, selected))
        media = pd.DataFrame(latest_related_media(db, selected))
        strategies = pd.DataFrame(latest_strategy_results(db, symbol=selected, limit=20))
        backtests = to_df(
            db.scalars(
                select(StrategyBacktestSummary)
                .where(StrategyBacktestSummary.symbol == selected)
                .order_by(StrategyBacktestSummary.updated_at.desc())
                .limit(20)
            ).all()
        )
    if row:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Recommendation", row.final_recommendation)
        c2.metric("Final score", f"{row.final_score:.0f}%")
        c3.metric("Confidence", f"{row.confidence:.0f}%")
        c4.metric("Risk", f"{row.risk_score or 0:.0f}%")
        st.write(row.reason)
        components = row.components_json or {}
        scores = components.get("scores") or {}
        st.bar_chart(pd.DataFrame([scores]).T.rename(columns={0: "score"}))
    else:
        st.info("No combined analysis stored for this symbol yet.")

    tabs = st.tabs(["Leaderboard", "Telegram messages", "Image OCR", "Strategy comparison", "Backtests"])
    with tabs[0]:
        if all_rows.empty:
            st.info("No combined analysis rows yet.")
        else:
            visible = ["symbol", "final_recommendation", "final_score", "confidence", "telegram_score", "strategy_legacy_score", "strategy_cli_v6_score", "daily_report_score", "tradingview_score", "backtest_score", "risk_score", "updated_at"]
            st.dataframe(all_rows[[col for col in visible if col in all_rows.columns]], use_container_width=True, hide_index=True)
    with tabs[1]:
        st.dataframe(messages, use_container_width=True, hide_index=True) if not messages.empty else st.info("No Telegram messages linked to this symbol yet.")
    with tabs[2]:
        st.dataframe(media, use_container_width=True, hide_index=True) if not media.empty else st.info("No OCR rows linked to this symbol yet.")
    with tabs[3]:
        if strategies.empty:
            st.info("No strategy comparison rows yet.")
        else:
            visible = ["strategy_code", "strategy_name", "symbol", "recommendation", "score", "confidence", "trend", "reason", "created_at"]
            st.dataframe(strategies[[col for col in visible if col in strategies.columns]], use_container_width=True, hide_index=True)
    with tabs[4]:
        st.dataframe(backtests, use_container_width=True, hide_index=True) if not backtests.empty else st.info("No backtest rows yet.")


def page_system_settings() -> None:
    st.title("System Settings")
    with SessionLocal() as db:
        rows = pd.DataFrame(list_settings(db))
    if rows.empty:
        st.info("No DB-backed settings are available.")
        return
    edited = st.data_editor(
        rows[["key", "value", "value_type", "description", "updated_at"]],
        disabled=["key", "value_type", "description", "updated_at"],
        use_container_width=True,
        hide_index=True,
    )
    if st.button("Save system settings"):
        with SessionLocal() as db:
            for _, row in edited.iterrows():
                set_setting(db, str(row["key"]), row["value"], value_type=str(row["value_type"]))
        st.success("System settings saved.")
    st.caption("Secrets remain in .env and are not displayed. DB settings override .env for automation behavior.")


def page_imports() -> None:
    st.title("Imports")
    tab1, tab2, tab3, tab4 = st.tabs(["Stocks CSV", "OHLCV CSV", "Telegram Sources Excel/CSV", "Market Depth CSV"])
    with tab1:
        stocks_file = st.file_uploader("Import stocks CSV", type=["csv"], key="stocks_csv")
        if stocks_file and st.button("Import stocks"):
            df = pd.read_csv(stocks_file)
            df.columns = [str(col).strip().lower() for col in df.columns]
            with SessionLocal() as db:
                for _, row in df.iterrows():
                    symbol = str(row["symbol"]).upper().strip()
                    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
                    data = {
                        "name_ar": row.get("name_ar"),
                        "name_en": row.get("name_en"),
                        "sector": row.get("sector"),
                        "tradingview_symbol": row.get("tradingview_symbol") or f"EGX:{symbol}",
                        "is_active": bool(row.get("is_active", True)),
                    }
                    if stock:
                        for key, value in data.items():
                            setattr(stock, key, value)
                    else:
                        db.add(Stock(symbol=symbol, **data))
                db.commit()
            st.success("Stocks imported.")
    with tab2:
        ohlcv_file = st.file_uploader("Import OHLCV CSV", type=["csv"], key="ohlcv_csv")
        symbol = st.text_input("OHLCV symbol", value="COMI").upper()
        if ohlcv_file and symbol and st.button("Save OHLCV CSV"):
            target = Path(settings.csv_data_dir) / f"{symbol}.csv"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(ohlcv_file.getvalue())
            st.success(f"Saved {target}")
    with tab3:
        st.write("Use columns such as username, title, trust_score, source_type, is_active, notes.")
        source_file = st.file_uploader("Import Telegram sources", type=["xlsx", "xls", "csv"], key="imports_sources")
        fetch_after = st.checkbox("Fetch after source import", value=True, key="imports_sources_fetch")
        if source_file and st.button("Import Telegram sources"):
            try:
                df_import = read_sources_file(source_file.name, source_file.getvalue())
                with SessionLocal() as db:
                    result = import_sources_from_df(db, df_import)
                st.success(f"Inserted {result.inserted}, updated {result.updated}, skipped {result.skipped}.")
                if fetch_after:
                    with st.spinner("Fetching Telegram data now..."):
                        ingestion = run_ingestion_cycle()
                        cached_recommendations.clear()
                        cached_tradingview_rows.clear()
                        cached_strategy_universe.clear()
                    st.success(ingestion_result_text(ingestion))
            except Exception as exc:
                st.error(f"Source import failed: {exc}")
    with tab4:
        st.write("Use columns: timestamp, source, symbol, side, level, price, quantity, num_orders.")
        depth_file = st.file_uploader("Import market depth CSV", type=["csv"], key="imports_market_depth")
        if depth_file and st.button("Save market depth CSV"):
            safe_name = Path(depth_file.name).name
            target = Path(settings.market_depth_data_dir) / safe_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(depth_file.getvalue())
            st.success(f"Saved {target}")


PAGES: dict[str, Any] = {
    "Dashboard / Overview": intelligence_executive_overview_page.render,
    "Overview": page_home,
    "Telegram Data": intelligence_telegram_intelligence_page.render,
    "Telegram Bot Status": intelligence_bot_status_page.render,
    "Telegram Users": page_telegram_users,
    "Telegram Channels": page_channels,
    "Messages": page_messages,
    "Signals": page_signals,
    "Chart Images": page_chart_images,
    "Telegram Media Analysis": page_telegram_media_analysis,
    "Telegram Channel Performance": intelligence_telegram_performance_page.render,
    "Telegram Alerts Monitor": page_telegram_alerts_monitor,
    "Market Data": intelligence_data_sources_page.render,
    "Market Heatmap": intelligence_market_heatmap_page.render,
    "TradingView Screener": page_tradingview_screener,
    "Daily EGX Report": page_daily_egx_report,
    "Market Depth": page_market_depth,
    "All Stocks": page_all_stocks,
    "Stock Detail": page_stock_detail,
    "Imports": page_imports,
    "Technical Analysis": intelligence_stock_full_page.render,
    "Stock Full Analysis": intelligence_stock_full_page.render,
    "AI-Powered Analysis": intelligence_ai_analysis_page.render,
    "Financial Analysis": intelligence_financial_analysis_page.render,
    "News Analysis": intelligence_news_analysis_page.render,
    "News Impact": intelligence_news_impact_page.render,
    "Strategy Results": page_strategies,
    "Strategies": page_strategies,
    "CLI v6 Strategy": page_cli_v6_strategy,
    "Combined Analysis": page_combined_analysis,
    "Recommendations": page_final_recommendations,
    "Daily Opportunities": intelligence_daily_opportunities_page.render,
    "Daily Best Opportunities": intelligence_daily_opportunities_page.render,
    "Opportunities": page_opportunities,
    "Final Recommendations": page_final_recommendations,
    "Trading Alerts": intelligence_trading_alerts_page.render,
    "Daily Market Evaluation": intelligence_daily_market_evaluation_page.render,
    "Market Regime": intelligence_market_regime_page.render,
    "Backtesting": intelligence_backtesting_page.render,
    "Backtesting Page": intelligence_backtesting_page.render,
    "Strategy Backtest": page_strategy_backtest,
    "CLI v6 Backtests": page_cli_v6_backtests,
    "Backtest Queue": page_backtest_queue,
    "Portfolio / Trading Simulator": intelligence_portfolio_page.render,
    "Portfolio Bot": intelligence_portfolio_page.render,
    "Trading Control Center": intelligence_trading_control_page.render,
    "Live Trades": intelligence_live_trades_page.render,
    "Reports": intelligence_reports_center_page.render,
    "Daily Reports": intelligence_daily_reports_page.render,
    "Reports Center": intelligence_reports_center_page.render,
    "Risk & Audit Center": intelligence_risk_audit_page.render,
    "Last 7 Days Audit": intelligence_last7_audit_page.render,
    "Recommendation Performance": intelligence_recommendation_performance_page.render,
    "Daily Prediction Review": intelligence_daily_prediction_review_page.render,
    "Missed Opportunities": intelligence_missed_opportunities_page.render,
    "Missed Opportunity Diagnosis": intelligence_missed_opportunity_diagnosis_page.render,
    "Why Not Selected": intelligence_why_not_selected_page.render,
    "Strategy Learning Center": intelligence_strategy_learning_center_page.render,
    "Source Accuracy": intelligence_source_accuracy_page.render,
    "Accuracy Lab": intelligence_accuracy_lab_page.render,
    "Walk-Forward Testing": intelligence_walk_forward_testing_page.render,
    "Pump Risk Monitor": intelligence_pump_risk_monitor_page.render,
    "Intraday Scanner": intelligence_intraday_scanner_page.render,
    "Risk & Expectancy": intelligence_risk_expectancy_page.render,
    "Recommendation Quality": intelligence_recommendation_quality_page.render,
    "Analysis Comparison": intelligence_comparison_page.render,
    "Accuracy Center": intelligence_accuracy_page.render,
    "Accuracy Dashboard": intelligence_accuracy_page.render,
    "Mistake Review": intelligence_mistake_review_page.render,
    "Settings / Users / Admin": intelligence_system_health_admin_page.render,
    "System Health / Admin Control": intelligence_system_health_admin_page.render,
    "Setup / Control Center": intelligence_setup_control_center_page.render,
    "Automation Monitor": page_automation_monitor,
    "Automation Control": page_automation_control,
    "Admin Settings": intelligence_admin_settings_page.render,
    "Settings": page_settings,
    "System Settings": page_system_settings,
    "Manual Analyze": page_manual_analyze,
    "Telegram Bot": page_telegram_bot,
    "Performance": page_performance,
}

PAGE_CATEGORIES = [
    ("Command Center", ["Dashboard / Overview"]),
    (
        "Signals & Recommendations",
        [
            "Daily Opportunities",
            "Recommendations",
            "Opportunities",
            "Trading Alerts",
            "Final Recommendations",
            "Combined Analysis",
        ],
    ),
    (
        "Market & Data",
        [
            "Market Data",
            "Market Heatmap",
            "Daily Market Evaluation",
            "Market Regime",
            "TradingView Screener",
            "Daily EGX Report",
            "Market Depth",
            "All Stocks",
            "Stock Detail",
            "Imports",
        ],
    ),
    (
        "Analysis Workbench",
        [
            "Stock Full Analysis",
            "Technical Analysis",
            "Financial Analysis",
            "News Analysis",
            "News Impact",
            "Strategy Results",
            "CLI v6 Strategy",
            "AI-Powered Analysis",
            "Analysis Comparison",
        ],
    ),
    (
        "Telegram Intelligence",
        [
            "Telegram Data",
            "Telegram Bot Status",
            "Telegram Users",
            "Telegram Channels",
            "Messages",
            "Signals",
            "Chart Images",
            "Telegram Media Analysis",
            "Telegram Channel Performance",
            "Telegram Alerts Monitor",
            "Telegram Bot",
        ],
    ),
    (
        "Backtesting & Learning",
        [
            "Backtesting",
            "Strategy Backtest",
            "CLI v6 Backtests",
            "Backtest Queue",
            "Risk & Audit Center",
            "Last 7 Days Audit",
            "Recommendation Performance",
            "Daily Prediction Review",
            "Missed Opportunities",
            "Missed Opportunity Diagnosis",
            "Why Not Selected",
            "Strategy Learning Center",
            "Source Accuracy",
            "Accuracy Lab",
            "Walk-Forward Testing",
            "Pump Risk Monitor",
            "Intraday Scanner",
            "Risk & Expectancy",
            "Recommendation Quality",
            "Accuracy Center",
            "Mistake Review",
        ],
    ),
    (
        "Portfolio & Execution",
        [
            "Portfolio / Trading Simulator",
            "Trading Control Center",
            "Live Trades",
        ],
    ),
    (
        "Reports & Exports",
        [
            "Reports",
            "Daily Reports",
            "Reports Center",
        ],
    ),
    (
        "Settings & Health",
        [
            "Settings / Users / Admin",
            "System Health / Admin Control",
            "Setup / Control Center",
            "Automation Monitor",
            "Automation Control",
            "Admin Settings",
            "Settings",
            "System Settings",
            "Manual Analyze",
            "Performance",
        ],
    ),
]

NAV_CATEGORY_HELP = {
    "Command Center": "Executive status, market posture, and portfolio pulse.",
    "Signals & Recommendations": "Daily ideas, final recommendations, alerts, and combined scores.",
    "Market & Data": "Prices, heatmaps, imports, TradingView, and market-regime inputs.",
    "Analysis Workbench": "Stock-level technical, financial, news, AI, and strategy analysis.",
    "Telegram Intelligence": "Channels, messages, OCR media, users, bot health, and alert monitors.",
    "Backtesting & Learning": "Backtests, audits, accuracy, missed opportunities, and learning diagnostics.",
    "Portfolio & Execution": "Paper portfolio, trading controls, risk guard, and execution history.",
    "Reports & Exports": "Daily reports, file reports, and generated outputs.",
    "Settings & Health": "Automation, system settings, admin tools, health checks, and manual actions.",
}

NAV_PAGE_LABELS = {
    "Dashboard / Overview": "Executive Overview",
    "Daily Opportunities": "Daily Best Opportunities",
    "Recommendations": "Final Decision Board",
    "Opportunities": "Opportunity Engine",
    "Trading Alerts": "Buy/Sell/TP Alerts",
    "Combined Analysis": "Combined Stock Analysis",
    "Market Data": "Data Sources",
    "Market Heatmap": "Sector Heatmap",
    "Stock Detail": "Single Stock Detail",
    "Stock Full Analysis": "Full Stock Analysis",
    "Technical Analysis": "Technical Workspace",
    "Strategy Results": "Strategy Registry",
    "Telegram Data": "Telegram Intelligence",
    "Messages": "Collected Messages",
    "Signals": "Extracted Signals",
    "Chart Images": "Chart Images/OCR",
    "Backtesting": "Backtesting Overview",
    "Risk & Audit Center": "Risk Audit Center",
    "Recommendation Performance": "Recommendation Performance",
    "Daily Prediction Review": "Daily Prediction Review",
    "Source Accuracy": "Source Accuracy",
    "Accuracy Lab": "Accuracy Lab",
    "Walk-Forward Testing": "Walk-Forward Testing",
    "Risk & Expectancy": "Risk and Expectancy",
    "Portfolio / Trading Simulator": "Portfolio Simulator",
    "Reports": "Reports Center",
    "Settings / Users / Admin": "System Health/Admin",
}

NAV_PAGE_DESCRIPTIONS = {
    "Dashboard / Overview": "Best first stop: market status, portfolio pulse, top ideas, and risk warnings.",
    "Daily Opportunities": "The clean shortlist for today with score, risk, entry, target, and stop context.",
    "Trading Alerts": "Focused buy, sell, take-profit, and risk alerts from the complete system.",
    "Market Heatmap": "Sector and stock movement map with gainers, losers, and risk color.",
    "Stock Full Analysis": "Deep stock view across chart, technicals, financials, news, Telegram, and accuracy.",
    "Recommendation Performance": "Recommendation versus actual outcome with quality and evaluation status.",
    "Daily Prediction Review": "End-of-day review: what was predicted, what happened, and why.",
    "Missed Opportunity Diagnosis": "Top movers that were not selected, with failed filters and suggested fixes.",
    "Pump Risk Monitor": "Telegram/social concentration, liquidity, and hype-risk checks.",
    "Trading Control Center": "Mode, emergency stop, risk limits, and execution safety controls.",
    "Reports": "Generated Excel/PDF reports, status, and manual report actions.",
    "System Health / Admin Control": "Database, bot, automation, reports, and smoke-test controls.",
    "Automation Control": "Configure and run automation tasks in audit/paper mode.",
}

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {
        border-right: 1px solid rgba(148, 163, 184, .18);
        box-shadow: 12px 0 28px rgba(15, 23, 42, .10);
    }
    [data-testid="stSidebar"] .stSelectbox,
    [data-testid="stSidebar"] .stTextInput,
    [data-testid="stSidebar"] .stRadio {
        margin-bottom: .35rem;
    }
    [data-testid="stSidebar"] label {
        font-size: .78rem !important;
        font-weight: 700 !important;
        letter-spacing: .01em;
    }
    [data-testid="stSidebar"] [data-baseweb="select"] > div {
        background: rgba(15, 23, 42, .72);
        border-color: rgba(148, 163, 184, .34);
        border-radius: 8px;
    }
    [data-testid="stSidebar"] input {
        background: rgba(15, 23, 42, .72) !important;
        border-color: rgba(148, 163, 184, .34) !important;
        border-radius: 8px !important;
    }
    [data-testid="stSidebar"] [role="radiogroup"] {
        gap: 2px;
    }
    [data-testid="stSidebar"] [role="radiogroup"] label {
        background: rgba(255, 255, 255, .035);
        border: 1px solid rgba(148, 163, 184, .10);
        transition: background .12s ease, border-color .12s ease;
    }
    [data-testid="stSidebar"] [role="radiogroup"] label:hover {
        background: rgba(37, 99, 235, .16);
        border-color: rgba(96, 165, 250, .38);
    }
    .egx-sidebar-brand {
        padding: 10px 0 8px;
        border-bottom: 1px solid rgba(148, 163, 184, .22);
        margin-bottom: 12px;
    }
    .egx-sidebar-title {
        color: #f8fafc;
        font-size: 20px;
        font-weight: 850;
        line-height: 1.05;
    }
    .egx-sidebar-subtitle {
        color: #94a3b8;
        font-size: 12px;
        margin-top: 4px;
    }
    .egx-sidebar-status {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 6px;
        margin: 4px 0 14px;
    }
    .egx-status-pill {
        border: 1px solid rgba(148, 163, 184, .18);
        border-radius: 8px;
        padding: 7px 8px;
        background: rgba(15, 23, 42, .54);
        color: #e5e7eb;
        font-size: 11px;
        line-height: 1.2;
    }
    .egx-status-pill strong {
        display: block;
        color: #f8fafc;
        font-size: 12px;
    }
    .egx-status-ok { border-color: rgba(34, 197, 94, .40); }
    .egx-status-warn { border-color: rgba(245, 158, 11, .42); }
    .egx-status-risk { border-color: rgba(248, 113, 113, .50); }
    .egx-nav-help {
        color: #94a3b8;
        font-size: 12px;
        line-height: 1.35;
        margin: -2px 0 10px;
    }
    .egx-nav-current {
        border: 1px solid rgba(96, 165, 250, .35);
        background: rgba(37, 99, 235, .13);
        border-radius: 8px;
        padding: 9px 10px;
        margin: 9px 0 14px;
        color: #dbeafe;
        font-size: 12px;
        line-height: 1.35;
    }
    .egx-nav-current strong {
        color: #f8fafc;
        font-size: 13px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _nav_label(page: str) -> str:
    return NAV_PAGE_LABELS.get(page, page)


def _setting_bool(key: str, fallback: bool) -> bool:
    try:
        return bool(get_setting_value(key, fallback, "bool"))
    except Exception:
        return bool(fallback)


def _apply_page_search() -> None:
    st.session_state["egx_page_search"] = str(st.session_state.get("egx_page_search_input", "") or "").strip()


def _clear_page_search() -> None:
    st.session_state["egx_page_search"] = ""
    st.session_state["egx_page_search_input"] = ""


live_enabled = _setting_bool("live_trading_enabled", settings.live_trading_enabled)
audit_enabled = _setting_bool("audit_mode_enabled", settings.audit_mode)
paper_enabled = _setting_bool("paper_trading_enabled", True)
emergency_stop = _setting_bool("emergency_stop_enabled", settings.emergency_stop_trading)
automation_enabled = _setting_bool("automation_enabled", settings.automation_enabled)

st.sidebar.markdown(
    """
    <div class="egx-sidebar-brand">
      <div class="egx-sidebar-title">EGX Intelligence</div>
      <div class="egx-sidebar-subtitle">Research, signals, reports, and audit controls</div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.sidebar.markdown(
    f"""
    <div class="egx-sidebar-status">
      <div class="egx-status-pill {'egx-status-risk' if live_enabled else 'egx-status-ok'}"><strong>{'LIVE ON' if live_enabled else 'LIVE OFF'}</strong>Broker execution</div>
      <div class="egx-status-pill {'egx-status-ok' if emergency_stop else 'egx-status-warn'}"><strong>{'STOP ON' if emergency_stop else 'STOP OFF'}</strong>Emergency guard</div>
      <div class="egx-status-pill {'egx-status-ok' if audit_enabled else 'egx-status-warn'}"><strong>{'AUDIT ON' if audit_enabled else 'AUDIT OFF'}</strong>Validation mode</div>
      <div class="egx-status-pill {'egx-status-ok' if paper_enabled else 'egx-status-warn'}"><strong>{'PAPER ON' if paper_enabled else 'PAPER OFF'}</strong>Simulation</div>
      <div class="egx-status-pill {'egx-status-ok' if automation_enabled else 'egx-status-warn'}"><strong>{'AUTO ON' if automation_enabled else 'AUTO OFF'}</strong>Scheduler</div>
      <div class="egx-status-pill {'egx-status-ok' if settings.telegram_bot_token else 'egx-status-warn'}"><strong>{'BOT READY' if settings.telegram_bot_token else 'BOT MISSING'}</strong>Telegram</div>
    </div>
    """,
    unsafe_allow_html=True,
)

search_input = st.sidebar.text_input("Search pages", key="egx_page_search_input", placeholder="Type page, stock area, or feature...")
search_buttons = st.sidebar.columns(2)
search_buttons[0].button("Search", key="egx_page_search_apply", use_container_width=True, on_click=_apply_page_search)
search_buttons[1].button(
    "Clear",
    key="egx_page_search_clear",
    use_container_width=True,
    disabled=not bool(st.session_state.get("egx_page_search") or st.session_state.get("egx_page_search_input")),
    on_click=_clear_page_search,
)
page_search = str(st.session_state.get("egx_page_search", "") or "").strip()
if search_input and not page_search:
    st.sidebar.caption("Use Search to filter the page list.")
all_page_names = list(PAGES.keys())
if page_search:
    query = page_search.lower()
    page_options = [
        name
        for name in all_page_names
        if query in name.lower() or query in _nav_label(name).lower()
    ]
    if not page_options:
        st.sidebar.warning("No matching page. Showing all pages.")
        page_options = all_page_names
    default_index = page_options.index(st.session_state.get("egx_selected_page")) if st.session_state.get("egx_selected_page") in page_options else 0
    page_name = st.sidebar.selectbox(
        "Matching pages",
        page_options,
        index=default_index,
        key="egx_search_selected_page",
        format_func=_nav_label,
    )
    st.session_state["egx_selected_page"] = page_name
    st.sidebar.caption(f"{len(page_options)} page match{'es' if len(page_options) != 1 else ''}.")
else:
    category_names = [category for category, _pages in PAGE_CATEGORIES]
    current_page = st.session_state.get("egx_selected_page")
    current_category = next((category for category, pages in PAGE_CATEGORIES if current_page in pages), category_names[0])
    category_index = category_names.index(current_category) if current_category in category_names else 0
    selected_category = st.sidebar.selectbox("Workspace", category_names, index=category_index, key="egx_selected_section")
    st.sidebar.markdown(f'<div class="egx-nav-help">{NAV_CATEGORY_HELP.get(selected_category, "")}</div>', unsafe_allow_html=True)
    page_options = next((pages for category, pages in PAGE_CATEGORIES if category == selected_category), PAGE_CATEGORIES[0][1])
    default_index = page_options.index(current_page) if current_page in page_options else 0
    page_name = st.sidebar.radio(
        "Page",
        page_options,
        index=default_index,
        key=f"egx_page_radio_{selected_category}",
        format_func=_nav_label,
    )
    st.session_state["egx_selected_page"] = page_name

st.sidebar.markdown(
    f"""
    <div class="egx-nav-current">
      <strong>{_nav_label(page_name)}</strong><br>
      {NAV_PAGE_DESCRIPTIONS.get(page_name, "Use this workspace for the selected EGX intelligence workflow.")}
    </div>
    """,
    unsafe_allow_html=True,
)

render_top_header()
PAGES[page_name]()
st.sidebar.caption(f"Disclaimer: {DISCLAIMER}")
