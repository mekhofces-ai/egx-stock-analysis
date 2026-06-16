from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DISCLAIMER, RISK_NOTE, Settings, get_settings
from app.database import SessionLocal, init_db, sqlite_write_lock
from app.models import Opportunity, StrategyCliV6Result, TradingViewScreeningResult, TradingViewScreeningRun
from app.services.stock_alert_service import send_stock_alert
from app.services.backtest_cli_v6 import get_latest_cli_v6_backtest_summary
from app.services.backtest_engine import get_latest_backtest_summary
from app.services.reports import combined_final_decision
from app.services.screener_recommendations import build_final_recommendations
from app.services.strategy import run_strategy_for_symbol
from app.services.backtest_queue import add_opportunities_to_queue
from app.services.daily_egx_report import latest_report_component
from app.services.recommendation_validation import CONDITIONAL_BUY, validate_recommendation
from app.services.stock_analysis_engine import build_combined_analysis
from app.services.strategies.cli_v6_egx import (
    STRATEGY_NAME as CLI_V6_STRATEGY_NAME,
    latest_cli_v6_result,
    recommendation_to_score,
    run_cli_v6_for_symbol,
)
from app.services.market_daily_evaluation import evaluate_daily_market
from app.services.tradingview_screener import run_tradingview_screening


logger = logging.getLogger(__name__)

CAIRO_TZ = ZoneInfo("Africa/Cairo")

DEFAULT_COOLDOWN_MINUTES = 120


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _bound(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _weights(settings: Settings) -> dict[str, float]:
    return settings.cli_v6_opportunity_weights


def _strategy_component(strategy: dict[str, Any] | None) -> float | None:
    if not strategy or strategy.get("strategy_action") == "UNAVAILABLE":
        return None
    score = _num(strategy.get("strategy_score"), 0.0) or 0.0
    action = str(strategy.get("strategy_action") or "").upper()
    if action == "BUY":
        score += 12
    elif action == "WATCH":
        score += 5
    elif action == "AVOID":
        score -= 18
    return _bound(score)


def _backtest_component(backtests: list[dict[str, Any]]) -> float | None:
    usable = [row for row in backtests if row.get("recommendation") != "UNAVAILABLE"]
    if not usable:
        return None
    best = max(usable, key=lambda row: row.get("score") or 0)
    score = _num(best.get("score"), 0.0) or 0.0
    total_return = best.get("total_return")
    if total_return is None:
        total_return = best.get("total_return_pct")
    max_drawdown = best.get("max_drawdown")
    if max_drawdown is None:
        max_drawdown = best.get("max_drawdown_pct")
    if (total_return or 0) > 0:
        score += 4
    if (max_drawdown or 0) > 18:
        score -= 8
    return _bound(score)


def _cli_v6_component(cli_result: dict[str, Any] | None) -> float | None:
    if not cli_result:
        return None
    return recommendation_to_score(cli_result.get("recommendation"), cli_result.get("confidence"))


def _latest_screening_row(db: Session, symbol: str) -> TradingViewScreeningResult | None:
    run = db.scalar(select(TradingViewScreeningRun).order_by(TradingViewScreeningRun.created_at.desc()))
    if not run:
        return None
    return db.scalar(
        select(TradingViewScreeningResult).where(
            TradingViewScreeningResult.run_id == run.id,
            TradingViewScreeningResult.symbol == symbol,
        )
    )


def _freshness_score(db: Session) -> float:
    run = db.scalar(select(TradingViewScreeningRun).order_by(TradingViewScreeningRun.created_at.desc()))
    if not run:
        return 35.0
    age_hours = max(0.0, (datetime.utcnow() - (run.completed_at or run.created_at)).total_seconds() / 3600)
    if age_hours <= 8:
        return 100.0
    if age_hours <= 24:
        return 80.0
    if age_hours <= 72:
        return 55.0
    return 30.0


def _market_context(db: Session) -> dict[str, Any]:
    cached = db.info.get("opportunity_market_context")
    if isinstance(cached, dict):
        return cached
    try:
        payload = evaluate_daily_market(db, persist=True)
    except Exception as exc:
        logger.info("Market context unavailable for opportunity scoring: %s", exc)
        payload = {
            "market_score": None,
            "market_regime": "DATA_INSUFFICIENT",
            "trade_permission": "DATA_INSUFFICIENT",
            "explanation": "Market daily evaluation unavailable.",
        }
    db.info["opportunity_market_context"] = payload
    return payload


def _apply_market_permission(
    recommendation: str,
    final_score: float,
    market: dict[str, Any],
) -> tuple[str, float, str | None]:
    permission = str(market.get("trade_permission") or "DATA_INSUFFICIENT").upper()
    if recommendation != "BUY" or permission == "TRADE_ALLOWED":
        return recommendation, final_score, None
    if permission == "WATCH_ONLY":
        return (
            "WATCH",
            min(final_score, 74.99),
            "Market permission is WATCH_ONLY, so BUY was downgraded to WATCH.",
        )
    if permission in {"BUY_BLOCKED", "SELL_ONLY", "NO_TRADING", "DATA_INSUFFICIENT"}:
        return (
            "WATCH",
            min(final_score, 64.99),
            f"Market permission is {permission}, so BUY was blocked and downgraded to WATCH.",
        )
    return recommendation, final_score, None


def _risk_score(
    row: dict[str, Any],
    strategy: dict[str, Any] | None,
    backtests: list[dict[str, Any]],
    cli_result: dict[str, Any] | None = None,
) -> tuple[float, list[str]]:
    score = 100.0
    risks: list[str] = []
    warning_text = " ".join(str(item) for item in (row.get("warnings") or [])).lower()
    if "overbought" in warning_text:
        score -= 14
        risks.append("RSI/chase risk")
    if "hype" in warning_text:
        score -= 9
        risks.append("Telegram hype risk")
    if "missing stop" in warning_text or int(row.get("missing_stop") or 0):
        score -= 8
        risks.append("Some Telegram signals missed stop loss")
    if strategy and strategy.get("data_quality") == "unavailable":
        score -= 15
        risks.append("Strategy candles unavailable")
    cli_recommendation = str((cli_result or {}).get("recommendation") or "").upper()
    if cli_recommendation == "STRONG SELL":
        score -= 25
        risks.append("CLI v6 shows STRONG SELL risk")
    elif cli_recommendation == "WEAK SELL":
        score -= 12
        risks.append("CLI v6 shows weak sell pressure")
    usable = [item for item in backtests if item.get("recommendation") != "UNAVAILABLE"]
    if usable:
        worst_drawdown = max(_num(item.get("max_drawdown") if item.get("max_drawdown") is not None else item.get("max_drawdown_pct"), 0.0) or 0.0 for item in usable)
        if worst_drawdown >= 20:
            score -= 12
            risks.append("Reviewed backtest drawdown is high")
    return _bound(score), risks[:4]


def calculate_opportunity(
    db: Session,
    symbol: str,
    row: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    symbol = symbol.upper().replace("EGX:", "").strip()
    if row is None:
        rec_run = build_final_recommendations(db, settings=settings, limit=500)
        row = next((item for item in rec_run.rows if item.get("symbol") == symbol), None)
    if row is None:
        raise ValueError(f"No recent analysis found for {symbol}. Please run the analysis/update process first.")

    strategy = {
        "strategy_action": "UNAVAILABLE",
        "strategy_score": 0.0,
        "data_quality": "not_used",
        "note": "Legacy strategy is not rerun during CLI v6 opportunity scoring.",
    }
    try:
        cli_result = latest_cli_v6_result(db, symbol) or run_cli_v6_for_symbol(db, symbol=symbol, settings=settings)
    except Exception as exc:
        logger.exception("CLI v6 component failed for %s", symbol)
        cli_result = {"recommendation": "INSUFFICIENT DATA", "confidence": 0.0, "reason": str(exc)}
    cli_backtests = get_latest_cli_v6_backtest_summary(db, symbol=symbol, limit=10)
    legacy_backtests = get_latest_backtest_summary(db, symbol=symbol, limit=10)
    backtests = cli_backtests or legacy_backtests
    screening = _latest_screening_row(db, symbol)

    components: dict[str, float | None] = {
        "system_recommendation": _num(row.get("final_score")),
        "cli_v6_strategy": _cli_v6_component(cli_result),
        "backtest": _backtest_component(backtests),
        "tradingview": _num(screening.final_score if screening else row.get("tv_score") or row.get("final_score")),
        "telegram": _num(row.get("telegram_score")),
    }
    weights = _weights(settings)
    weighted_total = 0.0
    active_weight = 0.0
    for key, value in components.items():
        if value is None:
            continue
        weight = weights.get(key, 0.0)
        weighted_total += value * weight
        active_weight += weight
    base_score = weighted_total / active_weight if active_weight else 0.0
    risk_score, risks = _risk_score(row, strategy, backtests, cli_result=cli_result)
    freshness_score = _freshness_score(db)
    final_score = _bound(base_score * 0.88 + risk_score * 0.07 + freshness_score * 0.05)
    combined_payload = None
    daily_report_score, daily_report_details = latest_report_component(db, symbol)
    try:
        combined_payload = build_combined_analysis(db, symbol, settings=settings, run_missing=False, persist=True)
    except Exception as exc:
        logger.info("Combined analysis component skipped for %s: %s", symbol, exc)
        combined_payload = None
    if combined_payload and combined_payload.get("final_score") is not None:
        final_score = _bound(final_score * 0.6 + float(combined_payload.get("final_score") or 0) * 0.4)
    combined_decision = combined_final_decision(row, strategy)
    cli_recommendation = str((cli_result or {}).get("recommendation") or "").upper()
    system_action = str(row.get("final_recommendation") or "NEUTRAL").upper()
    if cli_recommendation == "STRONG SELL":
        recommendation = "AVOID"
    elif final_score >= 78 and cli_recommendation in {"STRONG BUY", "WEAK BUY"} and system_action in {"BUY", "WATCH"} and risk_score >= 65:
        recommendation = "BUY"
    elif final_score >= 62 and system_action not in {"SELL", "AVOID", "HIGH_RISK"} and cli_recommendation != "STRONG SELL":
        recommendation = "WATCH"
    elif final_score < 40 or combined_decision in {"SELL", "AVOID", "HIGH_RISK"}:
        recommendation = "AVOID"
    else:
        recommendation = "NEUTRAL"

    market_context = _market_context(db)
    recommendation, final_score, market_adjustment = _apply_market_permission(
        recommendation,
        final_score,
        market_context,
    )

    reasons = []
    if market_adjustment:
        reasons.append(market_adjustment)
    if row.get("final_recommendation"):
        reasons.append(f"System recommendation is {row.get('final_recommendation')} with score {row.get('final_score') or 0:.0f}%.")
    if strategy.get("strategy_action") not in {None, "UNAVAILABLE"}:
        reasons.append(f"Strategy is {strategy.get('strategy_action')} with score {strategy.get('strategy_score') or 0:.0f}%.")
    if cli_result and cli_result.get("recommendation") != "INSUFFICIENT DATA":
        reasons.append(f"CLI v6 is {cli_result.get('recommendation')} with confidence {cli_result.get('confidence') or 0:.0f}%.")
    if backtests:
        best = max(backtests, key=lambda item: item.get("score") or 0)
        reasons.append(f"Best reviewed backtest is {best.get('recommendation')} on {best.get('timeframe')} with score {best.get('score') or 0:.0f}%.")
    if row.get("telegram_vote") and row.get("telegram_vote") != "NONE":
        reasons.append(f"Telegram consensus is {row.get('telegram_vote')} from {row.get('telegram_signals') or 0} signal(s).")
    if daily_report_score is not None:
        reasons.append(
            f"Daily Excel report is {daily_report_details.get('recommendation') or '-'} with score {daily_report_score:.0f}%."
        )
    if not reasons:
        reasons.append("Available sources do not show enough confirmation yet.")

    target = row.get("smart_target_swing") or row.get("smart_target_scalp") or row.get("smart_target_long")
    if not target:
        target = daily_report_details.get("target1") or daily_report_details.get("target2")
    entry_price = _num(row.get("smart_suggested_entry"))
    if entry_price is None:
        entry_price = _num(daily_report_details.get("buy_price"))
    stop_loss = _num(row.get("smart_suggested_stop"))
    if stop_loss is None:
        stop_loss = _num(daily_report_details.get("stop_loss"))
    payload = {
        "symbol": symbol,
        "final_score": final_score,
        "recommendation": recommendation,
        "confidence": final_score,
        "entry_price": entry_price,
        "target_price": _num(target),
        "stop_loss": stop_loss,
        "reason": " ".join(reasons[:4]),
        "components_json": _json_safe({
            "components": components,
            "weights": weights,
            "base_score": round(base_score, 2),
            "risk_score": risk_score,
            "freshness_score": freshness_score,
            "risks": risks,
            "combined_decision": combined_decision,
            "cli_v6_strategy": {
                "recommendation": (cli_result or {}).get("recommendation"),
                "confidence": (cli_result or {}).get("confidence"),
                "bullish_count": (cli_result or {}).get("bullish_count"),
                "bearish_count": (cli_result or {}).get("bearish_count"),
                "neutral_count": (cli_result or {}).get("neutral_count"),
                "timeframes": (cli_result or {}).get("timeframes"),
                "reason": (cli_result or {}).get("reason"),
                "run_id": (cli_result or {}).get("run_id"),
            },
            "strategy": {
                "action": strategy.get("strategy_action"),
                "score": strategy.get("strategy_score"),
                "data_quality": strategy.get("data_quality"),
            },
            "backtests": backtests[:4],
            "screener": {
                "recommendation": row.get("final_recommendation"),
                "score": row.get("final_score"),
                "tv_vote": row.get("tv_vote"),
                "telegram_vote": row.get("telegram_vote"),
                "last_price": row.get("last_price"),
                "buy_zone": row.get("smart_buy_zone"),
                "warnings": row.get("warnings") or [],
            },
            "daily_report": daily_report_details,
            "stock_combined_analysis": combined_payload,
            "market_daily_evaluation": {
                "market_score": market_context.get("market_score"),
                "market_regime": market_context.get("market_regime"),
                "trade_permission": market_context.get("trade_permission"),
                "explanation": market_context.get("explanation"),
                "trade_allowed": market_context.get("trade_permission") == "TRADE_ALLOWED" and recommendation == "BUY",
            },
        }),
    }
    return payload


def refresh_opportunities(
    db: Session | None = None,
    settings: Settings | None = None,
    limit: int = 100,
    run_screening: bool = True,
) -> dict[str, Any]:
    settings = settings or get_settings()

    def _run(active_db: Session) -> dict[str, Any]:
        logger.info("Opportunity refresh started.")
        rows: list[dict[str, Any]]
        provider_status = None
        provider_warning = None
        if run_screening:
            screening = run_tradingview_screening(active_db, settings=settings, limit=500)
            rows = screening.get("rows", [])
            provider_status = screening.get("provider_status")
            provider_warning = screening.get("provider_warning")
        else:
            rec_run = build_final_recommendations(active_db, settings=settings, limit=max(limit, settings.strategy_symbol_limit))
            rows = rec_run.rows
            provider_status = rec_run.provider_status
            provider_warning = rec_run.provider_warning

        saved: list[dict[str, Any]] = []
        for row in rows[:limit]:
            symbol = str(row.get("symbol") or "").upper().replace("EGX:", "")
            if not symbol:
                continue
            try:
                payload = calculate_opportunity(active_db, symbol=symbol, row=row, settings=settings)
                with sqlite_write_lock():
                    existing = active_db.scalar(select(Opportunity).where(Opportunity.symbol == symbol))
                    if existing:
                        existing.final_score = payload["final_score"]
                        existing.recommendation = payload["recommendation"]
                        existing.confidence = payload["confidence"]
                        existing.entry_price = payload["entry_price"]
                        existing.target_price = payload["target_price"]
                        existing.stop_loss = payload["stop_loss"]
                        existing.reason = payload["reason"]
                        existing.components_json = payload["components_json"]
                        existing.source = "combined_cli_v6"
                        existing.updated_at = datetime.utcnow()
                        opportunity_id = existing.id
                    else:
                        opportunity = Opportunity(**payload, source="combined_cli_v6")
                        active_db.add(opportunity)
                        active_db.flush()
                        opportunity_id = opportunity.id
                    active_db.commit()
                saved.append({"id": opportunity_id, **payload})
            except Exception as exc:
                active_db.rollback()
                logger.exception("Opportunity calculation failed for %s: %s", symbol, exc)
        logger.info("Opportunity refresh completed: %s rows.", len(saved))
        try:
            add_opportunities_to_queue(active_db, saved, threshold=settings.telegram_alert_min_confidence)
        except Exception as exc:
            logger.info("Backtest queue update skipped after opportunity refresh: %s", exc)
        return {
            "saved": len(saved),
            "provider_status": provider_status,
            "provider_warning": provider_warning,
            "rows": sorted(saved, key=lambda item: item.get("final_score") or 0, reverse=True),
        }

    if db is not None:
        return _run(db)

    with SessionLocal() as active_db:
        return _run(active_db)


def _needs_refresh(db: Session, stale_minutes: int = 10) -> bool:
    latest = db.scalar(select(Opportunity).order_by(Opportunity.updated_at.desc()))
    if not latest:
        return True
    return latest.updated_at < datetime.utcnow() - timedelta(minutes=stale_minutes)


def get_top_opportunities(
    db: Session | None = None,
    settings: Settings | None = None,
    limit: int = 5,
    refresh_if_stale: bool = True,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()

    def _run(active_db: Session) -> list[dict[str, Any]]:
        if refresh_if_stale and _needs_refresh(active_db):
            refresh_opportunities(active_db, settings=settings, limit=max(limit, settings.strategy_symbol_limit), run_screening=True)
        rows = active_db.scalars(select(Opportunity).order_by(Opportunity.final_score.desc(), Opportunity.updated_at.desc()).limit(limit)).all()
        return [
            {
                "symbol": row.symbol,
                "final_score": row.final_score,
                "recommendation": row.recommendation,
                "confidence": row.confidence,
                "entry_price": row.entry_price,
                "target_price": row.target_price,
                "stop_loss": row.stop_loss,
                "reason": row.reason,
                "components_json": row.components_json,
                "source": row.source,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]

    if db is not None:
        return _run(db)

    with SessionLocal() as active_db:
        return _run(active_db)


def format_opportunity_alert(opportunity: dict[str, Any]) -> str:
    components = opportunity.get("components_json") or {}
    source = components.get("screener") or {}
    cli_strategy = components.get("cli_v6_strategy") or {}
    legacy_strategy = components.get("strategy") or {}
    backtest = (components.get("backtests") or [{}])[0] if components.get("backtests") else {}
    timeframes = cli_strategy.get("timeframes") or {}
    frame_lines = []
    for key, label in [("15m", "15m"), ("30m", "30m"), ("1h", "1h"), ("4h", "4h"), ("1d", "Daily")]:
        row = timeframes.get(key) or {}
        score = row.get("score")
        score_text = "-" if score is None else f"{float(score):.0f}"
        frame_lines.append(f"{label}: {row.get('status') or '-'} ({score_text})")
    return (
        "EGX Strategy Alert\n\n"
        f"Symbol: {opportunity.get('symbol')}\n"
        f"Strategy: {CLI_V6_STRATEGY_NAME}\n"
        f"Recommendation: {CONDITIONAL_BUY if opportunity.get('recommendation') == 'BUY' else (cli_strategy.get('recommendation') or opportunity.get('recommendation'))}\n"
        f"Confidence: {cli_strategy.get('confidence') or opportunity.get('confidence') or 0:.0f}%\n"
        f"Final Score: {opportunity.get('final_score') or 0:.0f}%\n"
        f"Timeframes:\n" + "\n".join(frame_lines) + "\n\n"
        f"Backtest:\n"
        f"Win Rate: {backtest.get('win_rate') if backtest.get('win_rate') is not None else '-'}%\n"
        f"Max Drawdown: {backtest.get('max_drawdown') if backtest.get('max_drawdown') is not None else backtest.get('max_drawdown_pct') if backtest.get('max_drawdown_pct') is not None else '-'}%\n"
        f"Profit Factor: {backtest.get('profit_factor') if backtest.get('profit_factor') is not None else '-'}\n\n"
        f"TradingView: {source.get('tv_vote') or '-'}\n"
        f"Legacy Strategy: {legacy_strategy.get('action') or '-'} {legacy_strategy.get('score') or 0:.0f}%\n"
        f"Entry: {opportunity.get('entry_price') or '-'}\n"
        f"Target: {opportunity.get('target_price') or '-'}\n"
        f"Stop Loss: {opportunity.get('stop_loss') or '-'}\n"
        "Entry is allowed only if price reaches the entry zone, volume confirms, market condition is not bearish, "
        "and risk per trade remains below 1%.\n"
        "If conditions are not met: do not enter.\n"
        f"Reason:\n{opportunity.get('reason') or '-'}\n\n"
        f"Risk Note:\n{RISK_NOTE}"
    )


def _risk_reward(entry: Any, stop_loss: Any, target: Any) -> float | None:
    entry_value = _num(entry)
    stop_value = _num(stop_loss)
    target_value = _num(target)
    if entry_value is None or stop_value is None or target_value is None:
        return None
    risk = entry_value - stop_value
    reward = target_value - entry_value
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _validated_opportunity_signal(row: dict[str, Any]) -> str:
    components_json = row.get("components_json") or {}
    components = components_json.get("components") if isinstance(components_json, dict) else {}
    components = components if isinstance(components, dict) else {}
    validation = validate_recommendation(
        {
            "signal": row.get("recommendation"),
            "final_score": row.get("final_score"),
            "telegram_score": components.get("telegram", 50),
            "technical_score": components.get("tradingview") or components.get("system_recommendation") or 50,
            "strategy_score": components.get("cli_v6_strategy", 50),
            "news_score": components.get("news", 50),
            "backtest_score": components.get("backtest", 50),
            "risk_liquidity_score": components_json.get("risk_score", 50) if isinstance(components_json, dict) else 50,
            "entry_zone_low": row.get("entry_price"),
            "entry_zone_high": row.get("entry_price"),
            "stop_loss": row.get("stop_loss"),
            "risk_reward": _risk_reward(row.get("entry_price"), row.get("stop_loss"), row.get("target_price")),
        },
        current_price=row.get("entry_price"),
    )
    return validation.signal


def send_buy_alerts(db: Session | None = None, settings: Settings | None = None, limit: int = 10) -> dict[str, Any]:
    settings = settings or get_settings()

    def _run(active_db: Session) -> dict[str, Any]:
        from app.services.alerts import alerts_configured
        from app.services.stock_alert_service import send_stock_alert

        if not alerts_configured(settings):
            return {"configured": False, "eligible": 0, "sent": 0, "skipped_duplicate": 0}
        opportunities = get_top_opportunities(active_db, settings=settings, limit=limit, refresh_if_stale=True)
        eligible = [
            row
            for row in opportunities
            if row.get("recommendation") == "BUY"
            and float(row.get("final_score") or 0) >= settings.telegram_alert_min_confidence
            and _validated_opportunity_signal(row) == CONDITIONAL_BUY
        ]
        result = {"configured": True, "eligible": len(eligible), "sent": 0, "skipped_duplicate": 0}
        if not eligible:
            return result

        for row in eligible:
            sn = row["symbol"]
            rec = row.get("recommendation", "BUY")
            score_val = row.get("final_score")

            alert_result = send_stock_alert(
                active_db, symbol=sn, recommendation=rec,
                entry_price=row.get("entry_price"), target_price=row.get("target_price"),
                stop_loss=row.get("stop_loss"), score=score_val,
                source_module="opportunity_engine.send_buy_alerts",
                message_text=format_opportunity_alert(row),
                row_data=row, settings=settings,
            )
            if alert_result.blocked:
                result["skipped_duplicate"] += 1
                logger.info("Blocked %s %s: %s | stage=%s", sn, rec, alert_result.reason, alert_result.stage)
                continue
            result["sent"] += 1
            logger.info("Sent %s %s (score=%s, stage=%s)", sn, rec, score_val, alert_result.stage)
        return result

    if db is not None:
        return _run(db)

    with SessionLocal() as active_db:
        return _run(active_db)


def send_strategy_notifications(db: Session | None = None, settings: Settings | None = None, limit: int = 50) -> dict[str, Any]:
    settings = settings or get_settings()

    def _run(active_db: Session) -> dict[str, Any]:
        from app.services.alerts import alerts_configured

        if not alerts_configured(settings):
            return {"configured": False, "eligible": 0, "sent": 0, "skipped_duplicate": 0}
        summaries = active_db.scalars(
            select(StrategyCliV6Result)
            .where(
                StrategyCliV6Result.strategy_name == CLI_V6_STRATEGY_NAME,
                StrategyCliV6Result.timeframe == "summary",
            )
            .order_by(StrategyCliV6Result.created_at.desc(), StrategyCliV6Result.id.desc())
            .limit(max(limit * 5, limit))
        ).all()
        latest_by_symbol: dict[str, StrategyCliV6Result] = {}
        for row in summaries:
            if row.symbol not in latest_by_symbol:
                latest_by_symbol[row.symbol] = row
            if len(latest_by_symbol) >= limit:
                break
        eligible = [
            row
            for row in latest_by_symbol.values()
            if row.recommendation == "STRONG BUY"
            or (row.recommendation == "WEAK BUY" and float(row.confidence or 0) >= settings.telegram_alert_min_confidence)
            or row.recommendation == "STRONG SELL"
        ]
        result = {"configured": True, "eligible": len(eligible), "sent": 0, "skipped_duplicate": 0}
        if not eligible:
            return result

        for summary in eligible:
            sn = summary.symbol
            rec = summary.recommendation
            score_val = summary.confidence

            latest = latest_cli_v6_result(active_db, sn) or {}
            opportunity = active_db.scalar(select(Opportunity).where(Opportunity.symbol == sn))
            opportunity_payload = {
                "symbol": sn,
                "recommendation": opportunity.recommendation if opportunity else rec,
                "final_score": opportunity.final_score if opportunity else score_val,
                "confidence": opportunity.confidence if opportunity else score_val,
                "entry_price": opportunity.entry_price if opportunity else None,
                "target_price": opportunity.target_price if opportunity else None,
                "stop_loss": opportunity.stop_loss if opportunity else None,
                "reason": opportunity.reason if opportunity else summary.reason,
                "components_json": {
                    "cli_v6_strategy": latest,
                    "backtests": get_latest_cli_v6_backtest_summary(active_db, symbol=sn, limit=1),
                    "screener": {},
                    "strategy": {},
                },
            }

            alert_result = send_stock_alert(
                active_db, symbol=sn, recommendation=rec,
                entry_price=opportunity_payload.get("entry_price"),
                target_price=opportunity_payload.get("target_price"),
                stop_loss=opportunity_payload.get("stop_loss"),
                score=score_val,
                source_module="opportunity_engine.send_strategy_notifications",
                message_text=format_opportunity_alert(opportunity_payload),
                row_data=opportunity_payload, settings=settings,
            )
            if alert_result.blocked:
                result["skipped_duplicate"] += 1
                logger.info("Blocked %s %s: %s | stage=%s", sn, rec, alert_result.reason, alert_result.stage)
                continue
            result["sent"] += 1
            logger.info("Sent %s %s (score=%s, stage=%s)", sn, rec, score_val, alert_result.stage)
        return result

    if db is not None:
        return _run(db)

    with SessionLocal() as active_db:
        return _run(active_db)


def format_top_opportunities(db: Session, settings: Settings | None = None, limit: int = 5) -> str:
    rows = get_top_opportunities(db, settings=settings, limit=limit, refresh_if_stale=True)
    lines = ["Top EGX Opportunities Today", ""]
    if not rows:
        lines.append("No current opportunity rows are available. Run the update process first.")
    for idx, row in enumerate(rows, start=1):
        components = row.get("components_json") or {}
        cli_strategy = components.get("cli_v6_strategy") or {}
        strategy = components.get("strategy") or {}
        screener = components.get("screener") or {}
        backtest = (components.get("backtests") or [{}])[0] if components.get("backtests") else {}
        lines.extend(
            [
                f"{idx}. {row['symbol']} - {row.get('recommendation')}",
                f"   Score: {row.get('final_score') or 0:.0f}%",
                f"   CLI v6: {cli_strategy.get('recommendation') or '-'} {cli_strategy.get('confidence') or 0:.0f}%",
                f"   Legacy strategy: {strategy.get('action') or '-'} {strategy.get('score') or 0:.0f}%",
                f"   Screener: {screener.get('recommendation') or '-'} | TV {screener.get('tv_vote') or '-'}",
                f"   Backtest: {backtest.get('recommendation') or '-'} {backtest.get('score') or 0:.0f}%",
                f"   Entry: {row.get('entry_price') or '-'}",
                f"   Target: {row.get('target_price') or '-'}",
                f"   Stop Loss: {row.get('stop_loss') or '-'}",
                f"   Reason: {row.get('reason') or '-'}",
                "",
            ]
        )
    lines.append(f"Risk Note: {RISK_NOTE}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh EGX opportunities.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--alerts", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    init_db(seed=True)
    with SessionLocal() as db:
        if args.alerts:
            print(send_buy_alerts(db, limit=min(args.limit, 10)))
        else:
            result = refresh_opportunities(db, limit=args.limit)
            print(f"Saved {result['saved']} opportunities. Provider status: {result['provider_status']}")


if __name__ == "__main__":
    main()
