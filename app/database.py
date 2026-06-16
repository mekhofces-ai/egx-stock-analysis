from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()
is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


if is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_with_db_retry(func, *, attempts: int = 3, delay_seconds: float = 0.25):  # noqa: ANN001, ANN201
    """Retry short-lived SQLite locked errors without hiding real failures."""
    last_error: OperationalError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except OperationalError as exc:
            message = str(exc).lower()
            if "database is locked" not in message and "database table is locked" not in message:
                raise
            last_error = exc
            logger.warning("SQLite database is locked; retrying attempt %s/%s.", attempt, attempts)
            time.sleep(delay_seconds * attempt)
    if last_error:
        raise last_error


def _sqlite_table_columns(connection, table_name: str) -> set[str]:  # noqa: ANN001
    rows = connection.exec_driver_sql(f'PRAGMA table_info("{table_name}")').mappings().all()
    return {str(row["name"]) for row in rows}


def _sqlite_table_exists(connection, table_name: str) -> bool:  # noqa: ANN001
    row = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).first()
    return row is not None


def _ensure_sqlite_schema_extensions() -> None:
    if not is_sqlite:
        return
    additions: dict[str, dict[str, str]] = {
        "stocks": {
            "name": "VARCHAR(255)",
        },
        "strategy_backtest_summary": {
            "start_date": "VARCHAR(64)",
            "end_date": "VARCHAR(64)",
            "total_return": "FLOAT",
            "max_drawdown": "FLOAT",
            "profit_factor": "FLOAT",
            "trades_count": "INTEGER",
            "avg_win": "FLOAT",
            "avg_loss": "FLOAT",
            "best_trade": "FLOAT",
            "worst_trade": "FLOAT",
            "latest_signal": "VARCHAR(32)",
            "latest_recommendation": "VARCHAR(32)",
            "win_rate": "FLOAT",
            "run_id": "VARCHAR(64)",
            "created_at": "DATETIME",
        },
        "strategy_backtest_trades": {
            "strategy_name": "VARCHAR(100)",
            "entry_date": "VARCHAR(64)",
            "exit_date": "VARCHAR(64)",
            "pnl": "FLOAT",
            "pnl_pct": "FLOAT",
            "exit_reason": "VARCHAR(100)",
            "run_id": "VARCHAR(64)",
        },
        "opportunities": {
            "source": "VARCHAR(100)",
            "is_watched": "BOOLEAN DEFAULT 0 NOT NULL",
        },
        "telegram_sent_alerts": {
            "alert_type": "VARCHAR(100)",
            "final_score": "FLOAT",
        },
        "telegram_messages": {
            "channel_id": "VARCHAR(255)",
            "channel_name": "VARCHAR(255)",
            "sender_id": "VARCHAR(255)",
            "message_text": "TEXT",
            "media_type": "VARCHAR(64)",
            "media_path": "VARCHAR(1024)",
            "symbol": "VARCHAR(32)",
            "sentiment": "VARCHAR(32)",
            "recommendation_type": "VARCHAR(64)",
            "target_price": "FLOAT",
            "stop_loss": "FLOAT",
            "timeframe": "VARCHAR(64)",
            "has_image": "BOOLEAN DEFAULT 0",
            "image_text": "TEXT",
            "message_type": "VARCHAR(64)",
        },
        "automation_runs": {
            "telegram_fetch_status": "VARCHAR(64)",
            "strategy_status": "VARCHAR(64)",
            "backtest_status": "VARCHAR(64)",
            "opportunity_status": "VARCHAR(64)",
        },
        "stock_combined_analysis": {
            "daily_report_score": "FLOAT",
        },
        "final_stock_decisions": {
            "liquidity_score": "FLOAT",
            "sector_score": "FLOAT",
            "market_regime": "VARCHAR(64)",
            "no_trade_reason": "TEXT",
        },
        "portfolio_settings": {
            "max_weekly_loss_pct": "FLOAT DEFAULT 8.0 NOT NULL",
            "max_drawdown_pct": "FLOAT DEFAULT 15.0 NOT NULL",
            "max_consecutive_losses": "INTEGER DEFAULT 3 NOT NULL",
        },
        "daily_file_reports": {
            "report_date": "DATETIME",
            "report_time": "DATETIME",
            "excel_path": "VARCHAR(1024)",
            "pdf_path": "VARCHAR(1024)",
            "excel_created": "BOOLEAN DEFAULT 0 NOT NULL",
            "pdf_created": "BOOLEAN DEFAULT 0 NOT NULL",
            "sent_to_telegram": "BOOLEAN DEFAULT 0 NOT NULL",
            "status": "VARCHAR(64) DEFAULT 'created' NOT NULL",
            "error_message": "TEXT",
            "created_at": "DATETIME",
        },
        "daily_loss_audit_items": {
            "evaluation_quality": "VARCHAR(64)",
            "market_score_at_signal": "FLOAT",
            "market_regime_at_signal": "VARCHAR(64)",
            "trade_permission_at_signal": "VARCHAR(64)",
            "should_trade_yes_no": "VARCHAR(16)",
            "time_to_target_minutes": "FLOAT",
            "time_to_stop_minutes": "FLOAT",
        },
    }
    indexes = [
        "CREATE INDEX IF NOT EXISTS ix_strategy_backtest_summary_run_id ON strategy_backtest_summary (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_backtest_trades_run_id ON strategy_backtest_trades (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_cli_v6_results_symbol ON strategy_cli_v6_results (symbol)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_cli_v6_results_created_at ON strategy_cli_v6_results (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_cli_v6_results_recommendation ON strategy_cli_v6_results (recommendation)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_cli_v6_results_run_id ON strategy_cli_v6_results (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_runs_run_id ON strategy_runs (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_runs_strategy_name ON strategy_runs (strategy_name)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_runs_status ON strategy_runs (status)",
        "CREATE INDEX IF NOT EXISTS ix_automation_runs_run_id ON automation_runs (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_automation_runs_status ON automation_runs (status)",
        "CREATE INDEX IF NOT EXISTS ix_telegram_subscribers_chat_id ON telegram_subscribers (chat_id)",
        "CREATE INDEX IF NOT EXISTS ix_telegram_subscribers_is_active ON telegram_subscribers (is_active)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_results_symbol ON strategy_results (symbol)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_results_strategy_code ON strategy_results (strategy_code)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_results_recommendation ON strategy_results (recommendation)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_results_run_id ON strategy_results (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_results_created_at ON strategy_results (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_backtest_queue_status_priority ON backtest_queue (status, priority, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_backtest_queue_symbol ON backtest_queue (symbol)",
        "CREATE INDEX IF NOT EXISTS ix_telegram_message_symbols_symbol ON telegram_message_symbols (symbol)",
        "CREATE INDEX IF NOT EXISTS ix_telegram_message_symbols_message ON telegram_message_symbols (telegram_message_id)",
        "CREATE INDEX IF NOT EXISTS ix_telegram_media_analysis_status ON telegram_media_analysis (status)",
        "CREATE INDEX IF NOT EXISTS ix_stock_combined_analysis_symbol ON stock_combined_analysis (symbol)",
        "CREATE INDEX IF NOT EXISTS ix_stock_combined_analysis_recommendation ON stock_combined_analysis (final_recommendation)",
        "CREATE INDEX IF NOT EXISTS ix_daily_egx_report_uploads_report_date ON daily_egx_report_uploads (report_date)",
        "CREATE INDEX IF NOT EXISTS ix_daily_egx_report_uploads_file_sha256 ON daily_egx_report_uploads (file_sha256)",
        "CREATE INDEX IF NOT EXISTS ix_daily_egx_report_rows_symbol ON daily_egx_report_rows (symbol)",
        "CREATE INDEX IF NOT EXISTS ix_daily_egx_report_rows_report_date ON daily_egx_report_rows (report_date)",
        "CREATE INDEX IF NOT EXISTS ix_daily_egx_report_rows_recommendation ON daily_egx_report_rows (recommendation)",
        "CREATE INDEX IF NOT EXISTS ix_daily_egx_report_rows_score ON daily_egx_report_rows (report_score)",
        "CREATE INDEX IF NOT EXISTS ix_ohlcv_data_symbol_datetime ON ohlcv_data (symbol, datetime)",
        "CREATE INDEX IF NOT EXISTS ix_technical_signals_symbol_date ON technical_signals (symbol, signal_date)",
        "CREATE INDEX IF NOT EXISTS ix_financial_data_symbol_period ON financial_data (symbol, period)",
        "CREATE INDEX IF NOT EXISTS ix_financial_signals_symbol_date ON financial_signals (symbol, signal_date)",
        "CREATE INDEX IF NOT EXISTS ix_stock_news_symbol_published ON stock_news (symbol, published_at)",
        "CREATE INDEX IF NOT EXISTS ix_news_signals_symbol_date ON news_signals (symbol, signal_date)",
        "CREATE INDEX IF NOT EXISTS ix_telegram_signals_symbol_date ON telegram_signals (symbol, signal_date)",
        "CREATE INDEX IF NOT EXISTS ix_telegram_channel_performance_channel_symbol ON telegram_channel_performance (channel_name, symbol)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_signals_symbol_strategy_date ON strategy_signals (symbol, strategy_name, signal_date)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_performance_strategy_symbol ON strategy_performance (strategy_name, symbol)",
        "CREATE INDEX IF NOT EXISTS ix_final_stock_decisions_symbol_date ON final_stock_decisions (symbol, decision_date)",
        "CREATE INDEX IF NOT EXISTS ix_final_stock_decisions_signal_score ON final_stock_decisions (final_signal, final_score)",
        "CREATE INDEX IF NOT EXISTS ix_signal_accuracy_symbol_decision ON signal_accuracy_tracking (symbol, decision_date)",
        "CREATE INDEX IF NOT EXISTS ix_dynamic_weights_symbol ON dynamic_weights_by_symbol (symbol)",
        "CREATE INDEX IF NOT EXISTS ix_portfolio_positions_status_symbol ON portfolio_positions (status, symbol)",
        "CREATE INDEX IF NOT EXISTS ix_portfolio_trades_symbol_date ON portfolio_trades (symbol, trade_date)",
        "CREATE INDEX IF NOT EXISTS ix_market_regime_snapshots_created_at ON market_regime_snapshots (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_sector_analysis_snapshots_sector_created ON sector_analysis_snapshots (sector, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_liquidity_snapshots_symbol_created ON liquidity_snapshots (symbol, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_trade_approvals_status_symbol ON trade_approvals (status, symbol)",
        "CREATE INDEX IF NOT EXISTS ix_no_trade_reasons_symbol_date ON no_trade_reasons (symbol, decision_date)",
        "CREATE INDEX IF NOT EXISTS ix_mistake_reviews_symbol_created ON mistake_reviews (symbol, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_confidence_calibration_type_bucket ON confidence_calibration (analysis_type, bucket)",
        "CREATE INDEX IF NOT EXISTS ix_recommendation_reports_type_time ON recommendation_reports (report_type, report_time)",
        "CREATE INDEX IF NOT EXISTS ix_recommendation_reports_status_created ON recommendation_reports (status, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_recommendation_items_report_score ON recommendation_items (report_id, final_score)",
        "CREATE INDEX IF NOT EXISTS ix_recommendation_items_symbol_score ON recommendation_items (symbol, final_score)",
        "CREATE INDEX IF NOT EXISTS ix_daily_file_reports_report_date ON daily_file_reports (report_date)",
        "CREATE INDEX IF NOT EXISTS ix_daily_file_reports_status_created ON daily_file_reports (status, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_daily_loss_audit_items_quality ON daily_loss_audit_items (evaluation_quality)",
        "CREATE INDEX IF NOT EXISTS ix_daily_loss_audit_items_market ON daily_loss_audit_items (market_regime_at_signal, trade_permission_at_signal)",
        "CREATE INDEX IF NOT EXISTS ix_market_daily_evaluations_date ON market_daily_evaluations (evaluation_date)",
        "CREATE INDEX IF NOT EXISTS ix_market_daily_evaluations_regime ON market_daily_evaluations (market_regime, trade_permission)",
        "CREATE INDEX IF NOT EXISTS ix_live_trade_execution_logs_symbol_status ON live_trade_execution_logs (symbol, execution_status)",
        "CREATE INDEX IF NOT EXISTS ix_repeated_recommendation_audit_symbol_run ON repeated_recommendation_audit (symbol, run_id)",
        "CREATE INDEX IF NOT EXISTS ix_recommendation_evaluations_item ON recommendation_evaluations (recommendation_item_id)",
        "CREATE INDEX IF NOT EXISTS ix_recommendation_evaluations_symbol_status ON recommendation_evaluations (symbol, final_status)",
        "CREATE INDEX IF NOT EXISTS ix_recommendation_evaluations_evaluated_at ON recommendation_evaluations (evaluated_at)",
        "CREATE INDEX IF NOT EXISTS ix_recommendation_evaluations_stage ON recommendation_evaluations (recommendation_stage)",
        "CREATE INDEX IF NOT EXISTS ix_recommendation_evaluations_strategy ON recommendation_evaluations (strategy_source)",
        "CREATE INDEX IF NOT EXISTS ix_end_of_day_review_reports_date ON end_of_day_review_reports (review_date)",
        "CREATE INDEX IF NOT EXISTS ix_end_of_day_review_items_type_symbol ON end_of_day_review_items (row_type, symbol)",
        "CREATE INDEX IF NOT EXISTS ix_end_of_day_review_items_classification ON end_of_day_review_items (classification)",
        "CREATE INDEX IF NOT EXISTS ix_decision_snapshots_symbol_time ON decision_snapshots (symbol, snapshot_time)",
        "CREATE INDEX IF NOT EXISTS ix_decision_snapshots_decision ON decision_snapshots (decision, selected_rejected)",
        "CREATE INDEX IF NOT EXISTS ix_walk_forward_periods_run_period ON walk_forward_periods (run_id, period_index)",
        "CREATE INDEX IF NOT EXISTS ix_intraday_scan_items_event_symbol ON intraday_scan_items (event_type, symbol)",
        "CREATE INDEX IF NOT EXISTS ix_intraday_scan_runs_time_type ON intraday_scan_runs (scan_time, scan_type)",
        "CREATE INDEX IF NOT EXISTS ix_source_accuracy_snapshots_source_time ON source_accuracy_snapshots (source_type, source_name, as_of)",
        "CREATE INDEX IF NOT EXISTS ix_pump_risk_snapshots_symbol_time ON pump_risk_snapshots (symbol, as_of)",
        "CREATE INDEX IF NOT EXISTS ix_pump_risk_snapshots_level ON pump_risk_snapshots (risk_level, pump_risk_score)",
        "CREATE INDEX IF NOT EXISTS ix_risk_expectancy_snapshots_scope_time ON risk_expectancy_snapshots (scope, as_of)",
        "CREATE INDEX IF NOT EXISTS ix_recommendation_quality_symbol_grade ON recommendation_quality_snapshots (symbol, quality_grade)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_learning_reports_date ON strategy_learning_reports (report_date)",
    ]
    with engine.begin() as connection:
        for table_name, columns in additions.items():
            if not _sqlite_table_exists(connection, table_name):
                continue
            existing = _sqlite_table_columns(connection, table_name)
            for column_name, sql_type in columns.items():
                if column_name not in existing:
                    connection.exec_driver_sql(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {sql_type}')
        for statement in indexes:
            connection.exec_driver_sql(statement)


def _sqlite_path_from_url() -> Path | None:
    if not is_sqlite or settings.database_url == "sqlite:///:memory:":
        return None
    prefix = "sqlite:///"
    if settings.database_url.startswith(prefix):
        return Path(settings.database_url.replace(prefix, "", 1)).resolve()
    return None


@contextmanager
def sqlite_write_lock(timeout_seconds: float = 60.0) -> Generator[None, None, None]:
    db_path = _sqlite_path_from_url()
    if db_path is None:
        yield
        return
    lock_path = db_path.with_suffix(db_path.suffix + ".write.lock")
    start = time.monotonic()
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            break
        except FileExistsError:
            if time.monotonic() - start > timeout_seconds:
                raise TimeoutError(f"Timed out waiting for SQLite write lock: {lock_path}")
            try:
                if time.time() - lock_path.stat().st_mtime > timeout_seconds * 2:
                    lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.25)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove SQLite write lock: %s", lock_path)


SAMPLE_STOCKS = [
    {"symbol": "COMI", "name_ar": "البنك التجاري الدولي", "name_en": "Commercial International Bank", "sector": "Banks", "tradingview_symbol": "EGX:COMI"},
    {"symbol": "HRHO", "name_ar": "اي اف جي القابضة", "name_en": "EFG Holding", "sector": "Financial Services", "tradingview_symbol": "EGX:HRHO"},
    {"symbol": "TMGH", "name_ar": "طلعت مصطفى القابضة", "name_en": "Talaat Moustafa Group", "sector": "Real Estate", "tradingview_symbol": "EGX:TMGH"},
    {"symbol": "ORAS", "name_ar": "اوراسكوم كونستراكشون", "name_en": "Orascom Construction", "sector": "Construction", "tradingview_symbol": "EGX:ORAS"},
    {"symbol": "FWRY", "name_ar": "فوري", "name_en": "Fawry", "sector": "Technology", "tradingview_symbol": "EGX:FWRY"},
    {"symbol": "ABUK", "name_ar": "ابوقير للاسمدة", "name_en": "Abou Kir Fertilizers", "sector": "Fertilizers", "tradingview_symbol": "EGX:ABUK"},
    {"symbol": "ESRS", "name_ar": "حديد عز", "name_en": "Ezz Steel", "sector": "Basic Materials", "tradingview_symbol": "EGX:ESRS"},
    {"symbol": "SWDY", "name_ar": "السويدي اليكتريك", "name_en": "Elsewedy Electric", "sector": "Industrials", "tradingview_symbol": "EGX:SWDY"},
    {"symbol": "ETEL", "name_ar": "المصرية للاتصالات", "name_en": "Telecom Egypt", "sector": "Telecom", "tradingview_symbol": "EGX:ETEL"},
    {"symbol": "EFIH", "name_ar": "اي فاينانس", "name_en": "e-finance", "sector": "Technology", "tradingview_symbol": "EGX:EFIH"},
]


def init_db(seed: bool = True) -> None:
    from app.models import AutomationSetting, PortfolioSetting, Stock, StrategyDefinition, TelegramSource, TelegramSubscriber

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_schema_extensions()
    if not seed:
        return

    with SessionLocal() as db:
        for row in SAMPLE_STOCKS:
            existing = db.scalar(select(Stock).where(Stock.symbol == row["symbol"]))
            if not existing:
                db.add(Stock(**row, name=row.get("name_en")))
            elif not existing.name:
                existing.name = existing.name_en or existing.name_ar

        for username in settings.source_channels:
            existing = db.scalar(select(TelegramSource).where(TelegramSource.username == username))
            if not existing:
                db.add(
                    TelegramSource(
                        username=username,
                        title=username,
                        source_type="channel",
                        is_active=True,
                        trust_score=50.0,
                        notes="Seeded from TELEGRAM_SOURCE_CHANNELS.",
                    )
                )

        default_settings = {
            "automation_enabled": ("true" if settings.automation_enabled else "false", "bool", "Enable continuous automation runner."),
            "automation_interval_seconds": (str(settings.safe_automation_interval_seconds), "int", "Automation loop interval, minimum 60 seconds."),
            "automation_symbol_limit": ("3", "int", "Maximum active stock symbols processed by each automation strategy/final-decision cycle."),
            "automation_fetch_telegram": ("true", "bool", "Fetch new Telegram messages during automation."),
            "automation_run_tradingview": ("true", "bool", "Run TradingView screening during automation."),
            "automation_fetch_financial_data": ("true", "bool", "Refresh TradingView financial snapshots during automation when due."),
            "automation_fetch_news_data": ("true", "bool", "Refresh free RSS news during automation when due."),
            "automation_fetch_ohlcv_data": ("true", "bool", "Refresh TradingView OHLCV candles during automation when due."),
            "financial_refresh_interval_seconds": ("86400", "int", "Minimum seconds between automatic financial snapshot refreshes."),
            "news_refresh_interval_seconds": ("3600", "int", "Minimum seconds between automatic news RSS refreshes."),
            "ohlcv_refresh_interval_seconds": ("600", "int", "Minimum seconds between automatic OHLCV refreshes."),
            "dynamic_data_symbol_limit": ("5", "int", "Maximum symbols per automatic dynamic data refresh."),
            "dynamic_data_timeframes": ("1d,1h", "string", "Comma-separated OHLCV timeframes refreshed from TradingView."),
            "news_rss_query_template": ("{symbol} EGX OR {name} Egypt", "string", "Google News RSS query template."),
            "news_rss_max_items_per_symbol": ("5", "int", "Maximum RSS news items stored per symbol per refresh."),
            "automation_run_strategy_legacy": ("true", "bool", "Run legacy strategy when requested by automation."),
            "automation_run_cli_v6": ("true", "bool", "Run CLI v6 strategy during automation."),
            "automation_run_final_decisions": ("true", "bool", "Run final weighted decision engine during automation."),
            "automation_run_opportunities": ("true", "bool", "Refresh opportunities during automation."),
            "automation_send_alerts": ("true", "bool", "Send Telegram alerts for new eligible signals."),
            "automation_update_accuracy": ("true", "bool", "Update signal accuracy tracking when later prices exist."),
            "automation_run_ai_analysis": ("false", "bool", "Run optional AI analysis during automation."),
            "enable_ai_analysis": ("false", "bool", "Enable optional AI analysis features."),
            "automation_run_portfolio_bot": ("false", "bool", "Run paper portfolio scan during automation."),
            "telegram_analyze_images": ("false", "bool", "Enable OCR/media image analysis for new Telegram images."),
            "telegram_download_media": ("false", "bool", "Download supported image media from Telegram."),
            "backtest_mode": ("opportunities_only", "string", "Backtest schedule: manual_only, hourly, daily, opportunities_only."),
            "backtest_queue_limit": ("10", "int", "Maximum pending backtest queue items per run."),
            "alert_confidence_threshold": (str(settings.telegram_alert_min_confidence), "float", "Minimum score/confidence for alerts."),
            "telegram_fetch_limit": (str(settings.telegram_fetch_limit_per_channel), "int", "Telegram fetch limit per active source."),
            "data_provider_priority": (settings.market_data_provider_priority, "string", "Market data provider priority."),
            "combined_weight_telegram": ("20", "float", "Combined analysis weight for Telegram signals."),
            "combined_weight_strategy_legacy": ("20", "float", "Combined analysis weight for legacy strategy."),
            "combined_weight_cli_v6": ("20", "float", "Combined analysis weight for CLI v6 strategy."),
            "combined_weight_tradingview": ("20", "float", "Combined analysis weight for TradingView screener."),
            "combined_weight_backtest": ("10", "float", "Combined analysis weight for backtest quality."),
            "combined_weight_risk": ("10", "float", "Combined analysis weight for freshness and risk."),
            "final_weight_technical": ("35", "float", "Final decision weight for technical analysis."),
            "final_weight_financial": ("25", "float", "Final decision weight for financial analysis."),
            "final_weight_news": ("20", "float", "Final decision weight for news analysis."),
            "final_weight_telegram": ("10", "float", "Final decision weight for Telegram analysis."),
            "final_weight_strategy": ("10", "float", "Final decision weight for strategy analysis."),
            "accuracy_bullish_threshold_pct": ("1.0", "float", "Minimum positive move to count a bullish signal as correct."),
            "accuracy_bearish_threshold_pct": ("-1.0", "float", "Maximum move to count a bearish or avoid signal as correct."),
            "portfolio_bot_enabled": ("false", "bool", "Enable the paper portfolio bot."),
            "portfolio_bot_auto_execute_paper_trades": ("false", "bool", "Allow automation to execute paper BUY/SELL trades."),
            "portfolio_bot_symbol_limit": ("50", "int", "Maximum symbols scanned by the paper portfolio bot per cycle."),
            "portfolio_initial_cash": ("100000", "float", "Initial paper trading cash in EGP."),
            "portfolio_buy_threshold": ("70", "float", "Minimum final score for a paper BUY."),
            "portfolio_sell_threshold": ("45", "float", "Final score below this exits an open paper position."),
            "portfolio_manual_confirmation": ("true", "bool", "Require manual confirmation before portfolio buys and sells."),
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
        for key, (value, value_type, description) in default_settings.items():
            existing = db.get(AutomationSetting, key)
            if not existing:
                db.add(AutomationSetting(key=key, value=value, value_type=value_type, description=description))

        strategy_rows = [
            {
                "strategy_code": "strategy_legacy",
                "strategy_name": "Legacy Multi-Timeframe Strategy",
                "description": "Existing EMA/RSI/MACD multi-timeframe strategy.",
                "default_timeframe": "15m,1h,4h,1D",
                "config_json": {"source": "app.services.strategy", "kept": True},
            },
            {
                "strategy_code": "cli_v6_egx",
                "strategy_name": "CLI v6 EGX",
                "description": "Composite Leading Indicator v6 EGX optimized strategy translated from TradingView Pine Script.",
                "default_timeframe": "15m,30m,1h,4h,1d",
                "config_json": {"source": "app.services.strategies.cli_v6_egx", "version": "v6"},
            },
        ]
        for row in strategy_rows:
            existing = db.scalar(select(StrategyDefinition).where(StrategyDefinition.strategy_code == row["strategy_code"]))
            if existing:
                existing.strategy_name = row["strategy_name"]
                existing.description = row["description"]
                existing.default_timeframe = row["default_timeframe"]
                existing.config_json = row["config_json"]
            else:
                db.add(StrategyDefinition(**row))

        if settings.telegram_bot_private_chat_id is not None:
            chat_id = str(settings.telegram_bot_private_chat_id)
            existing = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == chat_id))
            if existing:
                existing.role = "admin"
                existing.is_active = True
                existing.can_use_bot = True
                existing.can_receive_alerts = True
                existing.notes = existing.notes or "Seeded as admin from private chat id setting."
            else:
                db.add(
                    TelegramSubscriber(
                        chat_id=chat_id,
                        chat_type="private",
                        display_name="Configured admin",
                        role="admin",
                        is_active=True,
                        can_receive_alerts=True,
                        can_use_bot=True,
                        notes="Seeded as admin from private chat id setting.",
                    )
                )
        if not db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc())):
            db.add(PortfolioSetting(timezone=settings.timezone))
        db.commit()
