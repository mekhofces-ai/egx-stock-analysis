from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DISCLAIMER = "Not financial advice."
RESEARCH_DISCLAIMER = "This is for research and education only, not financial advice."
RISK_NOTE = "System-generated analysis, not financial advice."
REPORT_TIMEZONE = "Africa/Cairo"
DAILY_REPORT_TIMES = ["09:00", "21:00"]
DAILY_FILE_REPORT_TIME = "15:00"
RECOMMENDATION_TOP_N = 5
RECOMMENDATION_WEIGHTS = {
    "telegram_score": 0.20,
    "technical_score": 0.25,
    "strategy_score": 0.20,
    "news_score": 0.15,
    "backtest_score": 0.15,
    "risk_liquidity_score": 0.05,
}
LIVE_TRADING_ENABLED = False
AUDIT_MODE = True
EMERGENCY_STOP_TRADING = True
MAX_DAILY_LOSS_PCT = 0.03
DEFAULT_COMMISSION_RATE = 0.0015
DEFAULT_SLIPPAGE_RATE = 0.002
MIN_AVG_DAILY_VALUE_TRADED = 500000
MAX_POSITION_RISK_PCT = 0.01
MAX_ALLOWED_SPREAD_PCT = 0.015
MAX_DISTANCE_FROM_ENTRY_PCT = 0.025
MIN_TECHNICAL_SCORE_TO_BUY = 70
MIN_STRATEGY_SCORE_TO_BUY = 65
MIN_BACKTEST_SCORE_TO_BUY = 60
MIN_RISK_LIQUIDITY_SCORE_TO_BUY = 60
MIN_RISK_REWARD_TO_BUY = 1.8
PAPER_TRADING_REQUIRED_DAYS = 14
PAPER_TRADING_MIN_WIN_RATE = 0.55
PAPER_TRADING_MAX_DRAWDOWN = 0.05


def csv_list(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def csv_int_list(value: str | Iterable[int | str] | None) -> list[int]:
    ids: list[int] = []
    for item in csv_list(value):
        try:
            ids.append(int(item))
        except ValueError:
            continue
    return ids


def normalize_database_url(value: str) -> str:
    """Accept SQLAlchemy URLs and the Prisma-style file: URL used by the existing app."""
    if value.startswith("file:"):
        sqlite_path = value.replace("file:", "", 1)
        return f"sqlite:///{sqlite_path}"
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "EGX Telegram Signal Analyst"
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    egx_database_url: str = Field(default="sqlite:///./egx_signals.db", alias="EGX_DATABASE_URL")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_cors_origins: str = Field(default="*", alias="API_CORS_ORIGINS")

    telegram_api_id: int | None = Field(default=None, validation_alias=AliasChoices("TELEGRAM_API_ID", "API_ID"))
    telegram_api_hash: str | None = Field(default=None, validation_alias=AliasChoices("TELEGRAM_API_HASH", "API_HASH"))
    telegram_session_name: str = Field(
        default="egx_telegram",
        validation_alias=AliasChoices("TELEGRAM_SESSION_NAME", "TELEGRAM_SESSION", "SESSION_NAME"),
    )
    telegram_source_channels: str = Field(default="", alias="TELEGRAM_SOURCE_CHANNELS")
    telegram_fetch_limit_per_channel: int = Field(
        default=100,
        validation_alias=AliasChoices("TELEGRAM_FETCH_LIMIT_PER_CHANNEL", "TELEGRAM_FETCH_LIMIT"),
    )

    telegram_bot_token: str | None = Field(default=None, validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "BOT_TOKEN"))
    telegram_bot_private_chat_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_BOT_PRIVATE_CHAT_ID", "TELEGRAM_PRIVATE_CHAT_ID", "PRIVATE_CHAT_ID"),
    )
    telegram_bot_embedded_enabled: bool = Field(default=False, alias="TELEGRAM_BOT_EMBEDDED_ENABLED")
    telegram_bot_allowed_chat_ids: str = Field(default="", alias="TELEGRAM_BOT_ALLOWED_CHAT_IDS")
    telegram_bot_verify_tls: bool = Field(default=True, alias="TELEGRAM_BOT_VERIFY_TLS")
    telegram_alert_enabled: bool = Field(default=True, alias="TELEGRAM_ALERT_ENABLED")
    telegram_alert_decisions: str = Field(default="BUY", alias="TELEGRAM_ALERT_DECISIONS")
    telegram_alert_min_confidence: float = Field(default=70.0, alias="TELEGRAM_ALERT_MIN_CONFIDENCE")
    telegram_alert_recommendations_enabled: bool = Field(default=True, alias="TELEGRAM_ALERT_RECOMMENDATIONS_ENABLED")
    telegram_alert_require_telegram_confirmation: bool = Field(default=True, alias="TELEGRAM_ALERT_REQUIRE_TELEGRAM_CONFIRMATION")
    telegram_alert_scan_interval_minutes: int = Field(default=5, alias="TELEGRAM_ALERT_SCAN_INTERVAL_MINUTES")

    market_data_provider_priority: str = Field(
        default="tradingview_screener,tradingview_websocket,csv",
        validation_alias=AliasChoices("MARKET_DATA_PROVIDER_PRIORITY", "MARKET_DATA_PROVIDER"),
    )
    market_data_allow_mock: bool = Field(default=False, validation_alias=AliasChoices("MARKET_DATA_ALLOW_MOCK", "ENABLE_MOCK_PROVIDER"))
    allow_insecure_market_data_tls: bool = Field(default=False, alias="ALLOW_INSECURE_MARKET_DATA_TLS")
    tradingview_auth_token: str = Field(default="unauthorized_user_token", alias="TRADINGVIEW_AUTH_TOKEN")
    tradingview_ws_url: str = Field(default="wss://data.tradingview.com/socket.io/websocket", alias="TRADINGVIEW_WS_URL")
    csv_data_dir: str = Field(default="data/ohlcv", alias="CSV_DATA_DIR")
    csv_ohlcv_sample_path: str = Field(default="data/ohlcv_sample.csv", alias="CSV_OHLCV_SAMPLE_PATH")
    market_depth_data_dir: str = Field(default="data/market_depth", alias="MARKET_DEPTH_DATA_DIR")
    image_download_dir: str = Field(default="data/images", alias="IMAGE_DOWNLOAD_DIR")

    scheduler_enabled: bool = Field(default=True, alias="SCHEDULER_ENABLED")
    telegram_fetch_interval_minutes: int = Field(default=5, alias="TELEGRAM_FETCH_INTERVAL_MINUTES")
    analysis_interval_minutes: int = Field(default=5, alias="ANALYSIS_INTERVAL_MINUTES")
    performance_interval_minutes: int = Field(default=5, alias="PERFORMANCE_INTERVAL_MINUTES")
    backtest_interval_minutes: int = Field(default=60, alias="BACKTEST_INTERVAL_MINUTES")
    daily_report_hour: int = Field(default=17, alias="DAILY_REPORT_HOUR")
    daily_report_top_n: int = Field(default=10, alias="DAILY_REPORT_TOP_N")
    daily_report_include_strategy: bool = Field(default=True, alias="DAILY_REPORT_INCLUDE_STRATEGY")
    night_opportunity_report_enabled: bool = Field(default=True, alias="NIGHT_OPPORTUNITY_REPORT_ENABLED")
    night_opportunity_report_hour: int = Field(default=21, alias="NIGHT_OPPORTUNITY_REPORT_HOUR")
    night_opportunity_top_n: int = Field(default=7, alias="NIGHT_OPPORTUNITY_TOP_N")
    strategy_timeframes: str = Field(default="15m,1h,4h,1D", alias="STRATEGY_TIMEFRAMES")
    strategy_symbol_limit: int = Field(default=30, alias="STRATEGY_SYMBOL_LIMIT")
    strategy_backtest_bars: int = Field(default=260, alias="STRATEGY_BACKTEST_BARS")
    strategy_allow_mock_data: bool = Field(default=False, alias="STRATEGY_ALLOW_MOCK_DATA")
    strategy_max_daily_age_days: int = Field(default=14, alias="STRATEGY_MAX_DAILY_AGE_DAYS")
    strategy_max_intraday_age_days: int = Field(default=7, alias="STRATEGY_MAX_INTRADAY_AGE_DAYS")
    strategy_price_tolerance_percent: float = Field(default=15.0, alias="STRATEGY_PRICE_TOLERANCE_PERCENT")
    timezone: str = Field(default="Africa/Cairo", alias="TIMEZONE")

    default_risk_per_trade_percent: float = Field(default=1.0, alias="DEFAULT_RISK_PER_TRADE_PERCENT")
    opportunity_weight_recommendation: float = Field(default=30.0, alias="OPPORTUNITY_WEIGHT_RECOMMENDATION")
    opportunity_weight_strategy: float = Field(default=20.0, alias="OPPORTUNITY_WEIGHT_STRATEGY")
    opportunity_weight_backtest: float = Field(default=20.0, alias="OPPORTUNITY_WEIGHT_BACKTEST")
    opportunity_weight_tradingview: float = Field(default=20.0, alias="OPPORTUNITY_WEIGHT_TRADINGVIEW")
    opportunity_weight_telegram: float = Field(default=10.0, alias="OPPORTUNITY_WEIGHT_TELEGRAM")
    opportunity_weight_system_recommendation: float = Field(default=25.0, alias="OPPORTUNITY_WEIGHT_SYSTEM_RECOMMENDATION")
    opportunity_weight_cli_v6_strategy: float = Field(default=25.0, alias="OPPORTUNITY_WEIGHT_CLI_V6_STRATEGY")
    opportunity_weight_cli_v6_backtest: float = Field(default=20.0, alias="OPPORTUNITY_WEIGHT_CLI_V6_BACKTEST")
    opportunity_weight_cli_v6_tradingview: float = Field(default=20.0, alias="OPPORTUNITY_WEIGHT_CLI_V6_TRADINGVIEW")
    opportunity_weight_cli_v6_telegram: float = Field(default=10.0, alias="OPPORTUNITY_WEIGHT_CLI_V6_TELEGRAM")

    automation_enabled: bool = Field(default=True, alias="AUTOMATION_ENABLED")
    automation_interval_seconds: int = Field(default=120, alias="AUTOMATION_INTERVAL_SECONDS")
    automation_symbol_limit: int = Field(default=0, alias="AUTOMATION_SYMBOL_LIMIT")
    daily_file_report_time: str = Field(default=DAILY_FILE_REPORT_TIME, alias="DAILY_FILE_REPORT_TIME")
    daily_file_report_enabled: bool = Field(default=True, alias="DAILY_FILE_REPORT_ENABLED")
    live_trading_enabled: bool = Field(default=LIVE_TRADING_ENABLED, alias="LIVE_TRADING_ENABLED")

    # Stock alert control center settings
    enable_stock_alerts: bool = Field(default=True, alias="ENABLE_STOCK_ALERTS")
    muted_symbols: str = Field(default="", alias="MUTED_SYMBOLS")
    notification_cooldown_minutes: int = Field(default=120, alias="NOTIFICATION_COOLDOWN_MINUTES")
    enable_force_send: bool = Field(default=False, alias="ENABLE_FORCE_SEND")

    # AI Analysis settings
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    ai_base_url: str = Field(default="https://api.openai.com/v1", alias="AI_BASE_URL")
    enable_ai_analysis: bool = Field(default=False, alias="ENABLE_AI_ANALYSIS")
    ai_model: str = Field(default="deepseek-chat", alias="AI_MODEL")
    ai_max_stocks_per_run: int = Field(default=10, alias="AI_MAX_STOCKS_PER_RUN")
    ai_min_score_to_analyze: float = Field(default=60.0, alias="AI_MIN_SCORE_TO_ANALYZE")
    ai_weight_in_final_score: float = Field(default=10.0, alias="AI_WEIGHT_IN_FINAL_SCORE")
    ai_daily_call_limit: int = Field(default=50, alias="AI_DAILY_CALL_LIMIT")
    ai_timeout_seconds: int = Field(default=30, alias="AI_TIMEOUT_SECONDS")
    audit_mode: bool = Field(default=AUDIT_MODE, alias="AUDIT_MODE")
    emergency_stop_trading: bool = Field(default=EMERGENCY_STOP_TRADING, alias="EMERGENCY_STOP_TRADING")
    max_daily_loss_pct: float = Field(default=MAX_DAILY_LOSS_PCT, alias="MAX_DAILY_LOSS_PCT")
    min_avg_daily_value_traded: float = Field(default=MIN_AVG_DAILY_VALUE_TRADED, alias="MIN_AVG_DAILY_VALUE_TRADED")
    max_position_risk_pct: float = Field(default=MAX_POSITION_RISK_PCT, alias="MAX_POSITION_RISK_PCT")
    max_allowed_spread_pct: float = Field(default=MAX_ALLOWED_SPREAD_PCT, alias="MAX_ALLOWED_SPREAD_PCT")
    max_distance_from_entry_pct: float = Field(default=MAX_DISTANCE_FROM_ENTRY_PCT, alias="MAX_DISTANCE_FROM_ENTRY_PCT")

    @field_validator("telegram_api_id", "telegram_bot_private_chat_id", mode="before")
    @classmethod
    def blank_int_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("telegram_api_hash", "telegram_bot_token", mode="before")
    @classmethod
    def blank_secret_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @property
    def database_url(self) -> str:
        return normalize_database_url(self.egx_database_url)

    @property
    def provider_priority(self) -> list[str]:
        return csv_list(self.market_data_provider_priority)

    @property
    def source_channels(self) -> list[str]:
        return csv_list(self.telegram_source_channels)

    @property
    def allowed_chat_ids(self) -> list[int]:
        ids = csv_int_list(self.telegram_bot_allowed_chat_ids)
        if self.telegram_bot_private_chat_id is not None and self.telegram_bot_private_chat_id not in ids:
            ids.append(self.telegram_bot_private_chat_id)
        return ids

    @property
    def alert_decision_set(self) -> set[str]:
        return {item.upper() for item in csv_list(self.telegram_alert_decisions)}

    @property
    def strategy_timeframe_list(self) -> list[str]:
        return csv_list(self.strategy_timeframes) or ["15m", "1h", "4h", "1D"]

    @property
    def cli_v6_opportunity_weights(self) -> dict[str, float]:
        return {
            "system_recommendation": self.opportunity_weight_system_recommendation,
            "cli_v6_strategy": self.opportunity_weight_cli_v6_strategy,
            "backtest": self.opportunity_weight_cli_v6_backtest,
            "tradingview": self.opportunity_weight_cli_v6_tradingview,
            "telegram": self.opportunity_weight_cli_v6_telegram,
        }

    @property
    def safe_automation_interval_seconds(self) -> int:
        return max(60, int(self.automation_interval_seconds or 120))

    @property
    def cors_origins(self) -> list[str]:
        origins = csv_list(self.api_cors_origins)
        return origins or ["*"]

    def ensure_runtime_dirs(self) -> None:
        Path(self.csv_data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.market_depth_data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.image_download_dir).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
