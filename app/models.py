from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class TelegramSource(Base, TimestampMixin):
    __tablename__ = "telegram_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(50), default="channel", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trust_score: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    last_message_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    messages: Mapped[list["TelegramMessage"]] = relationship(back_populates="source", cascade="all, delete-orphan", lazy="noload")
    extracted_signals: Mapped[list["ExtractedSignal"]] = relationship(back_populates="source")
    performance: Mapped["ChannelPerformance | None"] = relationship(back_populates="source", uselist=False)


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    __table_args__ = (UniqueConstraint("source_id", "message_id", name="uq_telegram_message_source_message"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("telegram_sources.id"), index=True, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_date: Mapped[datetime | None] = mapped_column(DateTime)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String(255))
    channel_name: Mapped[str | None] = mapped_column(String(255))
    sender_id: Mapped[str | None] = mapped_column(String(255))
    message_text: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(64))
    media_path: Mapped[str | None] = mapped_column(String(1024))
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    image_path: Mapped[str | None] = mapped_column(String(1024))
    image_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    sentiment: Mapped[str | None] = mapped_column(String(32))
    recommendation_type: Mapped[str | None] = mapped_column(String(64))
    target_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    timeframe: Mapped[str | None] = mapped_column(String(64))
    has_image: Mapped[bool | None] = mapped_column(Boolean, default=False)
    image_text: Mapped[str | None] = mapped_column(Text)
    message_type: Mapped[str | None] = mapped_column(String(64))
    parsed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    source: Mapped[TelegramSource] = relationship(back_populates="messages")
    extracted_signals: Mapped[list["ExtractedSignal"]] = relationship(back_populates="telegram_message")


class Stock(Base, TimestampMixin):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    name_ar: Mapped[str | None] = mapped_column(String(255))
    name_en: Mapped[str | None] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(255))
    tradingview_symbol: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MarketPrice(Base):
    __tablename__ = "market_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(32), default="1D", nullable=False)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(100), default="unknown", nullable=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DailyEGXReportUpload(Base):
    __tablename__ = "daily_egx_report_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(100), default="daily_excel", index=True, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    report_date: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    rows_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="success", index=True, nullable=False)
    file_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)

    rows: Mapped[list["DailyEGXReportRow"]] = relationship(back_populates="upload", cascade="all, delete-orphan")


class DailyEGXReportRow(Base):
    __tablename__ = "daily_egx_report_rows"
    __table_args__ = (UniqueConstraint("upload_id", "symbol", name="uq_daily_egx_report_upload_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("daily_egx_report_uploads.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(64))
    report_date: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    buy_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    target1: Mapped[float | None] = mapped_column(Float)
    target2: Mapped[float | None] = mapped_column(Float)
    status_text: Mapped[str | None] = mapped_column(String(100))
    short_term: Mapped[str | None] = mapped_column(String(100))
    medium_term: Mapped[str | None] = mapped_column(String(100))
    performance: Mapped[str | None] = mapped_column(String(100))
    weight: Mapped[str | None] = mapped_column(String(100))
    mode: Mapped[str | None] = mapped_column(String(100))
    signal: Mapped[str | None] = mapped_column(String(100), index=True)
    week52_high: Mapped[float | None] = mapped_column(Float)
    week52_low: Mapped[float | None] = mapped_column(Float)
    final_arbitration: Mapped[str | None] = mapped_column(Text)
    report_score: Mapped[float | None] = mapped_column(Float, index=True)
    recommendation: Mapped[str | None] = mapped_column(String(32), index=True)
    risk_reward: Mapped[float | None] = mapped_column(Float)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)

    upload: Mapped[DailyEGXReportUpload] = relationship(back_populates="rows")


class ExtractedSignal(Base):
    __tablename__ = "extracted_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_sources.id"), index=True)
    telegram_message_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_messages.id"), index=True)
    stock_symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    stock_name: Mapped[str | None] = mapped_column(String(255))
    direction: Mapped[str | None] = mapped_column(String(32))
    entry_price: Mapped[float | None] = mapped_column(Float)
    targets: Mapped[list[float] | None] = mapped_column(JSON)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    support: Mapped[float | None] = mapped_column(Float)
    resistance: Mapped[float | None] = mapped_column(Float)
    timeframe: Mapped[str | None] = mapped_column(String(64))
    hype_words: Mapped[list[str] | None] = mapped_column(JSON)
    risk_flags: Mapped[list[str] | None] = mapped_column(JSON)
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending_analysis", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    source: Mapped[TelegramSource | None] = relationship(back_populates="extracted_signals")
    telegram_message: Mapped[TelegramMessage | None] = relationship(back_populates="extracted_signals")
    final_analysis: Mapped["FinalAnalysis | None"] = relationship(back_populates="extracted_signal", uselist=False)


class TechnicalAnalysis(Base):
    __tablename__ = "technical_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(32), default="1D", nullable=False)
    indicators: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    trend_direction: Mapped[str | None] = mapped_column(String(64))
    volatility_score: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    technical_score: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[float | None] = mapped_column(Float)
    support: Mapped[float | None] = mapped_column(Float)
    resistance: Mapped[float | None] = mapped_column(Float)
    breakout: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider: Mapped[str] = mapped_column(String(100), default="unknown", nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    final_analyses: Mapped[list["FinalAnalysis"]] = relationship(back_populates="technical_analysis")


class FinalAnalysis(Base):
    __tablename__ = "final_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    extracted_signal_id: Mapped[int | None] = mapped_column(ForeignKey("extracted_signals.id"), index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_sources.id"), index=True)
    technical_analysis_id: Mapped[int | None] = mapped_column(ForeignKey("technical_analysis.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    final_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    entry_zone: Mapped[str | None] = mapped_column(String(255))
    stop_loss: Mapped[float | None] = mapped_column(Float)
    targets: Mapped[list[float] | None] = mapped_column(JSON)
    reasons: Mapped[list[str] | None] = mapped_column(JSON)
    warnings: Mapped[list[str] | None] = mapped_column(JSON)
    invalidation_point: Mapped[str | None] = mapped_column(String(255))
    position_size_suggestion: Mapped[str | None] = mapped_column(String(255))
    last_price: Mapped[float | None] = mapped_column(Float)
    trend: Mapped[str | None] = mapped_column(String(64))
    disclaimer: Mapped[str] = mapped_column(String(255), default="Not financial advice.", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    extracted_signal: Mapped[ExtractedSignal | None] = relationship(back_populates="final_analysis")
    technical_analysis: Mapped[TechnicalAnalysis | None] = relationship(back_populates="final_analyses")


class ChannelPerformance(Base):
    __tablename__ = "channel_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("telegram_sources.id"), unique=True, index=True, nullable=False)
    total_signals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Float)
    fake_signal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_confidence: Mapped[float | None] = mapped_column(Float)
    best_symbols: Mapped[list[str] | None] = mapped_column(JSON)
    worst_symbols: Mapped[list[str] | None] = mapped_column(JSON)
    stop_loss_missing_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pump_words_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    source: Mapped[TelegramSource] = relationship(back_populates="performance")


class BotUser(Base, TimestampMixin):
    __tablename__ = "bot_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TelegramSubscriber(Base):
    __tablename__ = "telegram_subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    chat_type: Mapped[str | None] = mapped_column(String(64))
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_receive_alerts: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_use_bot: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allowed_symbols: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    last_message_status: Mapped[str | None] = mapped_column(String(64))
    last_message_error: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime)
    subscribed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AppSetting(Base, TimestampMixin):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text)


class AutomationSetting(Base):
    __tablename__ = "automation_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(String(32), default="string", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class JobsLog(Base):
    __tablename__ = "jobs_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    details: Mapped[str | None] = mapped_column(Text)


class StrategyRun(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True, nullable=False)
    symbols_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class StrategyDefinition(Base, TimestampMixin):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_timeframe: Mapped[str | None] = mapped_column(String(32))
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class StrategyResult(Base):
    __tablename__ = "strategy_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(255), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    timeframe: Mapped[str | None] = mapped_column(String(32), index=True)
    signal: Mapped[str | None] = mapped_column(String(64))
    recommendation: Mapped[str | None] = mapped_column(String(64), index=True)
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    trend: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class StrategyCliV6Result(Base):
    __tablename__ = "strategy_cli_v6_results"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "run_id", name="uq_cli_v6_symbol_timeframe_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    timeframe: Mapped[str | None] = mapped_column(String(32))
    total_score: Mapped[float | None] = mapped_column(Float)
    leading_score: Mapped[float | None] = mapped_column(Float)
    lagging_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(32))
    recommendation: Mapped[str | None] = mapped_column(String(32), index=True)
    recommendation_ar: Mapped[str | None] = mapped_column(String(64))
    bullish_count: Mapped[int | None] = mapped_column(Integer)
    bearish_count: Mapped[int | None] = mapped_column(Integer)
    neutral_count: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class StrategyBacktest(Base):
    __tablename__ = "strategy_backtests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), default="multi_timeframe_ema_rsi_macd", nullable=False)
    timeframe: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(100), default="unknown", nullable=False)
    data_quality: Mapped[str] = mapped_column(String(100), default="unknown", nullable=False)
    total_return_pct: Mapped[float | None] = mapped_column(Float)
    annualized_return_pct: Mapped[float | None] = mapped_column(Float)
    sharpe_like: Mapped[float | None] = mapped_column(Float)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float)
    win_rate: Mapped[float | None] = mapped_column(Float)
    avg_win_pct: Mapped[float | None] = mapped_column(Float)
    avg_loss_pct: Mapped[float | None] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    trades_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    best_trade_pct: Mapped[float | None] = mapped_column(Float)
    worst_trade_pct: Mapped[float | None] = mapped_column(Float)
    latest_signal: Mapped[str | None] = mapped_column(String(32))
    equity_curve: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    trades: Mapped[list["StrategyBacktestTrade"]] = relationship(back_populates="backtest", cascade="all, delete-orphan")


class StrategyBacktestTrade(Base):
    __tablename__ = "strategy_backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backtest_id: Mapped[int] = mapped_column(ForeignKey("strategy_backtests.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    strategy_name: Mapped[str | None] = mapped_column(String(100))
    timeframe: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime)
    entry_date: Mapped[str | None] = mapped_column(String(64))
    exit_date: Mapped[str | None] = mapped_column(String(64))
    entry_price: Mapped[float | None] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float)
    pnl_pct: Mapped[float | None] = mapped_column(Float)
    return_pct: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(100))
    exit_reason: Mapped[str | None] = mapped_column(String(100))
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    bars_held: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    backtest: Mapped[StrategyBacktest] = relationship(back_populates="trades")


class StrategyBacktestSummary(Base):
    __tablename__ = "strategy_backtest_summary"
    __table_args__ = (UniqueConstraint("symbol", "strategy_name", "timeframe", name="uq_strategy_backtest_summary_symbol_strategy_timeframe"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), default="multi_timeframe_ema_rsi_macd", nullable=False)
    timeframe: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    recommendation: Mapped[str | None] = mapped_column(String(32))
    start_date: Mapped[str | None] = mapped_column(String(64))
    end_date: Mapped[str | None] = mapped_column(String(64))
    total_return: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    trades_count: Mapped[int | None] = mapped_column(Integer)
    avg_win: Mapped[float | None] = mapped_column(Float)
    avg_loss: Mapped[float | None] = mapped_column(Float)
    best_trade: Mapped[float | None] = mapped_column(Float)
    worst_trade: Mapped[float | None] = mapped_column(Float)
    latest_signal: Mapped[str | None] = mapped_column(String(32))
    latest_recommendation: Mapped[str | None] = mapped_column(String(32))
    win_rate: Mapped[float | None] = mapped_column(Float)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class BacktestQueue(Base):
    __tablename__ = "backtest_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(Text)


class TradingViewScreeningRun(Base):
    __tablename__ = "tradingview_screening_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    provider_warning: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    symbols_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    results: Mapped[list["TradingViewScreeningResult"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class TradingViewScreeningResult(Base):
    __tablename__ = "tradingview_screening_results"
    __table_args__ = (UniqueConstraint("run_id", "symbol", name="uq_tradingview_screening_result_run_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("tradingview_screening_runs.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    recommendation: Mapped[str | None] = mapped_column(String(32))
    final_score: Mapped[float | None] = mapped_column(Float)
    tv_vote: Mapped[str | None] = mapped_column(String(32))
    telegram_vote: Mapped[str | None] = mapped_column(String(32))
    close: Mapped[float | None] = mapped_column(Float)
    change_percent: Mapped[float | None] = mapped_column(Float)
    rsi: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    technical_rating: Mapped[float | None] = mapped_column(Float)
    moving_averages_rating: Mapped[float | None] = mapped_column(Float)
    oscillators_rating: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    run: Mapped[TradingViewScreeningRun] = relationship(back_populates="results")


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    final_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(32), default="NEUTRAL", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    entry_price: Mapped[float | None] = mapped_column(Float)
    target_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    components_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source: Mapped[str | None] = mapped_column(String(100))
    is_watched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class TelegramSentAlert(Base):
    __tablename__ = "telegram_sent_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    alert_type: Mapped[str | None] = mapped_column(String(100))
    recommendation: Mapped[str | None] = mapped_column(String(32))
    final_score: Mapped[float | None] = mapped_column(Float)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class TelegramMessageSymbol(Base):
    __tablename__ = "telegram_message_symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_message_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_messages.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class TelegramMediaAnalysis(Base):
    __tablename__ = "telegram_media_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_message_id: Mapped[int | None] = mapped_column(ForeignKey("telegram_messages.id"), index=True)
    media_path: Mapped[str | None] = mapped_column(String(1024))
    media_type: Mapped[str | None] = mapped_column(String(64))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    detected_symbols: Mapped[str | None] = mapped_column(Text)
    analysis_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(64), default="pending", index=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class StockCombinedAnalysis(Base):
    __tablename__ = "stock_combined_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    final_recommendation: Mapped[str] = mapped_column(String(64), default="NEUTRAL", index=True, nullable=False)
    final_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    telegram_score: Mapped[float | None] = mapped_column(Float)
    strategy_legacy_score: Mapped[float | None] = mapped_column(Float)
    strategy_cli_v6_score: Mapped[float | None] = mapped_column(Float)
    daily_report_score: Mapped[float | None] = mapped_column(Float)
    tradingview_score: Mapped[float | None] = mapped_column(Float)
    backtest_score: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    components_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    telegram_fetch_status: Mapped[str | None] = mapped_column(String(64))
    strategy_status: Mapped[str | None] = mapped_column(String(64))
    backtest_status: Mapped[str | None] = mapped_column(String(64))
    opportunity_status: Mapped[str | None] = mapped_column(String(64))
    symbols_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opportunities_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alerts_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class AutomationState(Base):
    __tablename__ = "automation_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class OHLCVData(Base):
    __tablename__ = "ohlcv_data"
    __table_args__ = (UniqueConstraint("symbol", "datetime", name="uq_ohlcv_symbol_datetime"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    datetime: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(100), default="manual", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class TechnicalSignal(Base):
    __tablename__ = "technical_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    signal_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    signal: Mapped[str] = mapped_column(String(32), default="HOLD", index=True, nullable=False)
    technical_score: Mapped[float | None] = mapped_column(Float)
    entry_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit_1: Mapped[float | None] = mapped_column(Float)
    take_profit_2: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str | None] = mapped_column(String(32))
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class FinancialData(Base):
    __tablename__ = "financial_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    period: Mapped[str | None] = mapped_column(String(64), index=True)
    revenue: Mapped[float | None] = mapped_column(Float)
    gross_profit: Mapped[float | None] = mapped_column(Float)
    net_profit: Mapped[float | None] = mapped_column(Float)
    ebitda: Mapped[float | None] = mapped_column(Float)
    eps: Mapped[float | None] = mapped_column(Float)
    assets: Mapped[float | None] = mapped_column(Float)
    liabilities: Mapped[float | None] = mapped_column(Float)
    equity: Mapped[float | None] = mapped_column(Float)
    debt: Mapped[float | None] = mapped_column(Float)
    cash_flow: Mapped[float | None] = mapped_column(Float)
    market_price: Mapped[float | None] = mapped_column(Float)
    shares_outstanding: Mapped[float | None] = mapped_column(Float)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class FinancialSignal(Base):
    __tablename__ = "financial_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    signal_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    financial_signal: Mapped[str] = mapped_column(String(32), default="NEUTRAL", index=True, nullable=False)
    financial_score: Mapped[float | None] = mapped_column(Float)
    profitability_score: Mapped[float | None] = mapped_column(Float)
    growth_score: Mapped[float | None] = mapped_column(Float)
    valuation_score: Mapped[float | None] = mapped_column(Float)
    debt_score: Mapped[float | None] = mapped_column(Float)
    cashflow_score: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str | None] = mapped_column(String(32))


class StockNews(Base):
    __tablename__ = "stock_news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(255), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    sentiment: Mapped[str | None] = mapped_column(String(32), index=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    impact_score: Mapped[float | None] = mapped_column(Float)
    expected_impact_duration: Mapped[str | None] = mapped_column(String(64))
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class NewsSignal(Base):
    __tablename__ = "news_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    signal_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    news_signal: Mapped[str] = mapped_column(String(32), default="NEUTRAL", index=True, nullable=False)
    news_score: Mapped[float | None] = mapped_column(Float)
    main_news_drivers: Mapped[list[str] | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)


class TelegramSignal(Base):
    __tablename__ = "telegram_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    signal_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    telegram_signal: Mapped[str] = mapped_column(String(32), default="NEUTRAL", index=True, nullable=False)
    telegram_score: Mapped[float | None] = mapped_column(Float)
    top_channels: Mapped[list[str] | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)


class TelegramChannelPerformance(Base):
    __tablename__ = "telegram_channel_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    total_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correct_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wrong_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Float)
    average_return: Mapped[float | None] = mapped_column(Float)
    best_symbol: Mapped[str | None] = mapped_column(String(32))
    worst_symbol: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True, nullable=False)


class StrategySignal(Base):
    __tablename__ = "strategy_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    signal_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    signal: Mapped[str] = mapped_column(String(32), default="HOLD", index=True, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    entry_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit_1: Mapped[float | None] = mapped_column(Float)
    take_profit_2: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)


class StrategyPerformance(Base):
    __tablename__ = "strategy_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    total_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    average_return: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True, nullable=False)


class FinalStockDecision(Base):
    __tablename__ = "final_stock_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    decision_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    technical_score: Mapped[float | None] = mapped_column(Float)
    financial_score: Mapped[float | None] = mapped_column(Float)
    news_score: Mapped[float | None] = mapped_column(Float)
    telegram_score: Mapped[float | None] = mapped_column(Float)
    strategy_score: Mapped[float | None] = mapped_column(Float)
    final_score: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    sector_score: Mapped[float | None] = mapped_column(Float)
    market_regime: Mapped[str | None] = mapped_column(String(64))
    no_trade_reason: Mapped[str | None] = mapped_column(Text)
    final_signal: Mapped[str] = mapped_column(String(32), default="WATCH", index=True, nullable=False)
    best_analysis_today: Mapped[str | None] = mapped_column(String(64))
    best_strategy_today: Mapped[str | None] = mapped_column(String(100))
    entry_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit_1: Mapped[float | None] = mapped_column(Float)
    take_profit_2: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str | None] = mapped_column(String(32))
    components_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SignalAccuracyTracking(Base):
    __tablename__ = "signal_accuracy_tracking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    decision_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    check_date: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    price_after_1d: Mapped[float | None] = mapped_column(Float)
    price_after_3d: Mapped[float | None] = mapped_column(Float)
    price_after_5d: Mapped[float | None] = mapped_column(Float)
    price_after_10d: Mapped[float | None] = mapped_column(Float)
    price_after_20d: Mapped[float | None] = mapped_column(Float)
    move_1d_pct: Mapped[float | None] = mapped_column(Float)
    move_3d_pct: Mapped[float | None] = mapped_column(Float)
    move_5d_pct: Mapped[float | None] = mapped_column(Float)
    move_10d_pct: Mapped[float | None] = mapped_column(Float)
    move_20d_pct: Mapped[float | None] = mapped_column(Float)
    technical_correct: Mapped[bool | None] = mapped_column(Boolean)
    financial_correct: Mapped[bool | None] = mapped_column(Boolean)
    news_correct: Mapped[bool | None] = mapped_column(Boolean)
    telegram_correct: Mapped[bool | None] = mapped_column(Boolean)
    strategy_correct: Mapped[bool | None] = mapped_column(Boolean)
    final_decision_correct: Mapped[bool | None] = mapped_column(Boolean)
    actual_best_driver: Mapped[str | None] = mapped_column(String(64))
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class DynamicWeightsBySymbol(Base):
    __tablename__ = "dynamic_weights_by_symbol"
    __table_args__ = (UniqueConstraint("symbol", name="uq_dynamic_weights_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    technical_weight: Mapped[float] = mapped_column(Float, default=35.0, nullable=False)
    financial_weight: Mapped[float] = mapped_column(Float, default=25.0, nullable=False)
    news_weight: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    telegram_weight: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    strategy_weight: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True, nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class RecommendationReport(Base):
    __tablename__ = "recommendation_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    report_time: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    sent_to_telegram: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="created", index=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["RecommendationItem"]] = relationship(back_populates="report", cascade="all, delete-orphan")


class RecommendationItem(Base):
    __tablename__ = "recommendation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("recommendation_reports.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255))
    final_score: Mapped[float | None] = mapped_column(Float, index=True)
    telegram_score: Mapped[float | None] = mapped_column(Float)
    technical_score: Mapped[float | None] = mapped_column(Float)
    strategy_score: Mapped[float | None] = mapped_column(Float)
    news_score: Mapped[float | None] = mapped_column(Float)
    backtest_score: Mapped[float | None] = mapped_column(Float)
    risk_liquidity_score: Mapped[float | None] = mapped_column(Float)
    signal: Mapped[str | None] = mapped_column(String(32), index=True)
    entry_zone_low: Mapped[float | None] = mapped_column(Float)
    entry_zone_high: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    target_1: Mapped[float | None] = mapped_column(Float)
    target_2: Mapped[float | None] = mapped_column(Float)
    target_3: Mapped[float | None] = mapped_column(Float)
    risk_reward: Mapped[float | None] = mapped_column(Float)
    explanation: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    report: Mapped[RecommendationReport] = relationship(back_populates="items")
    evaluation: Mapped["RecommendationEvaluation | None"] = relationship(
        back_populates="recommendation_item",
        cascade="all, delete-orphan",
        uselist=False,
    )


class RecommendationEvaluation(Base):
    __tablename__ = "recommendation_evaluations"
    __table_args__ = (UniqueConstraint("recommendation_item_id", name="uq_recommendation_evaluation_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_item_id: Mapped[int] = mapped_column(ForeignKey("recommendation_items.id"), index=True, nullable=False)
    report_id: Mapped[int] = mapped_column(ForeignKey("recommendation_reports.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    report_type: Mapped[str | None] = mapped_column(String(64), index=True)
    recommendation_datetime: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    recommendation_stage: Mapped[str | None] = mapped_column(String(64), index=True)
    strategy_source: Mapped[str | None] = mapped_column(String(128), index=True)
    telegram_source: Mapped[str | None] = mapped_column(String(255), index=True)
    market_regime: Mapped[str | None] = mapped_column(String(64), index=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    days_evaluated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    signal_price: Mapped[float | None] = mapped_column(Float)
    next_available_open: Mapped[float | None] = mapped_column(Float)
    latest_close: Mapped[float | None] = mapped_column(Float)
    highest_after_signal: Mapped[float | None] = mapped_column(Float)
    lowest_after_signal: Mapped[float | None] = mapped_column(Float)
    actual_return_pct: Mapped[float | None] = mapped_column(Float)
    max_favorable_move_pct: Mapped[float | None] = mapped_column(Float)
    max_adverse_move_pct: Mapped[float | None] = mapped_column(Float)
    target_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stop_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    final_status: Mapped[str] = mapped_column(String(64), default="NOT_EVALUATED", index=True, nullable=False)
    final_quality: Mapped[str | None] = mapped_column(String(64), index=True)
    evaluation_quality: Mapped[str | None] = mapped_column(String(64), index=True)
    evaluation_notes: Mapped[str | None] = mapped_column(Text)
    horizons_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True, nullable=False)

    recommendation_item: Mapped[RecommendationItem] = relationship(back_populates="evaluation")


class EndOfDayReviewReport(Base):
    __tablename__ = "end_of_day_review_reports"
    __table_args__ = (UniqueConstraint("review_date", name="uq_end_of_day_review_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="created", index=True, nullable=False)
    excel_path: Mapped[str | None] = mapped_column(String(1024))
    sent_to_telegram: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    suggestions_json: Mapped[list[dict[str, Any]] | dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["EndOfDayReviewItem"]] = relationship(back_populates="report", cascade="all, delete-orphan")


class EndOfDayReviewItem(Base):
    __tablename__ = "end_of_day_review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("end_of_day_review_reports.id"), index=True, nullable=False)
    review_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    row_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(255), index=True)
    recommendation_stage: Mapped[str | None] = mapped_column(String(64), index=True)
    classification: Mapped[str | None] = mapped_column(String(64), index=True)
    final_status: Mapped[str | None] = mapped_column(String(64), index=True)
    final_quality: Mapped[str | None] = mapped_column(String(64), index=True)
    actual_return_pct: Mapped[float | None] = mapped_column(Float)
    volume_change_pct: Mapped[float | None] = mapped_column(Float)
    value_traded: Mapped[float | None] = mapped_column(Float)
    technical_score: Mapped[float | None] = mapped_column(Float)
    financial_score: Mapped[float | None] = mapped_column(Float)
    news_score: Mapped[float | None] = mapped_column(Float)
    telegram_score: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    risk_reward_score: Mapped[float | None] = mapped_column(Float)
    market_regime_score: Mapped[float | None] = mapped_column(Float)
    final_score: Mapped[float | None] = mapped_column(Float)
    selection_threshold: Mapped[float | None] = mapped_column(Float)
    passed_filters_json: Mapped[list[str] | None] = mapped_column(JSON)
    failed_filters_json: Mapped[list[str] | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    suggested_fix: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)

    report: Mapped[EndOfDayReviewReport] = relationship(back_populates="items")


class DecisionSnapshot(Base):
    __tablename__ = "decision_snapshots"
    __table_args__ = (UniqueConstraint("recommendation_item_id", name="uq_decision_snapshot_recommendation_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_item_id: Mapped[int | None] = mapped_column(ForeignKey("recommendation_items.id"), index=True)
    recommendation_report_id: Mapped[int | None] = mapped_column(ForeignKey("recommendation_reports.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    stock_price: Mapped[float | None] = mapped_column(Float)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    value_traded: Mapped[float | None] = mapped_column(Float)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    spread_pct: Mapped[float | None] = mapped_column(Float)
    technical_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    telegram_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    news_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    financial_score: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    risk_reward_score: Mapped[float | None] = mapped_column(Float)
    market_condition_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    final_score: Mapped[float | None] = mapped_column(Float)
    decision: Mapped[str | None] = mapped_column(String(64), index=True)
    selected_rejected: Mapped[str | None] = mapped_column(String(32), index=True)
    reason_selected: Mapped[str | None] = mapped_column(Text)
    failed_filters_json: Mapped[list[str] | None] = mapped_column(JSON)
    strategy_version: Mapped[str | None] = mapped_column(String(128))
    weights_version: Mapped[str | None] = mapped_column(String(128))
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class WalkForwardRun(Base):
    __tablename__ = "walk_forward_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(128), default="combined_model", index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(64), default="created", index=True, nullable=False)
    periods_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    performance_decay_pct: Mapped[float | None] = mapped_column(Float)
    overfit_warning: Mapped[str | None] = mapped_column(Text)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)

    periods: Mapped[list["WalkForwardPeriod"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class WalkForwardPeriod(Base):
    __tablename__ = "walk_forward_periods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("walk_forward_runs.run_id"), index=True, nullable=False)
    period_index: Mapped[int] = mapped_column(Integer, nullable=False)
    train_start: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    train_end: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    test_start: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    test_end: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    in_sample_win_rate: Mapped[float | None] = mapped_column(Float)
    out_of_sample_win_rate: Mapped[float | None] = mapped_column(Float)
    forward_return: Mapped[float | None] = mapped_column(Float)
    train_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    test_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    performance_decay_pct: Mapped[float | None] = mapped_column(Float)
    overfit_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)

    run: Mapped[WalkForwardRun] = relationship(back_populates="periods")


class IntradayScanRun(Base):
    __tablename__ = "intraday_scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    scan_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    scan_time: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    market_regime: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), default="created", index=True, nullable=False)
    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alerts_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)

    items: Mapped[list["IntradayScanItem"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class IntradayScanItem(Base):
    __tablename__ = "intraday_scan_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("intraday_scan_runs.run_id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    price: Mapped[float | None] = mapped_column(Float)
    volume_change_pct: Mapped[float | None] = mapped_column(Float)
    move_pct: Mapped[float | None] = mapped_column(Float)
    entry_status: Mapped[str | None] = mapped_column(String(64), index=True)
    recommendation_status: Mapped[str | None] = mapped_column(String(64), index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)

    run: Mapped[IntradayScanRun] = relationship(back_populates="items")


class SourceAccuracySnapshot(Base):
    __tablename__ = "source_accuracy_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    signals_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evaluated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Float)
    average_return: Mapped[float | None] = mapped_column(Float)
    false_positive_rate: Mapped[float | None] = mapped_column(Float)
    target_hit_rate: Mapped[float | None] = mapped_column(Float)
    stop_hit_rate: Mapped[float | None] = mapped_column(Float)
    best_stocks_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    worst_stocks_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    reliability_score: Mapped[float | None] = mapped_column(Float)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class PumpRiskSnapshot(Base):
    __tablename__ = "pump_risk_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    pump_risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    repeated_messages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    low_confidence_sources: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pre_signal_move_pct: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    spread_pct: Mapped[float | None] = mapped_column(Float)
    technical_confirmation: Mapped[bool | None] = mapped_column(Boolean)
    financial_confirmation: Mapped[bool | None] = mapped_column(Boolean)
    downgrade_action: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class RiskExpectancySnapshot(Base):
    __tablename__ = "risk_expectancy_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), default="combined_model", index=True, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    evaluated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_win: Mapped[float | None] = mapped_column(Float)
    average_loss: Mapped[float | None] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_holding_days: Mapped[float | None] = mapped_column(Float)
    entry_reached_rate: Mapped[float | None] = mapped_column(Float)
    target_hit_rate: Mapped[float | None] = mapped_column(Float)
    stop_hit_rate: Mapped[float | None] = mapped_column(Float)
    risk_reward_accuracy: Mapped[float | None] = mapped_column(Float)
    best_strategy_by_expectancy: Mapped[str | None] = mapped_column(String(128))
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class RecommendationQualitySnapshot(Base):
    __tablename__ = "recommendation_quality_snapshots"
    __table_args__ = (UniqueConstraint("recommendation_item_id", name="uq_recommendation_quality_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_item_id: Mapped[int | None] = mapped_column(ForeignKey("recommendation_items.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    execution_realism_score: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    timing_score: Mapped[float | None] = mapped_column(Float)
    risk_reward_score: Mapped[float | None] = mapped_column(Float)
    source_confirmation_score: Mapped[float | None] = mapped_column(Float)
    pump_risk_score: Mapped[float | None] = mapped_column(Float)
    final_quality_score: Mapped[float | None] = mapped_column(Float)
    quality_grade: Mapped[str | None] = mapped_column(String(32), index=True)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class StrategyLearningReport(Base):
    __tablename__ = "strategy_learning_reports"
    __table_args__ = (UniqueConstraint("report_date", name="uq_strategy_learning_report_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    filters_helped_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    filters_blocked_good_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    filters_allowed_bad_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    accurate_sources_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    misleading_sources_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    suggested_weight_changes_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    suggested_rules_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    auto_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class PortfolioSetting(Base):
    __tablename__ = "portfolio_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    initial_cash: Mapped[float] = mapped_column(Float, default=100000.0, nullable=False)
    current_cash: Mapped[float] = mapped_column(Float, default=100000.0, nullable=False)
    max_risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    max_position_size_pct: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(64), default="paper_trading", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Cairo", nullable=False)
    require_manual_buy_confirmation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_manual_sell_confirmation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_daily_trades: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    max_weekly_loss_pct: Mapped[float] = mapped_column(Float, default=8.0, nullable=False)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=15.0, nullable=False)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    allow_high_risk_trades: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    minimum_final_score_to_buy: Mapped[float] = mapped_column(Float, default=70.0, nullable=False)
    minimum_score_to_hold: Mapped[float] = mapped_column(Float, default=45.0, nullable=False)
    trailing_stop_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    portfolio_bot_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    buy_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    buy_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cost: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit_1: Mapped[float | None] = mapped_column(Float)
    take_profit_2: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True, nullable=False)
    current_price: Mapped[float | None] = mapped_column(Float)
    unrealized_profit: Mapped[float | None] = mapped_column(Float)
    unrealized_profit_pct: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PortfolioTrade(Base):
    __tablename__ = "portfolio_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    trade_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    trade_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_value: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    final_score: Mapped[float | None] = mapped_column(Float)
    technical_score: Mapped[float | None] = mapped_column(Float)
    financial_score: Mapped[float | None] = mapped_column(Float)
    news_score: Mapped[float | None] = mapped_column(Float)
    telegram_score: Mapped[float | None] = mapped_column(Float)
    strategy_score: Mapped[float | None] = mapped_column(Float)
    profit_loss: Mapped[float | None] = mapped_column(Float)
    profit_loss_pct: Mapped[float | None] = mapped_column(Float)
    cairo_timestamp: Mapped[str] = mapped_column(String(64), index=True, nullable=False)


class MarketRegimeSnapshot(Base):
    __tablename__ = "market_regime_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    index_symbol: Mapped[str] = mapped_column(String(32), default="EGX30", index=True, nullable=False)
    regime: Mapped[str] = mapped_column(String(64), default="unknown", index=True, nullable=False)
    trend_score: Mapped[float | None] = mapped_column(Float)
    volatility_score: Mapped[float | None] = mapped_column(Float)
    market_score: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class SectorAnalysisSnapshot(Base):
    __tablename__ = "sector_analysis_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sector: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    sector_score: Mapped[float | None] = mapped_column(Float)
    benchmark_score: Mapped[float | None] = mapped_column(Float)
    regime: Mapped[str | None] = mapped_column(String(64))
    top_symbols: Mapped[list[str] | None] = mapped_column(JSON)
    weak_symbols: Mapped[list[str] | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class LiquiditySnapshot(Base):
    __tablename__ = "liquidity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    avg_volume: Mapped[float | None] = mapped_column(Float)
    avg_value_traded: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(64), default="unknown", index=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class TradeApproval(Base):
    __tablename__ = "trade_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(32), default="BUY", index=True, nullable=False)
    proposed_price: Mapped[float | None] = mapped_column(Float)
    quantity: Mapped[int | None] = mapped_column(Integer)
    total_value: Mapped[float | None] = mapped_column(Float)
    final_score: Mapped[float | None] = mapped_column(Float)
    signal: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class TradeJournal(Base):
    __tablename__ = "trade_journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    signal: Mapped[str | None] = mapped_column(String(64), index=True)
    entry_zone: Mapped[str | None] = mapped_column(String(255))
    actual_entry: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    targets: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON)
    exit_price: Mapped[float | None] = mapped_column(Float)
    result: Mapped[str | None] = mapped_column(String(64), index=True)
    pnl: Mapped[float | None] = mapped_column(Float)
    pnl_pct: Mapped[float | None] = mapped_column(Float)
    reason_for_entry: Mapped[str | None] = mapped_column(Text)
    reason_for_exit: Mapped[str | None] = mapped_column(Text)
    mistake_type: Mapped[str | None] = mapped_column(String(64), index=True)
    lesson_learned: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class DailyLossAuditReport(Base):
    __tablename__ = "daily_loss_audit_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    audit_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    report_type: Mapped[str] = mapped_column(String(64), default="daily_loss_audit", index=True, nullable=False)
    total_recommendations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    good_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bad_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    no_entry: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stop_loss_hit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    target_hit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_pnl: Mapped[float | None] = mapped_column(Float)
    biggest_problem: Mapped[str | None] = mapped_column(Text)
    final_diagnosis: Mapped[str | None] = mapped_column(Text)
    action_plan: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="created", index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    items: Mapped[list["DailyLossAuditItem"]] = relationship(back_populates="report", cascade="all, delete-orphan")


class DailyLossAuditItem(Base):
    __tablename__ = "daily_loss_audit_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("daily_loss_audit_reports.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    recommendation: Mapped[str | None] = mapped_column(String(64), index=True)
    final_score: Mapped[float | None] = mapped_column(Float)
    entry_zone: Mapped[str | None] = mapped_column(String(255))
    actual_entry_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    targets_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    max_drawdown_after_entry: Mapped[float | None] = mapped_column(Float)
    max_profit_after_entry: Mapped[float | None] = mapped_column(Float)
    actual_return: Mapped[float | None] = mapped_column(Float)
    estimated_pnl: Mapped[float | None] = mapped_column(Float)
    evaluation_quality: Mapped[str | None] = mapped_column(String(64), index=True)
    market_score_at_signal: Mapped[float | None] = mapped_column(Float)
    market_regime_at_signal: Mapped[str | None] = mapped_column(String(64), index=True)
    trade_permission_at_signal: Mapped[str | None] = mapped_column(String(64), index=True)
    should_trade_yes_no: Mapped[str | None] = mapped_column(String(16), index=True)
    time_to_target_minutes: Mapped[float | None] = mapped_column(Float)
    time_to_stop_minutes: Mapped[float | None] = mapped_column(Float)
    result: Mapped[str | None] = mapped_column(String(64), index=True)
    mistake_type: Mapped[str | None] = mapped_column(String(64), index=True)
    root_cause: Mapped[str | None] = mapped_column(Text)
    fix_required: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(String(32), index=True)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)

    report: Mapped[DailyLossAuditReport] = relationship(back_populates="items")


class MarketDailyEvaluation(Base):
    __tablename__ = "market_daily_evaluations"
    __table_args__ = (UniqueConstraint("evaluation_date", name="uq_market_daily_evaluation_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    market_status: Mapped[str] = mapped_column(String(64), default="unknown", index=True, nullable=False)
    market_score: Mapped[float | None] = mapped_column(Float, index=True)
    market_regime: Mapped[str] = mapped_column(String(64), default="DATA_INSUFFICIENT", index=True, nullable=False)
    trade_permission: Mapped[str] = mapped_column(String(64), default="DATA_INSUFFICIENT", index=True, nullable=False)
    advancing_stocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    declining_stocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unchanged_stocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    volume_score: Mapped[float | None] = mapped_column(Float)
    volatility_score: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    news_score: Mapped[float | None] = mapped_column(Float)
    telegram_score: Mapped[float | None] = mapped_column(Float)
    sector_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    warnings_json: Mapped[list[str] | None] = mapped_column(JSON)
    explanation: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class LiveTradeExecutionLog(Base):
    __tablename__ = "live_trade_execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    cairo_timestamp: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="paper", index=True, nullable=False)
    quantity: Mapped[int | None] = mapped_column(Integer)
    price: Mapped[float | None] = mapped_column(Float)
    order_value: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    recommendation_id: Mapped[int | None] = mapped_column(Integer, index=True)
    market_score: Mapped[float | None] = mapped_column(Float)
    market_regime: Mapped[str | None] = mapped_column(String(64), index=True)
    execution_status: Mapped[str] = mapped_column(String(64), default="blocked", index=True, nullable=False)
    broker_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class RepeatedRecommendationAudit(Base):
    __tablename__ = "repeated_recommendation_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    recommendation_type: Mapped[str | None] = mapped_column(String(64), index=True)
    repeats_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repeated_at_json: Mapped[list[str] | None] = mapped_column(JSON)
    source_of_repeat: Mapped[str | None] = mapped_column(String(128), index=True)
    telegram_caused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    strategy_caused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    report_generation_caused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deduplication_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    root_cause: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class DailyFileReport(Base):
    __tablename__ = "daily_file_reports"
    __table_args__ = (UniqueConstraint("report_date", name="uq_daily_file_report_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_date: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    report_time: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    excel_path: Mapped[str | None] = mapped_column(String(1024))
    pdf_path: Mapped[str | None] = mapped_column(String(1024))
    excel_created: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pdf_created: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sent_to_telegram: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="created", index=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class NoTradeReason(Base):
    __tablename__ = "no_trade_reasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    decision_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    final_score: Mapped[float | None] = mapped_column(Float)
    final_signal: Mapped[str | None] = mapped_column(String(32))
    reasons_json: Mapped[list[str] | None] = mapped_column(JSON)
    reason_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class MistakeReview(Base):
    __tablename__ = "mistake_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    loss_amount: Mapped[float | None] = mapped_column(Float)
    loss_pct: Mapped[float | None] = mapped_column(Float)
    suspected_reason: Mapped[str | None] = mapped_column(Text)
    improvement: Mapped[str | None] = mapped_column(Text)
    related_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class ConfidenceCalibration(Base):
    __tablename__ = "confidence_calibration"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    analysis_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expected_confidence: Mapped[float | None] = mapped_column(Float)
    observed_win_rate: Mapped[float | None] = mapped_column(Float)
    calibration_error: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)


class NotificationLog(Base):
    __tablename__ = "notification_log"
    __table_args__ = (
        UniqueConstraint("notification_hash", name="uq_notification_log_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notification_hash: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    recommendation: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    entry_zone: Mapped[str | None] = mapped_column(String(255))
    target: Mapped[str | None] = mapped_column(String(255))
    stop_loss: Mapped[str | None] = mapped_column(String(255))
    source_module: Mapped[str] = mapped_column(String(100))
    cooldown_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class AiStockOpinion(Base):
    __tablename__ = "ai_stock_opinions"
    __table_args__ = (UniqueConstraint("symbol", "run_id", name="uq_ai_opinion_symbol_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ai_score: Mapped[float | None] = mapped_column(Float)
    ai_signal: Mapped[str | None] = mapped_column(String(32))
    ai_opinion: Mapped[str | None] = mapped_column(Text)
    ai_reasoning: Mapped[str | None] = mapped_column(Text)
    ai_key_drivers: Mapped[list[str] | None] = mapped_column(JSON)
    ai_risks: Mapped[list[str] | None] = mapped_column(JSON)
    ai_catalyst: Mapped[str | None] = mapped_column(String(255))
    ai_entry_zone: Mapped[str | None] = mapped_column(String(255))
    ai_stop_loss: Mapped[float | None] = mapped_column(Float)
    ai_target_1: Mapped[float | None] = mapped_column(Float)
    ai_target_2: Mapped[float | None] = mapped_column(Float)
    ai_time_horizon: Mapped[str | None] = mapped_column(String(64))
    ai_confidence: Mapped[str | None] = mapped_column(String(32))
    model_used: Mapped[str | None] = mapped_column(String(64))
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
