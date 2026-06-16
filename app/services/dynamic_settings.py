from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import AutomationSetting


DEFAULT_SETTINGS: dict[str, tuple[str, str, str]] = {
    "automation_enabled": ("true", "bool", "Enable continuous automation runner."),
    "automation_interval_seconds": ("600", "int", "Automation loop interval, minimum 60 seconds."),
    "automation_symbol_limit": ("25", "int", "Maximum active stock symbols processed by each automation strategy/final-decision cycle."),
    "daily_dynamic_refresh_enabled": ("true", "bool", "Run one daily full-universe dynamic refresh."),
    "daily_dynamic_refresh_time": ("08:30", "string", "Cairo time for the daily full-universe dynamic refresh."),
    "daily_dynamic_refresh_symbol_limit": ("250", "int", "Maximum active stock symbols rebuilt by the daily full refresh."),
    "automation_fetch_telegram": ("true", "bool", "Fetch new Telegram messages during automation."),
    "automation_run_tradingview": ("true", "bool", "Run TradingView screening during automation."),
    "automation_fetch_financial_data": ("true", "bool", "Refresh TradingView financial snapshots during automation when due."),
    "automation_fetch_news_data": ("true", "bool", "Refresh free RSS news during automation when due."),
    "automation_fetch_ohlcv_data": ("true", "bool", "Refresh TradingView OHLCV candles during automation when due."),
    "financial_refresh_interval_seconds": ("86400", "int", "Minimum seconds between automatic financial snapshot refreshes."),
    "news_refresh_interval_seconds": ("3600", "int", "Minimum seconds between automatic news RSS refreshes."),
    "ohlcv_refresh_interval_seconds": ("600", "int", "Minimum seconds between automatic OHLCV refreshes."),
    "dynamic_data_symbol_limit": ("15", "int", "Maximum symbols per automatic dynamic data refresh."),
    "dynamic_data_timeframes": ("1d", "string", "Comma-separated OHLCV timeframes refreshed from TradingView."),
    "news_rss_query_template": ("{symbol} EGX OR {name} Egypt", "string", "Google News RSS query template."),
    "news_rss_max_items_per_symbol": ("5", "int", "Maximum RSS news items stored per symbol per refresh."),
    "automation_run_strategy_legacy": ("true", "bool", "Run the legacy strategy."),
    "automation_run_cli_v6": ("true", "bool", "Run CLI v6 EGX strategy."),
    "automation_run_final_decisions": ("true", "bool", "Run the final weighted decision engine."),
    "automation_run_opportunities": ("true", "bool", "Refresh opportunities."),
    "automation_send_alerts": ("true", "bool", "Send Telegram alerts."),
    "automation_update_accuracy": ("true", "bool", "Update signal accuracy tracking when later prices exist."),
    "automation_run_ai_analysis": ("false", "bool", "Run optional AI analysis during automation."),
    "enable_ai_analysis": ("false", "bool", "Enable optional AI analysis features."),
    "automation_run_portfolio_bot": ("false", "bool", "Run paper portfolio scan during automation."),
    "portfolio_bot_auto_execute_paper_trades": ("false", "bool", "Allow automation to execute paper BUY/SELL trades."),
    "portfolio_bot_symbol_limit": ("50", "int", "Maximum symbols scanned by the paper portfolio bot per cycle."),
    "automation_analyze_images": ("false", "bool", "Analyze Telegram images when OCR/media is enabled."),
    "telegram_analyze_images": ("false", "bool", "Enable OCR/media image analysis."),
    "telegram_download_media": ("false", "bool", "Download new Telegram image media."),
    "telegram_fetch_limit": ("30", "int", "Telegram fetch limit per active source."),
    "backtest_mode": ("opportunities_only", "string", "manual_only, hourly, daily, opportunities_only."),
    "backtest_queue_limit": ("10", "int", "Maximum queued symbols processed per backtest run."),
    "alert_confidence_threshold": ("70", "float", "Minimum alert confidence/score."),
    "data_provider_priority": ("tradingview_screener,tradingview_websocket,csv", "string", "Market data provider priority."),
    "combined_weight_telegram": ("20", "float", "Combined analysis weight for Telegram signals."),
    "combined_weight_strategy_legacy": ("20", "float", "Combined analysis weight for legacy strategy."),
    "combined_weight_cli_v6": ("20", "float", "Combined analysis weight for CLI v6."),
    "combined_weight_daily_report": ("15", "float", "Combined analysis weight for the daily uploaded EGX Excel report."),
    "combined_weight_tradingview": ("20", "float", "Combined analysis weight for TradingView."),
    "combined_weight_backtest": ("10", "float", "Combined analysis weight for backtest quality."),
    "combined_weight_risk": ("10", "float", "Combined analysis weight for freshness/risk."),
    "final_weight_technical": ("35", "float", "Final decision weight for technical analysis."),
    "final_weight_financial": ("25", "float", "Final decision weight for financial analysis."),
    "final_weight_news": ("20", "float", "Final decision weight for news analysis."),
    "final_weight_telegram": ("10", "float", "Final decision weight for Telegram analysis."),
    "final_weight_strategy": ("10", "float", "Final decision weight for strategy analysis."),
    "portfolio_bot_enabled": ("false", "bool", "Enable paper portfolio bot."),
    "liquidity_min_score": ("35", "float", "Minimum liquidity score required before allowing BUY decisions."),
    "market_regime_buy_penalty": ("10", "float", "Score penalty applied when market regime is bearish."),
    "sector_strength_weight": ("5", "float", "Small final-score adjustment for sector strength."),
    "risk_guard_max_weekly_loss_pct": ("8", "float", "Stop paper trading after this weekly loss percent."),
    "risk_guard_max_drawdown_pct": ("15", "float", "Stop paper trading after this max drawdown percent."),
    "risk_guard_max_consecutive_losses": ("3", "int", "Stop paper trading after this many losing trades in a row."),
    "ui_theme_mode": ("light", "string", "Dashboard theme: light or dark."),
    "ui_language_mode": ("en", "string", "Dashboard language mode: en, ar, or both."),
    "daily_stock_report_enabled": ("true", "bool", "Send automatic morning and evening daily stock recommendation reports."),
    "daily_stock_report_times": ("09:00,21:00", "string", "Comma-separated Cairo report times in HH:MM format."),
    "daily_stock_report_top_n": ("5", "int", "Number of top stocks included in automatic daily reports."),
    "daily_file_report_enabled": ("true", "bool", "Generate automatic Excel/PDF daily file report."),
    "daily_file_report_time": ("15:00", "string", "Cairo time for automatic daily file report generation."),
    "daily_file_report_send_telegram": ("true", "bool", "Send daily file report notification/documents to Telegram."),
    "end_of_day_review_enabled": ("true", "bool", "Generate the 9 PM prediction review and missed-opportunity report."),
    "intraday_rescan_enabled": ("true", "bool", "Run accuracy-learning intraday scans in audit/paper mode."),
    "intraday_rescan_times": ("after_open=10:15,mid_session=11:30,before_close=14:00,after_close=15:05", "string", "Cairo scan schedule for after-open, mid-session, before-close, and after-close learning scans."),
    "pump_risk_downgrade_threshold": ("70", "float", "Pump-risk score threshold that downgrades BUY signals to WATCH ONLY."),
    "learning_min_evaluated_sample": ("5", "int", "Minimum evaluated rows before accuracy and source reliability are treated as reliable."),
    "live_trading_enabled": ("false", "bool", "Master switch for real broker execution. Default is disabled."),
    "audit_mode": ("true", "bool", "Audit/simulation mode. Blocks live and automatic trade execution."),
    "audit_mode_enabled": ("true", "bool", "Alias for audit mode; when enabled, live broker execution is blocked."),
    "paper_trading_enabled": ("true", "bool", "Enable paper trading and simulated execution."),
    "emergency_stop_trading": ("true", "bool", "Emergency stop. Blocks live and automatic trade execution."),
    "emergency_stop_enabled": ("true", "bool", "Alias for emergency stop. Blocks all live broker execution."),
    "portfolio_auto_execution_enabled": ("false", "bool", "Master switch for automatic order execution."),
    "max_daily_trades": ("5", "int", "Maximum total trades allowed per day."),
    "max_daily_buy_trades": ("2", "int", "Maximum BUY trades allowed per day."),
    "max_position_size_percent": ("20", "float", "Maximum single-position size as percent of portfolio value."),
    "max_total_portfolio_exposure_percent": ("80", "float", "Maximum total portfolio exposure percent."),
    "max_loss_per_trade_percent": ("1", "float", "Maximum allowed loss per trade percent of portfolio value."),
    "max_daily_loss_pct": ("0.03", "float", "Stop new trades after this daily portfolio loss percentage."),
    "max_daily_loss_percent": ("3", "float", "Stop new trades after this daily loss percent."),
    "max_alerts_per_stock_per_day": ("1", "int", "Maximum alerts per stock per day."),
    "max_buy_alerts_per_stock_per_day": ("1", "int", "Maximum BUY alerts per stock per day."),
    "min_confidence_to_trade": ("75", "float", "Minimum recommendation confidence required for live trade consideration."),
    "require_market_open_check": ("true", "bool", "Require market open status before live trade execution."),
    "require_market_daily_score_check": ("true", "bool", "Require daily market evaluation to allow trading."),
    "require_manual_approval_for_first_live_trade": ("true", "bool", "Require manual dashboard approval before the first live trade."),
    "first_live_trade_approved": ("false", "bool", "Internal flag set only after explicit first live trade approval."),
    "market_daily_min_score_to_trade": ("60", "float", "Minimum daily market score required for new BUY trades."),
    "min_avg_daily_value_traded": ("500000", "float", "Minimum average traded value for BUY validation."),
    "max_allowed_spread_pct": ("0.015", "float", "Maximum allowed spread percentage for BUY validation."),
    "max_position_risk_pct": ("0.01", "float", "Maximum portfolio risk per trade for recommendation sizing."),
    "max_distance_from_entry_pct": ("0.025", "float", "Maximum distance above entry before WAIT FOR PULLBACK."),
    "paper_trading_required_days": ("14", "int", "Required paper-trading review period before live trading can be considered."),
    "paper_trading_min_win_rate": ("0.55", "float", "Minimum paper-trading win rate before live trading can be considered."),
    "paper_trading_max_drawdown": ("0.05", "float", "Maximum paper-trading drawdown before live trading can be considered."),
}


def _parse_value(value: str | None, value_type: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    kind = (value_type or "string").lower()
    if kind == "bool":
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if kind == "int":
        try:
            return int(float(str(value).strip()))
        except Exception:
            return default
    if kind == "float":
        try:
            return float(str(value).strip())
        except Exception:
            return default
    return value


def seed_dynamic_settings(db: Session) -> None:
    for key, (value, value_type, description) in DEFAULT_SETTINGS.items():
        existing = db.get(AutomationSetting, key)
        if existing:
            continue
        db.add(AutomationSetting(key=key, value=value, value_type=value_type, description=description))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def get_setting(db: Session, key: str, default: Any = None, value_type: str | None = None) -> Any:
    row = db.get(AutomationSetting, key)
    if row and row.value is not None:
        return _parse_value(row.value, value_type or row.value_type, default)
    env_value = os.getenv(key.upper())
    if env_value is not None:
        return _parse_value(env_value, value_type, default)
    if key in DEFAULT_SETTINGS:
        value, default_type, _ = DEFAULT_SETTINGS[key]
        return _parse_value(value, value_type or default_type, default)
    return default


def get_setting_value(key: str, default: Any = None, value_type: str | None = None) -> Any:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        return get_setting(db, key, default=default, value_type=value_type)


def get_bool(db: Session, key: str, default: bool = False) -> bool:
    return bool(get_setting(db, key, default=default, value_type="bool"))


def get_int(db: Session, key: str, default: int = 0, minimum: int | None = None) -> int:
    value = int(get_setting(db, key, default=default, value_type="int") or default)
    if minimum is not None:
        value = max(minimum, value)
    return value


def get_float(db: Session, key: str, default: float = 0.0) -> float:
    return float(get_setting(db, key, default=default, value_type="float") or default)


def set_setting(db: Session, key: str, value: Any, value_type: str | None = None, description: str | None = None) -> AutomationSetting:
    row = db.get(AutomationSetting, key)
    if row is None:
        default = DEFAULT_SETTINGS.get(key)
        row = AutomationSetting(
            key=key,
            value=str(value),
            value_type=value_type or (default[1] if default else "string"),
            description=description or (default[2] if default else None),
        )
        db.add(row)
    else:
        row.value = str(value)
        if value_type:
            row.value_type = value_type
        if description is not None:
            row.description = description
        row.updated_at = datetime.utcnow()
    db.commit()
    return row


def list_settings(db: Session) -> list[dict[str, Any]]:
    seed_dynamic_settings(db)
    rows = db.scalars(select(AutomationSetting).order_by(AutomationSetting.key.asc())).all()
    return [
        {
            "key": row.key,
            "value": row.value,
            "value_type": row.value_type,
            "description": row.description,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


def automation_snapshot(db: Session, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    seed_dynamic_settings(db)
    interval = get_int(db, "automation_interval_seconds", settings.safe_automation_interval_seconds, minimum=60)
    return {
        "enabled": get_bool(db, "automation_enabled", settings.automation_enabled),
        "interval_seconds": interval,
        "automation_symbol_limit": get_int(db, "automation_symbol_limit", settings.automation_symbol_limit or settings.strategy_symbol_limit, minimum=1),
        "fetch_telegram": get_bool(db, "automation_fetch_telegram", True),
        "run_tradingview": get_bool(db, "automation_run_tradingview", True),
        "fetch_financial_data": get_bool(db, "automation_fetch_financial_data", True),
        "fetch_news_data": get_bool(db, "automation_fetch_news_data", True),
        "fetch_ohlcv_data": get_bool(db, "automation_fetch_ohlcv_data", True),
        "financial_refresh_interval_seconds": get_int(db, "financial_refresh_interval_seconds", 86400, minimum=3600),
        "news_refresh_interval_seconds": get_int(db, "news_refresh_interval_seconds", 3600, minimum=300),
        "ohlcv_refresh_interval_seconds": get_int(db, "ohlcv_refresh_interval_seconds", 600, minimum=120),
        "dynamic_data_symbol_limit": get_int(db, "dynamic_data_symbol_limit", 5, minimum=1),
        "run_strategy_legacy": get_bool(db, "automation_run_strategy_legacy", True),
        "run_cli_v6": get_bool(db, "automation_run_cli_v6", True),
        "run_final_decisions": get_bool(db, "automation_run_final_decisions", True),
        "run_opportunities": get_bool(db, "automation_run_opportunities", True),
        "send_alerts": get_bool(db, "automation_send_alerts", True),
        "update_accuracy": get_bool(db, "automation_update_accuracy", True),
        "run_ai_analysis": get_bool(db, "automation_run_ai_analysis", get_bool(db, "enable_ai_analysis", settings.enable_ai_analysis)),
        "run_portfolio_bot": get_bool(db, "automation_run_portfolio_bot", False),
        "portfolio_auto_execute": get_bool(db, "portfolio_bot_auto_execute_paper_trades", False),
        "portfolio_symbol_limit": get_int(db, "portfolio_bot_symbol_limit", 50, minimum=1),
        "analyze_images": get_bool(db, "automation_analyze_images", False),
        "telegram_download_media": get_bool(db, "telegram_download_media", False),
        "telegram_analyze_images": get_bool(db, "telegram_analyze_images", False),
        "telegram_fetch_limit": get_int(db, "telegram_fetch_limit", settings.telegram_fetch_limit_per_channel, minimum=1),
        "backtest_mode": str(get_setting(db, "backtest_mode", "opportunities_only", "string") or "opportunities_only"),
        "backtest_queue_limit": get_int(db, "backtest_queue_limit", 10, minimum=1),
        "alert_confidence_threshold": get_float(db, "alert_confidence_threshold", settings.telegram_alert_min_confidence),
    }


def combined_weights(db: Session) -> dict[str, float]:
    seed_dynamic_settings(db)
    return {
        "telegram": get_float(db, "combined_weight_telegram", 20.0),
        "strategy_legacy": get_float(db, "combined_weight_strategy_legacy", 20.0),
        "cli_v6": get_float(db, "combined_weight_cli_v6", 20.0),
        "daily_report": get_float(db, "combined_weight_daily_report", 15.0),
        "tradingview": get_float(db, "combined_weight_tradingview", 20.0),
        "backtest": get_float(db, "combined_weight_backtest", 10.0),
        "risk": get_float(db, "combined_weight_risk", 10.0),
    }
