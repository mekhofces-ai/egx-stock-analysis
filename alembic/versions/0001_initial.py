"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255)),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("trust_score", sa.Float(), nullable=False),
        sa.Column("last_message_id", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_telegram_sources_username"), "telegram_sources", ["username"], unique=True)

    op.create_table(
        "stocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("name_ar", sa.String(length=255)),
        sa.Column("name_en", sa.String(length=255)),
        sa.Column("sector", sa.String(length=255)),
        sa.Column("tradingview_symbol", sa.String(length=64)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_stocks_symbol"), "stocks", ["symbol"], unique=True)

    op.create_table(
        "market_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("timeframe", sa.String(length=32), nullable=False),
        sa.Column("open", sa.Float()),
        sa.Column("high", sa.Float()),
        sa.Column("low", sa.Float()),
        sa.Column("close", sa.Float()),
        sa.Column("volume", sa.Float()),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("raw", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_market_prices_symbol"), "market_prices", ["symbol"])
    op.create_index(op.f("ix_market_prices_timestamp"), "market_prices", ["timestamp"])

    op.create_table(
        "telegram_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("telegram_sources.id"), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("message_date", sa.DateTime()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("raw_json", sa.JSON()),
        sa.Column("image_path", sa.String(length=1024)),
        sa.Column("image_metadata", sa.JSON()),
        sa.Column("parsed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_id", "message_id", name="uq_telegram_message_source_message"),
    )
    op.create_index(op.f("ix_telegram_messages_source_id"), "telegram_messages", ["source_id"])

    op.create_table(
        "extracted_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("telegram_sources.id")),
        sa.Column("telegram_message_id", sa.Integer(), sa.ForeignKey("telegram_messages.id")),
        sa.Column("stock_symbol", sa.String(length=32)),
        sa.Column("stock_name", sa.String(length=255)),
        sa.Column("direction", sa.String(length=32)),
        sa.Column("entry_price", sa.Float()),
        sa.Column("targets", sa.JSON()),
        sa.Column("stop_loss", sa.Float()),
        sa.Column("support", sa.Float()),
        sa.Column("resistance", sa.Float()),
        sa.Column("timeframe", sa.String(length=64)),
        sa.Column("hype_words", sa.JSON()),
        sa.Column("risk_flags", sa.JSON()),
        sa.Column("sentiment_score", sa.Float(), nullable=False),
        sa.Column("raw", sa.JSON()),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_extracted_signals_source_id"), "extracted_signals", ["source_id"])
    op.create_index(op.f("ix_extracted_signals_telegram_message_id"), "extracted_signals", ["telegram_message_id"])
    op.create_index(op.f("ix_extracted_signals_stock_symbol"), "extracted_signals", ["stock_symbol"])

    op.create_table(
        "technical_analysis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.Column("timeframe", sa.String(length=32), nullable=False),
        sa.Column("indicators", sa.JSON()),
        sa.Column("trend_direction", sa.String(length=64)),
        sa.Column("volatility_score", sa.Float()),
        sa.Column("liquidity_score", sa.Float()),
        sa.Column("technical_score", sa.Float()),
        sa.Column("risk_score", sa.Float()),
        sa.Column("support", sa.Float()),
        sa.Column("resistance", sa.Float()),
        sa.Column("breakout", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_technical_analysis_symbol"), "technical_analysis", ["symbol"])

    op.create_table(
        "final_analysis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("extracted_signal_id", sa.Integer(), sa.ForeignKey("extracted_signals.id")),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("telegram_sources.id")),
        sa.Column("technical_analysis_id", sa.Integer(), sa.ForeignKey("technical_analysis.id")),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("final_decision", sa.String(length=32), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("entry_zone", sa.String(length=255)),
        sa.Column("stop_loss", sa.Float()),
        sa.Column("targets", sa.JSON()),
        sa.Column("reasons", sa.JSON()),
        sa.Column("warnings", sa.JSON()),
        sa.Column("invalidation_point", sa.String(length=255)),
        sa.Column("position_size_suggestion", sa.String(length=255)),
        sa.Column("last_price", sa.Float()),
        sa.Column("trend", sa.String(length=64)),
        sa.Column("disclaimer", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_final_analysis_symbol"), "final_analysis", ["symbol"])
    op.create_index(op.f("ix_final_analysis_source_id"), "final_analysis", ["source_id"])
    op.create_index(op.f("ix_final_analysis_extracted_signal_id"), "final_analysis", ["extracted_signal_id"])
    op.create_index(op.f("ix_final_analysis_technical_analysis_id"), "final_analysis", ["technical_analysis_id"])

    op.create_table(
        "channel_performance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("telegram_sources.id"), nullable=False),
        sa.Column("total_signals", sa.Integer(), nullable=False),
        sa.Column("win_rate", sa.Float()),
        sa.Column("fake_signal_count", sa.Integer(), nullable=False),
        sa.Column("avg_confidence", sa.Float()),
        sa.Column("best_symbols", sa.JSON()),
        sa.Column("worst_symbols", sa.JSON()),
        sa.Column("stop_loss_missing_count", sa.Integer(), nullable=False),
        sa.Column("pump_words_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_channel_performance_source_id"), "channel_performance", ["source_id"], unique=True)

    op.create_table(
        "bot_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger()),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_bot_users_chat_id"), "bot_users", ["chat_id"], unique=True)
    op.create_index(op.f("ix_bot_users_telegram_user_id"), "bot_users", ["telegram_user_id"])

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(op.f("ix_app_settings_key"), "app_settings", ["key"], unique=True)

    op.create_table(
        "jobs_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("details", sa.Text()),
    )
    op.create_index(op.f("ix_jobs_log_job_name"), "jobs_log", ["job_name"])


def downgrade() -> None:
    op.drop_table("jobs_log")
    op.drop_table("app_settings")
    op.drop_table("bot_users")
    op.drop_table("channel_performance")
    op.drop_table("final_analysis")
    op.drop_table("technical_analysis")
    op.drop_table("extracted_signals")
    op.drop_table("telegram_messages")
    op.drop_table("market_prices")
    op.drop_table("stocks")
    op.drop_table("telegram_sources")
