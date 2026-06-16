from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import DISCLAIMER, Settings, get_settings
from app.models import ExtractedSignal, FinalAnalysis, Stock, TelegramMessage, TelegramSource
from app.services.screener_recommendations import build_final_recommendations, tradingview_chart_url
from app.services.strategy import run_strategy_for_symbol, run_strategy_universe


TELEGRAM_MESSAGE_LIMIT = 3900


def _fmt_num(value: Any, digits: int = 0) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _local_now(settings: Settings) -> datetime:
    try:
        return datetime.now(ZoneInfo(settings.timezone))
    except ZoneInfoNotFoundError:
        return datetime.utcnow()


def _limit_message(message: str) -> str:
    if len(message) <= TELEGRAM_MESSAGE_LIMIT:
        return message
    suffix = f"\n\nMessage shortened for Telegram.\nDisclaimer: {DISCLAIMER}"
    return message[: TELEGRAM_MESSAGE_LIMIT - len(suffix)].rstrip() + suffix


def _first(items: list[Any] | None, default: str = "-") -> str:
    if not items:
        return default
    return ", ".join(str(item) for item in items[:3])


def _compact_list(items: list[str] | None, limit: int = 2) -> str:
    if not items:
        return "-"
    return "; ".join(str(item) for item in items[:limit])


def _signal_channel(signal: ExtractedSignal) -> str:
    if signal.source:
        return signal.source.title or signal.source.username
    return "-"


def _night_opportunity_score(row: dict[str, Any], strategy: dict[str, Any] | None) -> tuple[float, list[str], list[str]]:
    final_score = float(row.get("final_score") or 0)
    strategy_score = float((strategy or {}).get("strategy_score") or 0)
    final_action = str(row.get("final_recommendation") or "").upper()
    strategy_action = str((strategy or {}).get("strategy_action") or "").upper()
    telegram_vote = str(row.get("telegram_vote") or "NONE").upper()
    telegram_signals = int(row.get("telegram_signals") or 0)
    telegram_buy = int(row.get("telegram_buy") or 0)
    warnings = [str(item) for item in (row.get("warnings") or [])]

    score = final_score * 0.45 + strategy_score * 0.35
    reasons: list[str] = []
    risks: list[str] = []

    if final_action == "BUY":
        score += 10
        reasons.append("final screener view is BUY")
    elif final_action == "WATCH":
        score += 5
        reasons.append("final screener view is WATCH")
    elif final_action in {"SELL", "AVOID", "HIGH_RISK"}:
        score -= 18
        risks.append(f"final screener view is {final_action}")

    if strategy_action == "BUY":
        score += 10
        reasons.append("strategy has BUY alignment")
    elif strategy_action == "WATCH":
        score += 5
        reasons.append("strategy is on WATCH")
    elif strategy_action == "AVOID":
        score -= 12
        risks.append("strategy says AVOID")

    if telegram_vote == "POSITIVE":
        score += 8
        reasons.append("Telegram consensus is positive")
    elif telegram_vote == "MIXED":
        score += 3
        reasons.append("Telegram mentions are mixed but active")
    elif telegram_vote == "NEGATIVE":
        score -= 8
        risks.append("Telegram consensus is negative")

    if telegram_signals:
        score += min(8, telegram_buy * 2 + telegram_signals)
        reasons.append(f"{telegram_signals} recent Telegram signal(s)")

    if strategy:
        higher_frames = [
            frame
            for frame in strategy.get("timeframes", [])
            if frame.get("timeframe") in {"4h", "1D"} and frame.get("action") in {"BUY", "WATCH"}
        ]
        if higher_frames:
            score += 5
            reasons.append("4h/1D frame confirms")
        if strategy.get("uses_mock_data"):
            risks.append("some candles are fallback/mock data")
        if strategy.get("data_quality") == "unavailable":
            risks.append("strategy candles unavailable")

    warning_text = " ".join(warnings).lower()
    if "overbought" in warning_text:
        score -= 7
        risks.append("RSI/chase risk")
    if "hype" in warning_text:
        score -= 5
        risks.append("Telegram hype risk")
    if "missing stop" in warning_text or int(row.get("missing_stop") or 0):
        score -= 4
        risks.append("some Telegram ideas missed stop loss")

    return round(max(0.0, min(100.0, score)), 2), reasons[:4], risks[:4]


def _rank_night_opportunities(rec_rows: list[dict[str, Any]], strategy_rows: list[dict[str, Any]], min_entries: int = 5) -> list[dict[str, Any]]:
    strategy_by_symbol = {row["symbol"]: row for row in strategy_rows}
    preferred: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for row in rec_rows:
        symbol = row.get("symbol")
        if not symbol:
            continue
        strategy = strategy_by_symbol.get(symbol)
        final_action = str(row.get("final_recommendation") or "").upper()
        strategy_action = str((strategy or {}).get("strategy_action") or "").upper()
        score, reasons, risks = _night_opportunity_score(row, strategy)
        entry = {"row": row, "strategy": strategy, "opportunity_score": score, "reasons": reasons, "risks": risks}
        if final_action in {"BUY", "WATCH"} or strategy_action in {"BUY", "WATCH"}:
            preferred.append(entry)
        else:
            rest.append(entry)
    preferred.sort(key=lambda item: (item["opportunity_score"], item["row"].get("final_score") or 0), reverse=True)
    rest.sort(key=lambda item: (item["opportunity_score"], item["row"].get("final_score") or 0), reverse=True)
    return (preferred + rest)[:max(min_entries, len(preferred))]


def combined_final_decision(row: dict[str, Any] | None, strategy: dict[str, Any] | None) -> str:
    if not row:
        return "UNAVAILABLE"
    final_action = str(row.get("final_recommendation") or "NEUTRAL").upper()
    strategy_action = str((strategy or {}).get("strategy_action") or "UNAVAILABLE").upper()
    data_quality = str((strategy or {}).get("data_quality") or "").upper()

    if final_action in {"SELL", "AVOID", "HIGH_RISK"}:
        return final_action
    if strategy_action == "UNAVAILABLE" or data_quality == "UNAVAILABLE":
        return "WATCH_DATA_MISSING" if final_action in {"BUY", "WATCH"} else final_action
    if strategy_action == "AVOID":
        return "WATCH_CONFLICT" if final_action == "BUY" else "AVOID"
    if final_action == "BUY" and strategy_action in {"BUY", "WATCH"}:
        return "BUY"
    if final_action == "WATCH" and strategy_action == "BUY":
        return "WATCH_FOR_BUY"
    if final_action == "WATCH" or strategy_action == "WATCH":
        return "WATCH"
    return final_action


def build_final_decision_report(db: Session, symbol: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    symbol = symbol.strip().upper().replace("EGX:", "")
    if not symbol:
        return f"Usage: /decision SYMBOL\nDisclaimer: {DISCLAIMER}"

    rec_warning = None
    try:
        rec_run = build_final_recommendations(db, settings=settings, limit=500)
        row = next((item for item in rec_run.rows if item.get("symbol") == symbol), None)
        rec_warning = rec_run.provider_warning
    except Exception as exc:
        row = None
        rec_warning = str(exc)

    try:
        strategy = run_strategy_for_symbol(db, symbol=symbol, settings=settings)
    except Exception as exc:
        strategy = {
            "symbol": symbol,
            "strategy_action": "UNAVAILABLE",
            "strategy_score": 0,
            "data_quality": "unavailable",
            "buy_timeframes": 0,
            "watch_timeframes": 0,
            "timeframes": [],
            "error": str(exc),
        }

    decision = combined_final_decision(row, strategy)
    chart_url = (row or {}).get("tradingview_chart_url") or tradingview_chart_url(symbol)
    lines = [
        f"EGX Final Decision: {symbol}",
        f"Decision: {decision}",
        "",
    ]
    if row:
        lines.extend(
            [
                f"Screener: {row.get('final_recommendation')} | score {_fmt_num(row.get('final_score'))}% | TV {row.get('tv_vote') or '-'}",
                f"Telegram: {row.get('telegram_vote') or '-'} | signals {row.get('telegram_signals') or 0} | buy {row.get('telegram_buy') or 0} | sell {row.get('telegram_sell') or 0}",
                f"Smart action: {row.get('smart_action_now') or '-'} | plan {row.get('smart_plan') or '-'}",
                f"Last price: {_fmt_num(row.get('last_price'), 2)} | RSI {_fmt_num(row.get('rsi'), 1)}",
                f"Buy zone: {row.get('smart_buy_zone') or '-'}",
                f"Entry: {_fmt_num(row.get('smart_suggested_entry'), 2)} | stop {_fmt_num(row.get('smart_suggested_stop'), 2)}",
                f"Targets: {_fmt_num(row.get('smart_target_scalp'), 2)} / {_fmt_num(row.get('smart_target_swing'), 2)} / {_fmt_num(row.get('smart_target_long'), 2)}",
            ]
        )
    else:
        lines.append("Screener: unavailable")

    lines.extend(
        [
            "",
            f"Strategy: {strategy.get('strategy_action')} | score {_fmt_num(strategy.get('strategy_score'))}% | data {strategy.get('data_quality') or '-'}",
            f"Frames: BUY {strategy.get('buy_timeframes', 0)} | WATCH {strategy.get('watch_timeframes', 0)} | reference {strategy.get('reference_provider') or '-'} {_fmt_num(strategy.get('reference_price'), 2)}",
        ]
    )
    for frame in strategy.get("timeframes", [])[:4]:
        lines.append(
            f"- {frame.get('timeframe')}: {frame.get('action')} {_fmt_num(frame.get('score'))}% | "
            f"last {_fmt_num(frame.get('last_price'), 2)} | diff {_fmt_num(frame.get('price_difference_percent'), 1)}% | {frame.get('data_quality')}"
        )

    if row:
        lines.extend(["", f"Why: {_compact_list(row.get('reasons'), limit=3)}"])
        risks = list(row.get("warnings") or [])
        if strategy.get("data_quality") == "unavailable":
            risks.append("Strategy candles unavailable.")
        lines.append(f"Risks: {_compact_list(risks, limit=3)}")
    if rec_warning:
        lines.extend(["", f"Data warning: {rec_warning}"])
    lines.extend(["", f"Chart: {chart_url}", f"Disclaimer: {DISCLAIMER}"])
    return _limit_message("\n".join(lines))


def build_daily_report(db: Session, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    now = datetime.utcnow()
    since = now - timedelta(days=1)

    active_sources = db.scalars(select(TelegramSource).where(TelegramSource.is_active.is_(True)).order_by(TelegramSource.username)).all()
    messages_24h = db.query(TelegramMessage).filter(TelegramMessage.created_at >= since).count()
    signals_24h = db.query(ExtractedSignal).filter(ExtractedSignal.created_at >= since).count()
    analyses_24h = db.query(FinalAnalysis).filter(FinalAnalysis.created_at >= since).count()

    latest = db.scalars(select(FinalAnalysis).order_by(FinalAnalysis.created_at.desc()).limit(50)).all()
    buy_count = sum(1 for item in latest if item.final_decision == "BUY")
    watch_count = sum(1 for item in latest if item.final_decision == "WATCH")
    high_risk_count = sum(1 for item in latest if item.final_decision == "HIGH_RISK")
    avg_conf = sum(item.confidence_score for item in latest) / len(latest) if latest else 0.0

    try:
        rec_run = build_final_recommendations(db, settings=settings, limit=500)
        rec_rows = rec_run.rows
    except Exception as exc:
        rec_rows = []
        rec_warning = str(exc)
    else:
        rec_warning = None

    top_n = settings.daily_report_top_n
    buy_rows = [row for row in rec_rows if row.get("final_recommendation") == "BUY"][:top_n]
    watch_rows = [row for row in rec_rows if row.get("final_recommendation") == "WATCH"][:top_n]

    strategy_rows: list[dict[str, Any]] = []
    if settings.daily_report_include_strategy:
        try:
            strategy_rows = run_strategy_universe(db, settings=settings, limit=min(top_n, settings.strategy_symbol_limit))["rows"]
        except Exception as exc:
            strategy_rows = [{"symbol": "N/A", "strategy_action": "UNAVAILABLE", "strategy_score": 0, "error": str(exc)}]

    lines = [
        "Daily EGX Automatic Report",
        f"Generated: {now:%Y-%m-%d %H:%M} UTC",
        "",
        "Automation",
        f"Active channels: {len(active_sources)}",
        f"Messages 24h: {messages_24h}",
        f"Signals 24h: {signals_24h}",
        f"Analyses 24h: {analyses_24h}",
        "",
        "Recent analysis mix",
        f"BUY: {buy_count} | WATCH: {watch_count} | HIGH_RISK: {high_risk_count} | Avg confidence: {avg_conf:.0f}%",
    ]

    if active_sources:
        names = ", ".join((source.title or source.username) for source in active_sources[:8])
        suffix = "..." if len(active_sources) > 8 else ""
        lines.extend(["", f"Channels: {names}{suffix}"])

    lines.append("")
    lines.append("Top BUY recommendations")
    if buy_rows:
        for idx, row in enumerate(buy_rows, start=1):
            lines.append(
                f"{idx}. {row['symbol']} {row.get('name') or ''} | score {_fmt_num(row.get('final_score'))}% | "
                f"TV {row.get('tv_vote')} | TG {row.get('telegram_vote')} ({row.get('telegram_signals', 0)}) | "
                f"{row.get('smart_action_now')}"
            )
    else:
        lines.append("No BUY recommendations right now.")

    lines.append("")
    lines.append("Watchlist")
    if watch_rows:
        for idx, row in enumerate(watch_rows[:5], start=1):
            lines.append(f"{idx}. {row['symbol']} | score {_fmt_num(row.get('final_score'))}% | {row.get('smart_action_now')}")
    else:
        lines.append("No WATCH recommendations right now.")

    if settings.daily_report_include_strategy:
        lines.append("")
        lines.append("Multi-timeframe strategy")
        strategy_pick = [row for row in strategy_rows if row.get("strategy_action") in {"BUY", "WATCH"}][:5]
        if strategy_pick:
            for idx, row in enumerate(strategy_pick, start=1):
                mock_note = " mock" if row.get("uses_mock_data") else ""
                lines.append(
                    f"{idx}. {row['symbol']} | {row.get('strategy_action')} | score {_fmt_num(row.get('strategy_score'))}% | "
                    f"BUY frames {row.get('buy_timeframes', 0)} | WATCH frames {row.get('watch_timeframes', 0)}{mock_note}"
                )
        else:
            lines.append("No multi-timeframe BUY/WATCH alignment right now.")

    if rec_warning:
        lines.extend(["", f"Market data warning: {rec_warning}"])

    lines.extend(["", f"Disclaimer: {DISCLAIMER}"])
    message = "\n".join(lines)
    return _limit_message(message)


def build_stock_brief(db: Session, symbol: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    symbol = symbol.strip().upper()
    if not symbol:
        return f"Usage: /stock SYMBOL\nDisclaimer: {DISCLAIMER}"

    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    try:
        rec_run = build_final_recommendations(db, settings=settings, limit=500)
        rec_row = next((row for row in rec_run.rows if row.get("symbol") == symbol), None)
        rec_warning = rec_run.provider_warning
    except Exception as exc:
        rec_row = None
        rec_warning = str(exc)

    try:
        strategy = run_strategy_for_symbol(db, symbol=symbol, settings=settings)
    except Exception as exc:
        strategy = {
            "symbol": symbol,
            "strategy_action": "UNAVAILABLE",
            "strategy_score": 0,
            "buy_timeframes": 0,
            "watch_timeframes": 0,
            "uses_mock_data": False,
            "timeframes": [],
            "error": str(exc),
        }

    signals = db.scalars(
        select(ExtractedSignal)
        .options(joinedload(ExtractedSignal.source))
        .where(ExtractedSignal.stock_symbol == symbol)
        .order_by(ExtractedSignal.created_at.desc())
        .limit(5)
    ).all()
    latest_analyses = db.scalars(
        select(FinalAnalysis)
        .where(FinalAnalysis.symbol == symbol)
        .order_by(FinalAnalysis.created_at.desc())
        .limit(3)
    ).all()

    title_name = (rec_row or {}).get("name") or (stock.name_en if stock else None) or symbol
    sector = (rec_row or {}).get("sector") or (stock.sector if stock else None) or "-"
    now = _local_now(settings)
    chart_url = (rec_row or {}).get("tradingview_chart_url") or tradingview_chart_url(symbol)

    lines = [
        f"EGX Stock Brief: {symbol}",
        f"Name: {title_name}",
        f"Sector: {sector}",
        f"Generated: {now:%Y-%m-%d %H:%M} {settings.timezone}",
        "",
    ]

    if rec_row:
        lines.extend(
            [
                "Final recommendation",
                f"Decision: {rec_row.get('final_recommendation')} | score {_fmt_num(rec_row.get('final_score'))}%",
                f"Smart action: {rec_row.get('smart_action_now') or '-'} | plan {rec_row.get('smart_plan') or '-'}",
                f"Trend: {rec_row.get('smart_main_trend') or '-'} | pressure {rec_row.get('smart_pressure') or '-'} | volume {rec_row.get('smart_volume_status') or '-'}",
                f"Last price: {_fmt_num(rec_row.get('last_price'), 2)} | change {_fmt_num(rec_row.get('change_percent'), 2)}% | RSI {_fmt_num(rec_row.get('rsi'), 1)}",
                f"TradingView: {rec_row.get('tv_vote') or '-'} | Telegram: {rec_row.get('telegram_vote') or '-'} ({rec_row.get('telegram_signals') or 0} signals, {rec_row.get('telegram_buy') or 0} buy)",
                f"Buy zone: {rec_row.get('smart_buy_zone') or '-'}",
                f"Entry: {_fmt_num(rec_row.get('smart_suggested_entry'), 2)} | stop {_fmt_num(rec_row.get('smart_suggested_stop'), 2)}",
                f"Targets: {_fmt_num(rec_row.get('smart_target_scalp'), 2)} / {_fmt_num(rec_row.get('smart_target_swing'), 2)} / {_fmt_num(rec_row.get('smart_target_long'), 2)}",
                f"Why: {_compact_list(rec_row.get('reasons'))}",
                f"Risks: {_compact_list(rec_row.get('warnings'))}",
            ]
        )
    else:
        lines.extend(
            [
                "Final recommendation",
                "No screener recommendation is available for this symbol yet.",
            ]
        )

    lines.extend(
        [
            "",
            "Strategy backtest",
            f"Action: {strategy.get('strategy_action')} | score {_fmt_num(strategy.get('strategy_score'))}% | BUY frames {strategy.get('buy_timeframes', 0)} | WATCH frames {strategy.get('watch_timeframes', 0)}",
            f"Data quality: {strategy.get('data_quality') or '-'} | reference {strategy.get('reference_provider') or '-'} {_fmt_num(strategy.get('reference_price'), 2)}",
        ]
    )
    for frame in strategy.get("timeframes", [])[:4]:
        note = f" {frame.get('data_quality')}" if frame.get("data_quality") else ""
        lines.append(
            f"- {frame.get('timeframe')}: {frame.get('action')} {_fmt_num(frame.get('score'))}% | {frame.get('trend')} | "
            f"last {_fmt_num(frame.get('last_price'), 2)} ref {_fmt_num(frame.get('reference_price'), 2)} diff {_fmt_num(frame.get('price_difference_percent'), 1)}% | "
            f"entry {_fmt_num(frame.get('entry'), 2)} stop {_fmt_num(frame.get('stop'), 2)} target {_fmt_num(frame.get('target'), 2)} | "
            f"WR {_fmt_num(frame.get('win_rate'), 0)}% trades {frame.get('trades', 0)}{note}"
        )

    lines.extend(["", "Latest Telegram signals"])
    if signals:
        for signal in signals:
            targets = _first(signal.targets)
            lines.append(
                f"- {signal.created_at:%Y-%m-%d} | {_signal_channel(signal)} | {signal.direction or '-'} | "
                f"entry {_fmt_num(signal.entry_price, 2)} stop {_fmt_num(signal.stop_loss, 2)} targets {targets} | {signal.timeframe or '-'}"
            )
    else:
        lines.append("No Telegram signal found for this stock yet.")

    lines.extend(["", "Recent internal analyses"])
    if latest_analyses:
        for item in latest_analyses:
            lines.append(
                f"- {item.created_at:%Y-%m-%d %H:%M} | {item.final_decision} | confidence {_fmt_num(item.confidence_score)}% | trend {item.trend or '-'}"
            )
    else:
        lines.append("No internal analysis rows yet.")

    if rec_warning:
        lines.extend(["", f"Market data warning: {rec_warning}"])

    lines.extend(["", f"Chart: {chart_url}", "", f"Disclaimer: {DISCLAIMER}"])
    return _limit_message("\n".join(lines))


def build_night_opportunity_report(db: Session, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    now = _local_now(settings)
    rec_warning = None
    try:
        rec_run = build_final_recommendations(db, settings=settings, limit=500)
        rec_rows = rec_run.rows
        rec_warning = rec_run.provider_warning
        provider_status = rec_run.provider_status
    except Exception as exc:
        rec_rows = []
        provider_status = "unavailable"
        rec_warning = str(exc)

    top_n = max(1, settings.night_opportunity_top_n)
    top_preferred = [
        row
        for row in rec_rows
        if row.get("final_recommendation") in {"BUY", "WATCH"}
        or row.get("telegram_vote") in {"POSITIVE", "MIXED"}
        or "BUY" in str(row.get("smart_action_now") or "")
    ]
    candidates = top_preferred[:max(top_n, 5)] if len(top_preferred) >= max(top_n, 5) else rec_rows[: max(settings.strategy_symbol_limit, top_n, 5)]

    candidate_symbols = []
    for row in candidates:
        symbol = str(row.get("symbol") or "").upper()
        if symbol and symbol not in candidate_symbols:
            candidate_symbols.append(symbol)
    candidate_symbols = candidate_symbols[: max(settings.strategy_symbol_limit, top_n)]

    try:
        if candidate_symbols:
            strategy_rows = run_strategy_universe(
                db,
                settings=settings,
                limit=len(candidate_symbols),
                symbols=candidate_symbols,
            )["rows"]
        else:
            strategy_rows = run_strategy_universe(db, settings=settings, limit=settings.strategy_symbol_limit)["rows"]
    except Exception as exc:
        strategy_rows = []
        rec_warning = f"{rec_warning}; strategy warning: {exc}" if rec_warning else f"Strategy warning: {exc}"

    ranked = _rank_night_opportunities(rec_rows, strategy_rows)
    lines = [
        "Night EGX Opportunity Report",
        "For next trading session watchlist",
        f"Generated: {now:%Y-%m-%d %H:%M} {settings.timezone}",
        f"Sources: TradingView screener status {provider_status}, Telegram channels, multi-timeframe strategy",
        "",
        "Top opportunities",
    ]

    if ranked:
        for idx, item in enumerate(ranked[:top_n], start=1):
            row = item["row"]
            strategy = item.get("strategy") or {}
            rec = str(row.get("final_recommendation") or "NEUTRAL").upper()
            action = "BUY CANDIDATE" if item["opportunity_score"] >= 78 else ("WATCH FOR ENTRY" if rec in {"BUY", "WATCH"} else rec)
            quality_note = f" | {strategy.get('data_quality')}" if strategy.get("data_quality") else ""
            lines.extend(
                [
                    f"{idx}. {row.get('symbol')} - {action} | opportunity {_fmt_num(item['opportunity_score'])}%",
                    f"   Final: {row.get('final_recommendation')} {_fmt_num(row.get('final_score'))}% | Smart: {row.get('smart_action_now') or '-'}",
                    f"   Strategy: {strategy.get('strategy_action', 'N/A')} {_fmt_num(strategy.get('strategy_score'))}% | BUY {strategy.get('buy_timeframes', 0)} / WATCH {strategy.get('watch_timeframes', 0)}{quality_note}",
                    f"   Telegram: {row.get('telegram_vote') or '-'} | signals {row.get('telegram_signals') or 0} | buy {row.get('telegram_buy') or 0} | sell {row.get('telegram_sell') or 0}",
                    f"   Plan: zone {row.get('smart_buy_zone') or '-'} | entry {_fmt_num(row.get('smart_suggested_entry'), 2)} | stop {_fmt_num(row.get('smart_suggested_stop'), 2)}",
                    f"   Targets: {_fmt_num(row.get('smart_target_scalp'), 2)} / {_fmt_num(row.get('smart_target_swing'), 2)} / {_fmt_num(row.get('smart_target_long'), 2)}",
                    f"   Why: {_compact_list(item.get('reasons'), limit=3)}",
                    f"   Risks: {_compact_list(item.get('risks'), limit=3)}",
                ]
            )
    else:
        lines.append("No next-session BUY/WATCH opportunity has enough confirmation right now.")

    if rec_warning:
        lines.extend(["", f"Data warning: {rec_warning}"])

    lines.extend(
        [
            "",
            "Rule: buy only near entry zone after market confirmation; use stop loss.",
            f"Disclaimer: {DISCLAIMER}",
        ]
    )
    return _limit_message("\n".join(lines))


def send_daily_report(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    from app.database import SessionLocal
    from app.services.telegram_bot import send_private_message_sync

    with SessionLocal() as db:
        message = build_daily_report(db, settings=settings)
    send_private_message_sync(message, settings=settings)
    return {"sent": True, "length": len(message)}


def send_night_opportunity_report(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    from app.database import SessionLocal
    from app.services.telegram_bot import send_private_message_sync

    with SessionLocal() as db:
        message = build_night_opportunity_report(db, settings=settings)
    send_private_message_sync(message, settings=settings)
    return {"sent": True, "length": len(message)}


def build_afternoon_report(db: Session, settings: Settings | None = None) -> str:
    """Build the 3 PM Cairo daily report with morning review and pre-trade stages."""
    settings = settings or get_settings()
    now = _local_now(settings)
    from app.services.morning_review import review_morning_recommendations, format_review_for_telegram, analyze_system_mistakes
    from app.services.pre_trade_validator import (
        RecommendationStage, analyze_market_condition, pre_trade_validate,
    )

    review = review_morning_recommendations(db, settings)
    mistakes = analyze_system_mistakes(review)

    market = analyze_market_condition(db)

    try:
        rec_run = build_final_recommendations(db, settings=settings, limit=500)
        rec_rows = rec_run.rows
        rec_warning = rec_run.provider_warning
    except Exception as exc:
        rec_rows = []
        rec_warning = str(exc)

    # Classify each row through pre-trade validation
    classified = []
    for r in rec_rows:
        try:
            pv = pre_trade_validate(db, r.get("symbol", ""), r)
            classified.append((r, pv))
        except Exception:
            classified.append((r, None))

    actionable = [(r, pv) for r, pv in classified if pv and pv.passed and pv.stage in (RecommendationStage.ENTRY_CONFIRMED, RecommendationStage.BUY, RecommendationStage.STRONG_BUY)]
    watchlist = [(r, pv) for r, pv in classified if pv and pv.stage in (RecommendationStage.WATCH, RecommendationStage.NEAR_ENTRY) and pv.passed]
    avoid = [(r, pv) for r, pv in classified if not pv or not pv.passed or pv.stage == RecommendationStage.AVOID]

    lines = [
        "EGX Afternoon Report (3 PM Cairo)",
        f"Generated: {now:%Y-%m-%d %H:%M} {settings.timezone}",
        "",
    ]

    # Market condition
    lines.append("=== MARKET CONDITION ===")
    lines.append(f"Regime: {market.get('regime', 'unknown').upper()}")
    lines.append(f"Trend: {market.get('trend_score', 50):.0f}/100 | Market score: {market.get('market_score', 50):.0f}/100")
    if market.get("volatility_score"):
        lines.append(f"Volatility: {market.get('volatility_score'):.0f}/100")
    lines.append(f"Analysis: {market.get('reason', '')[:120]}")
    lines.append("")

    lines.append("=== MORNING REVIEW ===")
    if review.get("found"):
        summary = review.get("summary", {})
        lines.extend([
            f"Wins: {summary.get('wins', 0)} | Losses: {summary.get('losses', 0)}",
            f"Win Rate: {summary.get('win_rate_pct', 0)}%",
            f"Total P&L: {summary.get('total_profit_loss_pct', 0)}%",
        ])
        for rv in review.get("reviews", []):
            pl = rv.get("profit_loss_pct_display", rv.get("profit_loss_pct", "-"))
            lines.append(f"  {rv['symbol']} ({rv['signal']}): Entry {rv['entry_zone']}, P&L {pl}%")
    else:
        lines.append("No morning recommendations for today.")

    lines.extend(["", "=== SYSTEM MISTAKES ==="])
    for mistake in mistakes:
        lines.append(f"- {mistake}")

    lines.extend(["", "=== TOP ACTIONABLE TRADES ==="])
    if actionable:
        for idx, (r, pv) in enumerate(actionable[:5], 1):
            lines.append(f"{idx}. {r['symbol']} | {pv.stage.value} | Score {_fmt_num(r.get('final_score'))}%")
    else:
        lines.append("No actionable trades right now.")

    lines.extend(["", "=== WATCHLIST ==="])
    if watchlist:
        for idx, (r, pv) in enumerate(watchlist[:5], 1):
            stage_label = pv.stage.value if pv else "?"
            lines.append(f"{idx}. {r['symbol']} | {stage_label} | Score {_fmt_num(r.get('final_score'))}%")
    else:
        lines.append("No stocks on watchlist.")

    lines.extend(["", "=== AVOID LIST ==="])
    if avoid:
        for idx, (r, pv) in enumerate(avoid[:5], 1):
            reason = pv.reasons[0] if pv and pv.reasons else "Low score"
            lines.append(f"{idx}. {r['symbol']} | Score {_fmt_num(r.get('final_score'))}% | {reason}")
    else:
        lines.append("No stocks on avoid list.")

    # Trades that hit target or stop loss from morning review
    lines.extend(["", "=== TARGETS & STOP LOSSES HIT ==="])
    if review.get("found"):
        hit_target = [rv for rv in review.get("reviews", []) if rv.get("t1_hit") or rv.get("t2_hit")]
        hit_sl = [rv for rv in review.get("reviews", []) if rv.get("sl_hit")]
        if hit_target:
            lines.append("Targets hit:")
            for rv in hit_target:
                lines.append(f"  {rv['symbol']}: T1={rv.get('t1_hit')} T2={rv.get('t2_hit')}")
        if hit_sl:
            lines.append("Stop losses hit:")
            for rv in hit_sl:
                lines.append(f"  {rv['symbol']}: SL triggered")
        if not hit_target and not hit_sl:
            lines.append("No targets or stop losses hit yet.")
    else:
        lines.append("No review data available.")

    # Mistakes and lessons from trade_journal
    lines.extend(["", "=== MISTAKES & LESSONS ==="])
    from app.models import TradeJournal
    recent_journal = db.scalars(
        select(TradeJournal).where(
            TradeJournal.date >= _local_now(settings).replace(hour=0, minute=0, second=0, microsecond=0)
        ).order_by(TradeJournal.date.desc()).limit(5)
    ).all()
    if recent_journal:
        for tj in recent_journal:
            if tj.mistake_type:
                lines.append(f"  {tj.symbol}: {tj.mistake_type} - {tj.lesson_learned or 'No lesson'}")
    else:
        lines.append("No mistakes recorded today.")

    # Best and worst signal sources
    lines.extend(["", "=== BEST / WORST SIGNAL SOURCES ==="])
    try:
        from app.services.morning_review import best_worst_signal_sources
        bws = best_worst_signal_sources(db)
        lines.append(f"Best: {bws.get('best', 'N/A')}")
        lines.append(f"Worst: {bws.get('worst', 'N/A')}")
    except Exception:
        lines.append("Signal source analysis not available.")

    if rec_warning:
        lines.extend(["", f"Data quality: {rec_warning}"])

    lines.extend(["", f"Disclaimer: {DISCLAIMER}"])
    return _limit_message("\n".join(lines))


def send_afternoon_report(settings: Settings | None = None) -> dict[str, Any]:
    """Send the 3 PM Cairo afternoon report to Telegram."""
    settings = settings or get_settings()
    from app.database import SessionLocal
    from app.services.telegram_bot import send_private_message_sync

    with SessionLocal() as db:
        message = build_afternoon_report(db, settings=settings)
        # Also generate the daily file report
        try:
            from app.services.daily_file_report import build_daily_file_report
            build_daily_file_report(db, settings)
        except Exception as exc:
            pass
    send_private_message_sync(message, settings=settings)
    return {"sent": True, "length": len(message)}
    return {"sent": True, "length": len(message)}
