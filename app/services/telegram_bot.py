from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from app.config import DISCLAIMER, RESEARCH_DISCLAIMER, RISK_NOTE, Settings, get_settings
from app.database import SessionLocal
from app.intelligence.final_decision_engine import build_final_decision
from app.intelligence.portfolio_bot import get_portfolio_settings, portfolio_value, run_daily_portfolio_bot, scan_portfolio
from app.intelligence.risk_guard import risk_guard_status
from app.models import (
    BotUser,
    FinalAnalysis,
    FinancialData,
    FinancialSignal,
    NewsSignal,
    PortfolioPosition,
    PortfolioSetting,
    PortfolioTrade,
    StockNews,
    TelegramMediaAnalysis,
    TelegramSource,
    TelegramSubscriber,
)
from app.services.bot_analysis_adapter import (
    get_backtest_summary,
    automation_start_report,
    automation_stop_report,
    get_combined_opportunity_score,
    get_automation_status_report,
    get_latest_recommendations,
    get_market_summary,
    get_status_report,
    get_stock_analysis,
    get_strategy_summary,
    get_top_opportunities,
    get_tradingview_screening_summary,
    get_watchlist,
    send_buy_alerts,
)
from app.services.analysis_runner import analyze_symbol_manually, format_alert
from app.services.ingestion import run_ingestion_cycle_async
from app.services.market_depth import build_market_depth_report
from app.services.reports import build_daily_report, build_final_decision_report, build_night_opportunity_report, build_stock_brief
from app.services.env_health import format_env_health
from app.services.stock_analysis_engine import format_combined_analysis_report
from app.services.subscribers import (
    active_alert_chat_ids,
    can_use_bot as subscriber_can_use_bot,
    format_profile,
    format_subscribers,
    is_admin as subscriber_is_admin,
    list_subscribers,
    register_from_update,
    set_subscription_flags,
)
from app.services.backtest_queue import enqueue_backtest
from app.services.dynamic_settings import get_bool, get_int, seed_dynamic_settings, set_setting
from app.services.trading_safety import execution_block_reason, safety_snapshot

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

try:
    from telegram import Bot, Update
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
    from telegram.request import HTTPXRequest
except Exception:  # pragma: no cover - optional until installed
    Bot = None
    Update = Any
    Application = None
    CommandHandler = None
    ContextTypes = Any
    MessageHandler = None
    filters = None
    HTTPXRequest = None


def _normalize_channel(username: str) -> str:
    username = username.strip()
    return username if username.startswith("@") else f"@{username}"


def _telegram_request(settings: Settings):
    if HTTPXRequest is None:
        return None
    httpx_kwargs = {"verify": False} if not settings.telegram_bot_verify_tls else None
    if httpx_kwargs:
        logger.warning("Telegram bot TLS verification is disabled by TELEGRAM_BOT_VERIFY_TLS=false.")
    return HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=35.0,
        write_timeout=25.0,
        pool_timeout=10.0,
        httpx_kwargs=httpx_kwargs,
    )


def _admin_chat_ids(settings: Settings) -> list[int]:
    with SessionLocal() as db:
        ids = []
        rows = db.scalars(
            select(TelegramSubscriber).where(
                TelegramSubscriber.role == "admin",
                TelegramSubscriber.is_active.is_(True),
            )
        ).all()
        for row in rows:
            try:
                ids.append(int(row.chat_id))
            except Exception:
                continue
    for chat_id in settings.allowed_chat_ids:
        if chat_id not in ids:
            ids.append(chat_id)
    return ids


def _is_admin_chat(chat_id: int, settings: Settings) -> bool:
    with SessionLocal() as db:
        return subscriber_is_admin(db, chat_id, settings=settings)


def _user_label(update: Update) -> str:
    user = update.effective_user
    chat = update.effective_chat
    username = getattr(user, "username", None) or getattr(chat, "username", None)
    first_name = getattr(user, "first_name", None) or getattr(chat, "first_name", None)
    last_name = getattr(user, "last_name", None)
    parts = [part for part in [first_name, last_name] if part]
    if username:
        parts.append(f"@{username}")
    return " ".join(parts) or "Unknown user"


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, settings: Settings, text: str) -> None:
    for chat_id in _admin_chat_ids(settings):
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            logger.exception("Could not notify admin chat %s", chat_id)


def _approved_alert_chat_ids(settings: Settings) -> list[int]:
    with SessionLocal() as db:
        return active_alert_chat_ids(db, settings=settings)


def _alert_result_text(label: str, result: dict | None) -> str:
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


def _with_risk_note(text: str) -> str:
    if RISK_NOTE in text:
        return text
    return f"{text.rstrip()}\n\nRisk Note: {RISK_NOTE}"


def _ingestion_result_text(result) -> str:
    return (
        f"New messages: {result.inserted_messages}, new analyses: {result.new_analyses}.\n"
        f"{_alert_result_text('Signal alerts', result.signal_alerts)}.\n"
        f"{_alert_result_text('Recommendation alerts', result.recommendation_alerts)}."
    )


def _format_intelligence_decision(result: dict[str, Any]) -> str:
    components = result.get("components") if isinstance(result.get("components"), dict) else {}
    scores = components.get("scores") if isinstance(components, dict) else {}
    scores = scores if isinstance(scores, dict) else {}
    return (
        "EGX Final Weighted Decision\n\n"
        f"Symbol: {result.get('symbol')}\n"
        f"Final Signal: {result.get('final_signal')}\n"
        f"Final Score: {float(result.get('final_score') or 0):.0f}%\n\n"
        f"Technical: {float(scores.get('technical') or 0):.0f}%\n"
        f"Financial: {float(scores.get('financial') or 0):.0f}%\n"
        f"News: {float(scores.get('news') or 0):.0f}%\n"
        f"Telegram: {float(scores.get('telegram') or 0):.0f}%\n"
        f"Strategy: {float(scores.get('strategy') or 0):.0f}%\n\n"
        f"Best Driver Today: {result.get('best_analysis_today') or '-'}\n"
        f"Best Strategy Today: {result.get('best_strategy_today') or '-'}\n"
        f"Entry: {result.get('entry_price') or '-'}\n"
        f"Stop Loss: {result.get('stop_loss') or '-'}\n"
        f"TP1: {result.get('take_profit_1') or '-'}\n"
        f"TP2: {result.get('take_profit_2') or '-'}\n"
        f"Risk: {result.get('risk_level') or '-'}\n\n"
        f"Reason: {result.get('reason') or '-'}\n\n"
        f"{RESEARCH_DISCLAIMER}"
    )


async def _reply(update: Update, text: str) -> None:
    text = _with_risk_note(text)
    if len(text) <= 3900:
        await update.message.reply_text(text)
        return
    await update.message.reply_text(text[:3890].rstrip() + "\n...shortened")


def _normalize_symbol_arg(arg: str) -> str:
    return str(arg or "").upper().replace("EGX:", "").replace(".CA", "").strip()


def _format_financial_report(symbol: str) -> str:
    symbol = _normalize_symbol_arg(symbol)
    with SessionLocal() as db:
        raw_count = db.scalar(select(func.count()).select_from(FinancialData).where(FinancialData.symbol == symbol)) or 0
        signal = db.scalar(
            select(FinancialSignal)
            .where(FinancialSignal.symbol == symbol)
            .order_by(FinancialSignal.signal_date.desc(), FinancialSignal.id.desc())
        )
    if not signal:
        return (
            "EGX Financial Analysis\n\n"
            f"Symbol: {symbol}\n"
            "Status: no financial signal stored yet.\n"
            f"Raw statement rows: {raw_count}\n\n"
            "Run /analysis SYMBOL or upload financial CSV from the Financial Analysis page."
        )
    source_status = "real uploaded financial rows available" if raw_count else "no raw financial rows yet; neutral fallback may be used"
    return (
        "EGX Financial Analysis\n\n"
        f"Symbol: {symbol}\n"
        f"Signal: {signal.financial_signal}\n"
        f"Score: {float(signal.financial_score or 0):.0f}%\n"
        f"Profitability: {signal.profitability_score if signal.profitability_score is not None else '-'}\n"
        f"Growth: {signal.growth_score if signal.growth_score is not None else '-'}\n"
        f"Valuation: {signal.valuation_score if signal.valuation_score is not None else '-'}\n"
        f"Debt: {signal.debt_score if signal.debt_score is not None else '-'}\n"
        f"Cash Flow: {signal.cashflow_score if signal.cashflow_score is not None else '-'}\n"
        f"Risk: {signal.risk_level or '-'}\n"
        f"Raw statement rows: {raw_count}\n"
        f"Source status: {source_status}\n"
        f"Latest update: {signal.signal_date:%Y-%m-%d %H:%M}\n\n"
        f"Reason: {signal.reason or '-'}"
    )


def _format_news_report(symbol: str) -> str:
    symbol = _normalize_symbol_arg(symbol)
    with SessionLocal() as db:
        raw_count = db.scalar(select(func.count()).select_from(StockNews).where(StockNews.symbol == symbol)) or 0
        signal = db.scalar(
            select(NewsSignal)
            .where(NewsSignal.symbol == symbol)
            .order_by(NewsSignal.signal_date.desc(), NewsSignal.id.desc())
        )
        news_rows = db.scalars(
            select(StockNews)
            .where(StockNews.symbol == symbol)
            .order_by(StockNews.published_at.desc().nullslast(), StockNews.created_at.desc())
            .limit(3)
        ).all()
    if not signal:
        return (
            "EGX News Analysis\n\n"
            f"Symbol: {symbol}\n"
            "Status: no news signal stored yet.\n"
            f"Raw news rows: {raw_count}\n\n"
            "Run /analysis SYMBOL or import news CSV from the News Analysis page."
        )
    lines = [
        "EGX News Analysis",
        "",
        f"Symbol: {symbol}",
        f"Signal: {signal.news_signal}",
        f"Score: {float(signal.news_score or 0):.0f}%",
        f"Raw news rows: {raw_count}",
        f"Latest update: {signal.signal_date:%Y-%m-%d %H:%M}",
        "",
        f"Reason: {signal.reason or '-'}",
    ]
    if news_rows:
        lines.extend(["", "Latest news:"])
        for row in news_rows:
            title = (row.title or row.body or row.source or "news").replace("\n", " ")[:120]
            lines.append(f"- {title} | {row.sentiment or '-'} | impact {float(row.impact_score or 0):.0f}")
    else:
        lines.extend(["", "Latest news: none stored for this symbol yet. Neutral fallback may be used."])
    return "\n".join(lines)


def _format_portfolio_status() -> str:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        settings = get_portfolio_settings(db)
        values = portfolio_value(db, settings)
        guard = risk_guard_status(db, settings)
        safety = safety_snapshot(db)
        paper_block_reason = execution_block_reason(db, block_paper_execution=False)
        live_block_reason = execution_block_reason(db, block_paper_execution=True)
        automation_scan = get_bool(db, "automation_run_portfolio_bot", False)
        auto_execute = get_bool(db, "portfolio_bot_auto_execute_paper_trades", False)
        symbol_limit = get_int(db, "portfolio_bot_symbol_limit", 50, minimum=1)
        open_count = db.scalar(select(func.count()).select_from(PortfolioPosition).where(PortfolioPosition.status == "open")) or 0
        trade_count = db.scalar(select(func.count()).select_from(PortfolioTrade)) or 0
        latest_trades = db.scalars(select(PortfolioTrade).order_by(PortfolioTrade.trade_date.desc()).limit(5)).all()
    lines = [
        "EGX Portfolio Bot Status",
        "",
        "Mode: paper trading only",
        f"Portfolio bot: {'ON' if settings.portfolio_bot_enabled else 'OFF'}",
        f"Automation scan: {'ON' if automation_scan else 'OFF'}",
        f"Auto paper execution: {'ON' if auto_execute else 'OFF'}",
        f"Manual buy confirmation: {'ON' if settings.require_manual_buy_confirmation else 'OFF'}",
        f"Manual sell confirmation: {'ON' if settings.require_manual_sell_confirmation else 'OFF'}",
        f"Scan limit: {symbol_limit}",
        "",
        f"Audit mode: {'ON' if safety.get('audit_mode') else 'OFF'}",
        f"Emergency stop: {'ON' if safety.get('emergency_stop_trading') else 'OFF'}",
        f"Live trading: {'ON' if safety.get('live_trading_enabled') else 'OFF'}",
        f"Live execution blocked: {'YES' if live_block_reason else 'NO'}",
        f"Live block reasons: {live_block_reason or '-'}",
        f"Paper execution blocked: {'YES' if paper_block_reason else 'NO'}",
        f"Paper block reasons: {paper_block_reason or '-'}",
        "",
        f"Cash: {values['cash']:,.2f} EGP",
        f"Invested: {values['invested']:,.2f} EGP",
        f"Total value: {values['total_value']:,.2f} EGP",
        f"P/L: {values['profit_loss']:,.2f} EGP ({values['profit_loss_pct']:.2f}%)",
        f"Open positions: {open_count}",
        f"Total paper trades: {trade_count}",
        "",
        f"Risk guard: {'OK' if guard.get('allowed') else 'BLOCKED'}",
        f"Risk guard reasons: {', '.join(guard.get('reasons') or ['None'])}",
    ]
    if latest_trades:
        lines.extend(["", "Latest trades:"])
        for row in latest_trades:
            pnl = "" if row.trade_type == "BUY" else f" | P/L {float(row.profit_loss or 0):,.2f} EGP"
            lines.append(
                f"- {row.trade_type} {row.symbol} | {row.quantity} @ {row.price:,.2f}"
                f" | {row.trade_date:%Y-%m-%d %H:%M}{pnl}"
            )
    lines.extend(
        [
            "",
            "Commands:",
            "/portfolio_start auto - enable automatic paper buys/sells",
            "/portfolio_start manual - propose trades only",
            "/portfolio_run - run one execution cycle now",
            "/portfolio_scan - scan candidates only",
            "/portfolio_stop - stop portfolio automation",
        ]
    )
    return "\n".join(lines)


def _format_portfolio_actions(result: dict[str, Any], *, title: str) -> str:
    actions = result.get("actions") or []
    portfolio = result.get("portfolio") or {}
    lines = [
        title,
        "",
        f"Actions: {len(actions)}",
        f"Portfolio total: {float(portfolio.get('total_value') or 0):,.2f} EGP",
        f"Cash: {float(portfolio.get('cash') or 0):,.2f} EGP",
    ]
    if not actions:
        lines.append("No buy/sell candidates found in this cycle.")
        return "\n".join(lines)
    lines.extend(["", "Latest actions:"])
    for action in actions[:12]:
        status = str(action.get("status") or "-")
        symbol = action.get("symbol") or "-"
        score = action.get("final_score")
        reason = str(action.get("reason") or "-").replace("\n", " ")[:180]
        if status == "bought":
            lines.append(
                f"- BUY {symbol}: {action.get('quantity')} @ {float(action.get('price') or 0):,.2f} "
                f"| score {float(score or 0):.0f}%"
            )
        elif status == "sold":
            lines.append(
                f"- SELL {symbol}: {action.get('quantity')} @ {float(action.get('price') or 0):,.2f} "
                f"| P/L {float(action.get('profit_loss') or 0):,.2f} EGP"
            )
        elif status == "pending_approval":
            lines.append(f"- Approval needed {symbol}: score {float(score or 0):.0f}% | {reason}")
        elif symbol != "-":
            lines.append(f"- {status} {symbol}: {reason}")
        else:
            lines.append(f"- {status}: {reason}")
    if len(actions) > 12:
        lines.append(f"...and {len(actions) - 12} more.")
    return "\n".join(lines)


def _set_portfolio_mode_sync(*, enabled: bool, auto_execute: bool) -> str:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        if enabled:
            block_reason = execution_block_reason(db, block_paper_execution=False)
            if block_reason:
                set_setting(db, "automation_run_portfolio_bot", "false", value_type="bool")
                set_setting(db, "portfolio_bot_auto_execute_paper_trades", "false", value_type="bool")
                row = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc())) or PortfolioSetting()
                row.portfolio_bot_enabled = False
                row.trading_mode = "paper_trading"
                row.require_manual_buy_confirmation = True
                row.require_manual_sell_confirmation = True
                db.add(row)
                db.commit()
                return (
                    "Emergency Stop Enabled\n\n"
                    "Portfolio automation cannot be started while audit/emergency safety is active.\n"
                    f"Reason: {block_reason}\n\n"
                    "Live trading is disabled. The system is currently in audit/simulation mode only."
                )
        row = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc())) or PortfolioSetting()
        row.portfolio_bot_enabled = enabled
        row.trading_mode = "paper_trading"
        row.timezone = "Africa/Cairo"
        if enabled:
            row.require_manual_buy_confirmation = not auto_execute
            row.require_manual_sell_confirmation = not auto_execute
        db.add(row)
        set_setting(db, "automation_run_portfolio_bot", "true" if enabled else "false", value_type="bool")
        set_setting(db, "portfolio_bot_auto_execute_paper_trades", "true" if enabled and auto_execute else "false", value_type="bool")
        db.commit()
    if not enabled:
        return "Portfolio automation stopped. Existing paper positions remain open and can still be managed manually."
    if auto_execute:
        return (
            "Auto paper trading started.\n\n"
            "The automation runner can now execute virtual BUY/SELL trades from the final weighted analysis. "
            "Every executed transaction will be sent by Telegram."
        )
    return (
        "Portfolio bot started in approval mode.\n\n"
        "It will scan and create proposed trades, but will not execute buys/sells automatically."
    )


def _portfolio_scan_sync(*, execute: bool = False) -> str:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        limit = get_int(db, "portfolio_bot_symbol_limit", 50, minimum=1)
        if execute:
            result = run_daily_portfolio_bot(db, execute=True, force=False, limit=limit)
        else:
            result = scan_portfolio(db, execute=False, limit=limit)
        db.commit()
    return _format_portfolio_actions(
        result,
        title="EGX Portfolio Bot Execution" if execute else "EGX Portfolio Bot Scan",
    )


async def _authorized(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    admin_only: bool = False,
) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    created = False
    with SessionLocal() as db:
        subscriber = register_from_update(db, update, settings=settings, activate=True)
        is_admin = subscriber_is_admin(db, chat.id, settings=settings)
        bot_user = db.scalar(select(BotUser).where(BotUser.chat_id == chat.id))
        if not bot_user:
            bot_user = BotUser(
                chat_id=chat.id,
                telegram_user_id=getattr(update.effective_user, "id", None),
                username=getattr(update.effective_user, "username", None),
                is_active=True,
            )
            db.add(bot_user)
            created = True
        else:
            bot_user.telegram_user_id = getattr(update.effective_user, "id", bot_user.telegram_user_id)
            bot_user.username = getattr(update.effective_user, "username", bot_user.username)
            if is_admin:
                bot_user.is_active = True
        is_active = bool(bot_user.is_active)
        db.commit()
        subscriber_allowed = subscriber_can_use_bot(db, chat.id, settings=settings)

    if is_admin or (is_active and subscriber_allowed):
        if admin_only and not is_admin:
            await _reply(update, "Only the admin can use this command.")
            return False
        return True

    await update.message.reply_text("Your bot access is disabled in the dashboard. Send /id to share your chat id with the admin.")
    if created:
        await _notify_admins(
            context,
            settings,
            "New EGX bot access request\n"
            f"User: {_user_label(update)}\n"
            f"Chat ID: {chat.id}\n\n"
            f"Approve: /approve_user {chat.id}\n"
            f"Reject: /reject_user {chat.id}",
        )
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    logger.info("Command /start handled for chat %s", update.effective_chat.id if update.effective_chat else "-")
    await _reply(
        update,
        "Welcome to EGX Analysis Bot \u2705\n"
        "You can request stock analysis, view opportunities, check market status, and receive system alerts.",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    await _reply(
        update,
        "EGX Analysis Bot Commands\n"
        "/start\n"
        "/subscribe\n"
        "/unsubscribe\n"
        "/profile\n"
        "/analysis SYMBOL\n"
        "/decision SYMBOL\n"
        "/financial SYMBOL\n"
        "/news SYMBOL\n"
        "/opportunities\n"
        "/market\n"
        "/latest\n"
        "/backtest SYMBOL\n"
        "/strategy SYMBOL\n"
        "/screening\n"
        "/alerts\n"
        "/automation_status\n"
        "/automation\n"
        "/automation_start\n"
        "/automation_stop\n"
        "/env_status\n"
        "/image_status\n"
        "/watchlist\n"
        "/portfolio\n"
        "/portfolio_status\n"
        "/portfolio_start auto\n"
        "/portfolio_start manual\n"
        "/portfolio_scan\n"
        "/portfolio_run\n"
        "/portfolio_stop\n"
        "/id\n"
        "/status\n\n"
        "You can also send a symbol directly, for example COMI.",
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    await _reply(update, f"Chat ID: {chat.id}")


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    chat = update.effective_chat
    with SessionLocal() as db:
        register_from_update(db, update, settings=settings, activate=True)
        set_subscription_flags(db, chat.id, is_active=True, can_receive_alerts=True, can_use_bot=True)
        db.commit()
    await _reply(update, "Subscribed. This chat can receive EGX alerts.")


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    chat = update.effective_chat
    with SessionLocal() as db:
        set_subscription_flags(db, chat.id, can_receive_alerts=False)
        db.commit()
    await _reply(update, "Alerts disabled for this chat. You can still use the bot commands.")


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    chat = update.effective_chat
    with SessionLocal() as db:
        subscriber = db.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id == str(chat.id)))
        await _reply(update, format_profile(subscriber))


async def subscribers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    with SessionLocal() as db:
        rows = list_subscribers(db, include_inactive=True)
    await _reply(update, format_subscribers(rows))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    await _reply(update, get_status_report(settings=settings))


async def text_request_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    text = (update.message.text or "").strip()
    if text and len(text.split()) == 1 and text.replace(".", "").replace("-", "").isalnum():
        symbol = text.upper().replace("EGX:", "")
        logger.info("Plain symbol analysis requested: %s", symbol)
        with SessionLocal() as db:
            enqueue_backtest(db, symbol, reason="Bot plain symbol request", priority=2, requested_by=str(update.effective_chat.id))
            result = build_final_decision(db, symbol, run_sources=True, persist=True)
            db.commit()
        await _reply(update, _format_intelligence_decision(result))
        return
    await _reply(update, "Send /analysis SYMBOL, /opportunities, /market, /latest, /screening, /backtest SYMBOL, or /help.")


async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    if not context.args:
        await update.message.reply_text("Usage: /add_channel @channel1 @channel2")
        return
    usernames = [_normalize_channel(arg) for arg in context.args]
    with SessionLocal() as db:
        for username in usernames:
            source = db.scalar(select(TelegramSource).where(TelegramSource.username == username))
            if source:
                source.is_active = True
                source.notes = source.notes or "Reactivated from bot command."
            else:
                source = TelegramSource(username=username, title=username, source_type="channel", is_active=True, trust_score=50.0)
                db.add(source)
        db.commit()
    await update.message.reply_text(f"Added/activated {len(usernames)} channel(s). Fetching now...")
    try:
        result = await run_ingestion_cycle_async()
        await update.message.reply_text(f"Updated. {_ingestion_result_text(result)}\nDisclaimer: {DISCLAIMER}")
    except Exception as exc:
        logger.exception("Immediate channel refresh failed")
        await update.message.reply_text(f"Saved channels, but refresh failed: {exc}\nDisclaimer: {DISCLAIMER}")


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove_channel @channel1 @channel2")
        return
    usernames = [_normalize_channel(arg) for arg in context.args]
    with SessionLocal() as db:
        db.execute(delete(TelegramSource).where(TelegramSource.username.in_(usernames)))
        db.commit()
    await update.message.reply_text(f"Removed {len(usernames)} channel(s).")


async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    with SessionLocal() as db:
        sources = db.scalars(select(TelegramSource).order_by(TelegramSource.username)).all()
    if not sources:
        await update.message.reply_text("No channels configured.")
        return
    lines = [
        f"{source.username} | {'active' if source.is_active else 'paused'} | trust {source.trust_score:.0f} | last {source.last_message_id}"
        for source in sources
    ]
    await update.message.reply_text("\n".join(lines) + f"\n\nDisclaimer: {DISCLAIMER}")


async def _set_channel_active(update: Update, context: ContextTypes.DEFAULT_TYPE, active: bool) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    if not context.args:
        command = "activate_channel" if active else "pause_channel"
        await update.message.reply_text(f"Usage: /{command} @channel1 @channel2")
        return
    usernames = [_normalize_channel(arg) for arg in context.args]
    changed = 0
    with SessionLocal() as db:
        sources = db.scalars(select(TelegramSource).where(TelegramSource.username.in_(usernames))).all()
        for source in sources:
            source.is_active = active
            changed += 1
        db.commit()
    await update.message.reply_text(f"{changed} channel(s) are now {'active' if active else 'paused'}.")


async def pause_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_channel_active(update, context, False)


async def activate_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_channel_active(update, context, True)


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    if not context.args:
        await update.message.reply_text("Usage: /analyze SYMBOL")
        return
    symbol = context.args[0].upper()
    with SessionLocal() as db:
        try:
            final = analyze_symbol_manually(db, symbol=symbol)
            await update.message.reply_text(format_alert(final))
        except Exception as exc:
            logger.exception("Manual analysis failed")
            await update.message.reply_text(f"Could not analyze {symbol}: {exc}\nDisclaimer: {DISCLAIMER}")


async def analysis_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    if not context.args:
        await update.message.reply_text("Usage: /analysis SYMBOL")
        return
    symbol = context.args[0].upper().replace("EGX:", "")
    logger.info("Command /analysis handled for %s", symbol)
    try:
        with SessionLocal() as db:
            enqueue_backtest(db, symbol, reason="Bot /analysis request", priority=2, requested_by=str(update.effective_chat.id))
            result = build_final_decision(db, symbol, run_sources=True, persist=True)
            db.commit()
        legacy = format_combined_analysis_report(symbol, settings=settings, refresh=False)
        await _reply(update, _format_intelligence_decision(result) + "\n\nExisting Combined Analysis:\n" + legacy[:1600])
    except Exception as exc:
        logger.exception("Analysis command failed")
        await _reply(update, f"Could not build analysis for {symbol}: {exc}\nDisclaimer: {DISCLAIMER}")


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    if not context.args:
        await update.message.reply_text("Usage: /stock SYMBOL")
        return
    symbol = context.args[0].upper().replace("EGX:", "")
    with SessionLocal() as db:
        try:
            message = build_stock_brief(db, symbol=symbol, settings=settings)
            await update.message.reply_text(message)
        except Exception as exc:
            logger.exception("Stock brief failed")
            await update.message.reply_text(f"Could not build stock brief for {symbol}: {exc}\nDisclaimer: {DISCLAIMER}")


async def decision_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    if not context.args:
        await update.message.reply_text("Usage: /decision SYMBOL")
        return
    symbol = context.args[0].upper().replace("EGX:", "")
    with SessionLocal() as db:
        try:
            enqueue_backtest(db, symbol, reason="Bot /decision request", priority=2, requested_by=str(update.effective_chat.id))
            result = build_final_decision(db, symbol, run_sources=True, persist=True)
            db.commit()
            await _reply(update, _format_intelligence_decision(result))
        except Exception as exc:
            logger.exception("Final decision failed")
            await _reply(update, f"Could not build final decision for {symbol}: {exc}\nDisclaimer: {DISCLAIMER}")


async def financial_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    if not context.args:
        await update.message.reply_text("Usage: /financial SYMBOL")
        return
    await _reply(update, _format_financial_report(context.args[0]))


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    if not context.args:
        await update.message.reply_text("Usage: /news SYMBOL")
        return
    await _reply(update, _format_news_report(context.args[0]))


async def latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    limit = 10
    if context.args:
        try:
            limit = max(1, min(25, int(context.args[0])))
        except ValueError:
            limit = 10
    await _reply(update, get_latest_recommendations(limit=limit))


async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    with SessionLocal() as db:
        message = build_daily_report(db, settings=settings)
    await update.message.reply_text(message)


async def opportunities(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    limit = 5
    if context.args:
        try:
            limit = max(1, min(10, int(context.args[0])))
        except ValueError:
            limit = 5
    await _reply(update, get_top_opportunities(limit=limit, settings=settings))


async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    await _reply(update, get_market_summary(settings=settings))


async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    await _reply(update, send_buy_alerts())


async def automation_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    await _reply(update, get_automation_status_report(settings=settings))


async def automation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    await _reply(update, get_automation_status_report(settings=settings))


async def automation_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    await _reply(update, automation_start_report())


async def automation_stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    await _reply(update, automation_stop_report())


async def image_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(TelegramMediaAnalysis)) or 0
        latest = db.scalars(select(TelegramMediaAnalysis).order_by(TelegramMediaAnalysis.created_at.desc()).limit(5)).all()
    lines = ["Telegram Media Analysis Status", f"Rows: {total}", ""]
    if latest:
        for row in latest:
            lines.append(f"- {row.created_at:%Y-%m-%d %H:%M} | {row.status} | symbols {row.detected_symbols or '-'}")
    else:
        lines.append("No media analysis rows are stored yet.")
    await _reply(update, "\n".join(lines))


async def env_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    await _reply(update, format_env_health())


async def strategy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    if not context.args:
        await update.message.reply_text("Usage: /strategy SYMBOL")
        return
    await _reply(update, get_strategy_summary(context.args[0]))


async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    if not context.args:
        await update.message.reply_text("Usage: /backtest SYMBOL")
        return
    symbol = context.args[0].upper().replace("EGX:", "")
    with SessionLocal() as db:
        enqueue_backtest(db, symbol, reason="Bot /backtest request", priority=1, requested_by=str(update.effective_chat.id))
        db.commit()
    await _reply(update, get_backtest_summary(symbol))


async def screening_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    symbol = context.args[0] if context.args else None
    await _reply(update, get_tradingview_screening_summary(symbol=symbol))


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    await _reply(update, get_watchlist(limit=10))


async def portfolio_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    await _reply(update, await asyncio.to_thread(_format_portfolio_status))


async def portfolio_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    mode = str(context.args[0]).lower().strip() if context.args else "auto"
    auto_execute = mode not in {"manual", "approval", "approve", "propose"}
    text = await asyncio.to_thread(_set_portfolio_mode_sync, enabled=True, auto_execute=auto_execute)
    await _reply(update, text + "\n\nMode: paper trading only. Real broker execution is disabled.")


async def portfolio_stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    text = await asyncio.to_thread(_set_portfolio_mode_sync, enabled=False, auto_execute=False)
    await _reply(update, text)


async def portfolio_scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    await _reply(update, await asyncio.to_thread(_portfolio_scan_sync, execute=False))


async def portfolio_run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    await _reply(update, await asyncio.to_thread(_portfolio_scan_sync, execute=True))


async def score_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    if not context.args:
        await update.message.reply_text("Usage: /score SYMBOL")
        return
    await _reply(update, get_combined_opportunity_score(context.args[0]))


async def market_depth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings):
        return
    limit = 10
    if context.args:
        try:
            limit = max(1, min(25, int(context.args[0])))
        except ValueError:
            limit = 10
    await update.message.reply_text(build_market_depth_report(settings=settings, limit=limit))


async def refresh_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    await update.message.reply_text("Refreshing Telegram sources now...")
    try:
        result = await run_ingestion_cycle_async()
        await update.message.reply_text(f"Refresh complete. {_ingestion_result_text(result)}\nDisclaimer: {DISCLAIMER}")
    except Exception as exc:
        logger.exception("Refresh sources command failed")
        await update.message.reply_text(f"Refresh failed: {exc}\nDisclaimer: {DISCLAIMER}")


def _parse_chat_id_arg(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    if not context.args:
        return None
    try:
        return int(context.args[0])
    except (TypeError, ValueError):
        return None


async def pending_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    with SessionLocal() as db:
        users = db.scalars(select(BotUser).where(BotUser.is_active.is_(False)).order_by(BotUser.created_at.desc()).limit(25)).all()
    if not users:
        await update.message.reply_text("No pending users.")
        return
    lines = [
        f"{user.chat_id} | @{user.username or '-'} | requested {user.created_at:%Y-%m-%d %H:%M}"
        for user in users
    ]
    await update.message.reply_text("Pending users:\n" + "\n".join(lines))


async def list_bot_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    with SessionLocal() as db:
        users = db.scalars(select(BotUser).order_by(BotUser.updated_at.desc()).limit(50)).all()
    if not users:
        await update.message.reply_text("No bot users yet.")
        return
    lines = [
        f"{user.chat_id} | {'active' if user.is_active else 'pending'} | @{user.username or '-'}"
        for user in users
    ]
    await update.message.reply_text("Bot users:\n" + "\n".join(lines))


async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    chat_id = _parse_chat_id_arg(context)
    if chat_id is None:
        await update.message.reply_text("Usage: /approve_user CHAT_ID")
        return
    with SessionLocal() as db:
        user = db.scalar(select(BotUser).where(BotUser.chat_id == chat_id))
        if user:
            user.is_active = True
        else:
            user = BotUser(chat_id=chat_id, is_active=True)
            db.add(user)
        db.commit()
    await update.message.reply_text(f"Approved chat {chat_id}.")
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Your EGX bot access is approved. Send /stock SYMBOL, /latest, or /opportunities.\n" f"Disclaimer: {DISCLAIMER}",
        )
    except Exception:
        logger.exception("Could not notify approved user %s", chat_id)


async def reject_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not await _authorized(update, context, settings, admin_only=True):
        return
    chat_id = _parse_chat_id_arg(context)
    if chat_id is None:
        await update.message.reply_text("Usage: /reject_user CHAT_ID")
        return
    with SessionLocal() as db:
        user = db.scalar(select(BotUser).where(BotUser.chat_id == chat_id))
        if user:
            user.is_active = False
        else:
            db.add(BotUser(chat_id=chat_id, is_active=False))
        db.commit()
    await update.message.reply_text(f"Rejected/disabled chat {chat_id}.")
    try:
        await context.bot.send_message(chat_id=chat_id, text="Your EGX bot access was not approved.")
    except Exception:
        logger.exception("Could not notify rejected user %s", chat_id)


def create_bot_application(settings: Settings | None = None) -> Any | None:
    settings = settings or get_settings()
    if not settings.telegram_bot_token:
        logger.info("Telegram bot token not configured; bot disabled.")
        return None
    if Application is None:
        logger.warning("python-telegram-bot is not installed; bot disabled.")
        return None
    builder = Application.builder().token(settings.telegram_bot_token)
    builder = builder.request(_telegram_request(settings)).get_updates_request(_telegram_request(settings))
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("subscribers", subscribers_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("add_channel", add_channel))
    app.add_handler(CommandHandler("remove_channel", remove_channel))
    app.add_handler(CommandHandler("list_channels", list_channels))
    app.add_handler(CommandHandler("pause_channel", pause_channel))
    app.add_handler(CommandHandler("activate_channel", activate_channel))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("analysis", analysis_command))
    app.add_handler(CommandHandler("stock", stock_command))
    app.add_handler(CommandHandler("brief", stock_command))
    app.add_handler(CommandHandler("decision", decision_command))
    app.add_handler(CommandHandler("final", decision_command))
    app.add_handler(CommandHandler("financial", financial_command))
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("latest", latest))
    app.add_handler(CommandHandler("market", market_command))
    app.add_handler(CommandHandler("alerts", alerts_command))
    app.add_handler(CommandHandler("automation_status", automation_status_command))
    app.add_handler(CommandHandler("automation", automation_command))
    app.add_handler(CommandHandler("automation_start", automation_start_command))
    app.add_handler(CommandHandler("automation_stop", automation_stop_command))
    app.add_handler(CommandHandler("env_status", env_status_command))
    app.add_handler(CommandHandler("image_status", image_status_command))
    app.add_handler(CommandHandler("strategy", strategy_command))
    app.add_handler(CommandHandler("backtest", backtest_command))
    app.add_handler(CommandHandler("screening", screening_command))
    app.add_handler(CommandHandler("watchlist", watchlist_command))
    app.add_handler(CommandHandler("portfolio", portfolio_status_command))
    app.add_handler(CommandHandler("portfolio_status", portfolio_status_command))
    app.add_handler(CommandHandler("portfolio_start", portfolio_start_command))
    app.add_handler(CommandHandler("portfolio_stop", portfolio_stop_command))
    app.add_handler(CommandHandler("portfolio_scan", portfolio_scan_command))
    app.add_handler(CommandHandler("portfolio_run", portfolio_run_command))
    app.add_handler(CommandHandler("portfolio_execute", portfolio_run_command))
    app.add_handler(CommandHandler("score", score_command))
    app.add_handler(CommandHandler("daily_report", daily_report))
    app.add_handler(CommandHandler("opportunities", opportunities))
    app.add_handler(CommandHandler("opportunity", opportunities))
    app.add_handler(CommandHandler("night_report", opportunities))
    app.add_handler(CommandHandler("depth", market_depth))
    app.add_handler(CommandHandler("market_depth", market_depth))
    app.add_handler(CommandHandler("refresh_sources", refresh_sources))
    app.add_handler(CommandHandler("pending_users", pending_users))
    app.add_handler(CommandHandler("users", list_bot_users))
    app.add_handler(CommandHandler("approve_user", approve_user))
    app.add_handler(CommandHandler("reject_user", reject_user))
    if MessageHandler is not None and filters is not None:
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_request_access))
    app.add_error_handler(error_handler)
    return app


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = getattr(context, "error", None)
    name = error.__class__.__name__ if error else "Unknown"
    if name in {"NetworkError", "TimedOut", "RetryAfter"}:
        logger.warning("Telegram polling/transport issue: %s", error)
        return
    logger.error("Telegram update handling error: %s", error)


async def start_bot_application(bot_app: Any | None) -> None:
    if not bot_app:
        return
    await bot_app.initialize()
    await bot_app.start()
    if bot_app.updater:
        await bot_app.updater.start_polling()
    logger.info("Telegram bot polling started.")


async def stop_bot_application(bot_app: Any | None) -> None:
    if not bot_app:
        return
    if bot_app.updater:
        await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()


async def send_private_message(text: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    text = _with_risk_note(text)
    if not settings.telegram_bot_token or Bot is None:
        logger.info("Private bot alert skipped; token is not configured or bot library is unavailable.")
        return
    chat_ids = _approved_alert_chat_ids(settings)
    if not chat_ids:
        logger.info("Private bot alert skipped; no approved bot users.")
        return
    bot = Bot(settings.telegram_bot_token, request=_telegram_request(settings))
    sent = 0
    last_error: Exception | None = None
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            sent += 1
        except Exception as exc:
            last_error = exc
            logger.warning("Could not send bot message to chat %s: %s", chat_id, exc)
    if sent == 0 and last_error:
        raise last_error


def send_private_message_sync(text: str, settings: Settings | None = None) -> None:
    asyncio.run(send_private_message(text, settings=settings))


async def send_private_documents(
    text: str,
    document_paths: list[str | Path],
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    text = _with_risk_note(text)
    paths = [Path(path) for path in document_paths if path]
    result: dict[str, Any] = {
        "configured": bool(settings.telegram_bot_token and Bot is not None),
        "eligible": 0,
        "sent_messages": 0,
        "sent_documents": 0,
        "failed": 0,
        "errors": [],
    }
    if not settings.telegram_bot_token or Bot is None:
        logger.info("Private bot document alert skipped; token is not configured or bot library is unavailable.")
        return result
    chat_ids = _approved_alert_chat_ids(settings)
    result["eligible"] = len(chat_ids)
    if not chat_ids:
        logger.info("Private bot document alert skipped; no approved bot users.")
        return result

    bot = Bot(settings.telegram_bot_token, request=_telegram_request(settings))
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            result["sent_messages"] += 1
            for path in paths:
                if not path.exists():
                    result["errors"].append(f"Missing document: {path.name}")
                    continue
                with path.open("rb") as handle:
                    await bot.send_document(chat_id=chat_id, document=handle, filename=path.name)
                result["sent_documents"] += 1
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append(str(exc))
            logger.warning("Could not send bot document alert to chat %s: %s", chat_id, exc)
    return result


def send_private_documents_sync(
    text: str,
    document_paths: list[str | Path],
    settings: Settings | None = None,
) -> dict[str, Any]:
    return asyncio.run(send_private_documents(text, document_paths, settings=settings))


if __name__ == "__main__":
    bot_application = create_bot_application()
    if bot_application is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not configured.")
    bot_application.run_polling(drop_pending_updates=True)
