from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import RISK_NOTE, Settings, get_settings
from app.database import SessionLocal, sqlite_write_lock
from app.models import (
    ExtractedSignal,
    Stock,
    StockCombinedAnalysis,
    StrategyBacktestSummary,
    StrategyResult,
    TelegramMediaAnalysis,
    TelegramMessage,
    TelegramMessageSymbol,
    TradingViewScreeningResult,
    TradingViewScreeningRun,
)
from app.services.daily_egx_report import latest_report_component
from app.services.dynamic_settings import combined_weights
from app.services.strategy_registry import CLI_V6_CODE, LEGACY_CODE, run_all_enabled_strategies
from app.services.strategies.cli_v6_egx import latest_cli_v6_result, normalize_symbol, recommendation_to_score, run_cli_v6_for_symbol


logger = logging.getLogger(__name__)


def _bound(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _latest_strategy_result(db: Session, symbol: str, strategy_code: str) -> StrategyResult | None:
    return db.scalar(
        select(StrategyResult)
        .where(
            StrategyResult.symbol == symbol,
            StrategyResult.strategy_code == strategy_code,
            StrategyResult.timeframe == "summary",
        )
        .order_by(StrategyResult.created_at.desc(), StrategyResult.id.desc())
    )


def _strategy_score(row: StrategyResult | None) -> float | None:
    if not row:
        return None
    action = str(row.recommendation or row.signal or "").upper()
    if action in {"BUY", "STRONG BUY"}:
        base = 88.0
    elif action in {"WEAK BUY", "WATCH"}:
        base = 70.0
    elif action in {"NEUTRAL", "HOLD"}:
        base = 50.0
    elif action in {"WEAK SELL", "AVOID"}:
        base = 30.0
    elif action in {"SELL", "STRONG SELL"}:
        base = 12.0
    elif action == "UNAVAILABLE":
        return None
    else:
        base = _num(row.score)
        if base is None:
            return None
    confidence = _num(row.confidence, _num(row.score, base)) or base
    return _bound(base * 0.65 + confidence * 0.35)


def _telegram_score(db: Session, symbol: str) -> tuple[float | None, dict[str, Any]]:
    since = datetime.utcnow() - timedelta(days=14)
    rows = db.scalars(
        select(TelegramMessageSymbol)
        .where(TelegramMessageSymbol.symbol == symbol, TelegramMessageSymbol.created_at >= since)
        .order_by(TelegramMessageSymbol.created_at.desc())
        .limit(50)
    ).all()
    signals = db.scalars(
        select(ExtractedSignal)
        .where(ExtractedSignal.stock_symbol == symbol, ExtractedSignal.created_at >= since)
        .order_by(ExtractedSignal.created_at.desc())
        .limit(50)
    ).all()
    if not rows and not signals:
        return None, {"mentions": 0, "signals": 0}
    score = 50.0
    buy_mentions = sum(1 for row in rows if (row.intent or "").lower() in {"buy", "watch", "target", "support"})
    sell_mentions = sum(1 for row in rows if (row.intent or "").lower() in {"sell", "avoid", "stop_loss", "resistance"})
    score += min(18.0, len(rows) * 2.0)
    score += buy_mentions * 4.0
    score -= sell_mentions * 5.0
    sentiment = sum(float(signal.sentiment_score or 0) for signal in signals)
    score += max(-20.0, min(20.0, sentiment * 18.0))
    return _bound(score), {
        "mentions": len(rows),
        "signals": len(signals),
        "buy_mentions": buy_mentions,
        "sell_mentions": sell_mentions,
        "latest_symbols": [
            {
                "symbol": row.symbol,
                "intent": row.intent,
                "confidence": row.confidence,
                "source": row.source,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows[:8]
        ],
    }


def _latest_tradingview_score(db: Session, symbol: str) -> tuple[float | None, dict[str, Any]]:
    run = db.scalar(select(TradingViewScreeningRun).order_by(TradingViewScreeningRun.created_at.desc()))
    if not run:
        return None, {}
    row = db.scalar(
        select(TradingViewScreeningResult).where(
            TradingViewScreeningResult.run_id == run.id,
            TradingViewScreeningResult.symbol == symbol,
        )
    )
    if not row:
        return None, {"run_id": run.id}
    return _num(row.final_score), {
        "run_id": run.id,
        "recommendation": row.recommendation,
        "tv_vote": row.tv_vote,
        "telegram_vote": row.telegram_vote,
        "close": row.close,
        "change_percent": row.change_percent,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _backtest_score(db: Session, symbol: str) -> tuple[float | None, dict[str, Any]]:
    row = db.scalar(
        select(StrategyBacktestSummary)
        .where(StrategyBacktestSummary.symbol == symbol)
        .order_by(StrategyBacktestSummary.updated_at.desc())
    )
    if not row:
        return None, {}
    score = _num(row.score)
    if score is None:
        total_return = _num(row.total_return, 0.0) or 0.0
        win_rate = _num(row.win_rate, 50.0) or 50.0
        drawdown = _num(row.max_drawdown, 0.0) or 0.0
        score = 50 + total_return * 0.5 + (win_rate - 50) * 0.25 - drawdown * 0.6
    return _bound(score), {
        "strategy_name": row.strategy_name,
        "timeframe": row.timeframe,
        "recommendation": row.recommendation,
        "score": row.score,
        "total_return": row.total_return,
        "win_rate": row.win_rate,
        "max_drawdown": row.max_drawdown,
        "profit_factor": row.profit_factor,
        "trades_count": row.trades_count,
        "latest_signal": row.latest_signal,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _risk_freshness_score(db: Session, cli_result: dict[str, Any] | None, backtest: dict[str, Any]) -> tuple[float, list[str]]:
    score = 85.0
    risks: list[str] = []
    latest_tv = db.scalar(select(func.max(TradingViewScreeningRun.completed_at)))
    if latest_tv:
        age_hours = max(0.0, (datetime.utcnow() - latest_tv).total_seconds() / 3600)
        if age_hours > 24:
            score -= 10
            risks.append("TradingView screening is older than 24 hours")
    else:
        score -= 10
        risks.append("No TradingView screening yet")
    recommendation = str((cli_result or {}).get("recommendation") or "").upper()
    if recommendation == "STRONG SELL":
        score -= 35
        risks.append("CLI v6 shows STRONG SELL")
    elif recommendation == "WEAK SELL":
        score -= 16
        risks.append("CLI v6 shows WEAK SELL")
    drawdown = _num(backtest.get("max_drawdown"), 0.0) or 0.0
    if drawdown >= 20:
        score -= 15
        risks.append("Backtest drawdown is high")
    return _bound(score), risks


def _recommendation(score: float, cli_result: dict[str, Any] | None, risk_score: float) -> str:
    cli_rec = str((cli_result or {}).get("recommendation") or "").upper()
    if cli_rec == "STRONG SELL" or score < 30:
        return "AVOID"
    if score >= 78 and risk_score >= 60:
        return "BUY"
    if score >= 62 and risk_score >= 50:
        return "WATCH"
    if score <= 40:
        return "AVOID"
    return "NEUTRAL"


def build_combined_analysis(
    db: Session,
    symbol: str,
    settings: Settings | None = None,
    run_missing: bool = False,
    persist: bool = True,
) -> dict[str, Any]:
    settings = settings or get_settings()
    symbol = normalize_symbol(symbol)
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    if not stock:
        raise ValueError(f"Unknown EGX symbol: {symbol}")

    if run_missing:
        try:
            if not _latest_strategy_result(db, symbol, LEGACY_CODE) or not _latest_strategy_result(db, symbol, CLI_V6_CODE):
                run_all_enabled_strategies(symbol=symbol, db=db, settings=settings)
        except Exception as exc:
            logger.warning("Strategy refresh skipped while building combined analysis for %s: %s", symbol, exc)
        try:
            if latest_cli_v6_result(db, symbol) is None:
                run_cli_v6_for_symbol(db, symbol, settings=settings)
        except Exception as exc:
            logger.warning("CLI v6 refresh skipped for %s: %s", symbol, exc)

    telegram_score, telegram_details = _telegram_score(db, symbol)
    legacy_result = _latest_strategy_result(db, symbol, LEGACY_CODE)
    cli_common_result = _latest_strategy_result(db, symbol, CLI_V6_CODE)
    cli_result = latest_cli_v6_result(db, symbol)
    legacy_score = _strategy_score(legacy_result)
    cli_score = recommendation_to_score((cli_result or {}).get("recommendation"), (cli_result or {}).get("confidence"))
    if cli_score is None:
        cli_score = _strategy_score(cli_common_result)
    tradingview_score, tradingview_details = _latest_tradingview_score(db, symbol)
    backtest_score, backtest_details = _backtest_score(db, symbol)
    daily_report_score, daily_report_details = latest_report_component(db, symbol)
    risk_score, risks = _risk_freshness_score(db, cli_result, backtest_details)

    scores = {
        "telegram": telegram_score,
        "strategy_legacy": legacy_score,
        "cli_v6": cli_score,
        "daily_report": daily_report_score,
        "tradingview": tradingview_score,
        "backtest": backtest_score,
        "risk": risk_score,
    }
    weights = combined_weights(db)
    total = 0.0
    active_weight = 0.0
    for key, score in scores.items():
        if score is None:
            continue
        weight = float(weights.get(key, 0) or 0)
        total += score * weight
        active_weight += weight
    final_score = _bound(total / active_weight) if active_weight else 0.0
    rec = _recommendation(final_score, cli_result, risk_score)
    reasons = []
    if telegram_score is not None:
        reasons.append(f"Telegram score {telegram_score:.0f} from {telegram_details.get('mentions', 0)} mention(s).")
    if legacy_score is not None:
        reasons.append(f"Legacy strategy score {legacy_score:.0f}.")
    if cli_score is not None:
        reasons.append(f"CLI v6 score {cli_score:.0f} with recommendation {(cli_result or {}).get('recommendation') or '-'}.")
    if daily_report_score is not None:
        reasons.append(
            f"Daily Excel report score {daily_report_score:.0f} with recommendation {daily_report_details.get('recommendation') or '-'}."
        )
    if tradingview_score is not None:
        reasons.append(f"TradingView score {tradingview_score:.0f}.")
    if backtest_score is not None:
        reasons.append(f"Backtest quality score {backtest_score:.0f}.")
    if risks:
        reasons.append("Risk notes: " + "; ".join(risks[:3]) + ".")
    if not reasons:
        reasons.append("Not enough current source data is available yet.")

    payload = {
        "symbol": symbol,
        "final_recommendation": rec,
        "final_score": final_score,
        "confidence": final_score,
        "telegram_score": telegram_score,
        "strategy_legacy_score": legacy_score,
        "strategy_cli_v6_score": cli_score,
        "daily_report_score": daily_report_score,
        "tradingview_score": tradingview_score,
        "backtest_score": backtest_score,
        "risk_score": risk_score,
        "reason": " ".join(reasons),
        "components_json": {
            "weights": weights,
            "scores": scores,
            "telegram": telegram_details,
            "legacy_strategy": legacy_result.details_json if legacy_result else None,
            "cli_v6_strategy": cli_result,
            "daily_report": daily_report_details,
            "tradingview": tradingview_details,
            "backtest": backtest_details,
            "risks": risks,
            "risk_note": RISK_NOTE,
        },
    }

    if persist:
        existing = db.scalar(select(StockCombinedAnalysis).where(StockCombinedAnalysis.symbol == symbol))
        with sqlite_write_lock():
            if existing:
                for key, value in payload.items():
                    setattr(existing, key, value)
                existing.updated_at = datetime.utcnow()
            else:
                db.add(StockCombinedAnalysis(**payload))
            db.commit()
    return payload


def latest_combined_analysis(db: Session, symbol: str) -> dict[str, Any] | None:
    row = db.scalar(select(StockCombinedAnalysis).where(StockCombinedAnalysis.symbol == normalize_symbol(symbol)))
    if not row:
        return None
    return {
        "symbol": row.symbol,
        "final_recommendation": row.final_recommendation,
        "final_score": row.final_score,
        "confidence": row.confidence,
        "telegram_score": row.telegram_score,
        "strategy_legacy_score": row.strategy_legacy_score,
        "strategy_cli_v6_score": row.strategy_cli_v6_score,
        "daily_report_score": row.daily_report_score,
        "tradingview_score": row.tradingview_score,
        "backtest_score": row.backtest_score,
        "risk_score": row.risk_score,
        "reason": row.reason,
        "components_json": row.components_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def refresh_combined_analysis(
    db: Session,
    symbols: list[str] | None = None,
    settings: Settings | None = None,
    limit: int = 100,
    run_missing: bool = False,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if symbols is None:
        symbols = db.scalars(select(Stock.symbol).where(Stock.is_active.is_(True)).order_by(Stock.symbol.asc()).limit(limit)).all()
    rows = []
    errors = []
    for symbol in symbols[:limit]:
        try:
            rows.append(build_combined_analysis(db, symbol, settings=settings, run_missing=run_missing, persist=True))
        except Exception as exc:
            db.rollback()
            logger.warning("Combined analysis failed for %s: %s", symbol, exc)
            errors.append(f"{symbol}: {exc}")
    return {"rows": rows, "errors": errors, "count": len(rows)}


def latest_related_telegram(db: Session, symbol: str, limit: int = 8) -> list[dict[str, Any]]:
    symbol = normalize_symbol(symbol)
    rows = db.scalars(
        select(TelegramMessage)
        .join(TelegramMessageSymbol, TelegramMessageSymbol.telegram_message_id == TelegramMessage.id)
        .where(TelegramMessageSymbol.symbol == symbol)
        .order_by(TelegramMessage.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "message_id": row.message_id,
            "channel": row.channel_name or (row.source.title if row.source else None),
            "message_date": row.message_date,
            "text": row.message_text or row.text,
            "media_path": row.media_path or row.image_path,
        }
        for row in rows
    ]


def latest_related_media(db: Session, symbol: str, limit: int = 8) -> list[dict[str, Any]]:
    symbol = normalize_symbol(symbol)
    rows = db.scalars(
        select(TelegramMediaAnalysis)
        .where(TelegramMediaAnalysis.detected_symbols.like(f"%{symbol}%"))
        .order_by(TelegramMediaAnalysis.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "media_path": row.media_path,
            "status": row.status,
            "detected_symbols": row.detected_symbols,
            "ocr_text": row.ocr_text,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def format_combined_analysis_report(symbol: str, settings: Settings | None = None, refresh: bool = True) -> str:
    symbol = normalize_symbol(symbol)
    with SessionLocal() as db:
        data = latest_combined_analysis(db, symbol)
        if refresh or data is None:
            try:
                data = build_combined_analysis(db, symbol, settings=settings or get_settings(), run_missing=True, persist=True)
            except Exception as exc:
                return (
                    f"Combined Analysis: {symbol}\n"
                    f"Could not build combined analysis: {exc}\n"
                    "Run TradingView/data import first if OHLCV is missing.\n"
                    f"Risk Note: {RISK_NOTE}"
                )
        components = data.get("components_json") or {}
        scores = components.get("scores") or {}
        backtest = components.get("backtest") or {}
        lines = [
            f"Combined Analysis: {symbol}",
            f"Final recommendation: {data.get('final_recommendation')}",
            f"Final score: {float(data.get('final_score') or 0):.0f}%",
            f"Confidence: {float(data.get('confidence') or 0):.0f}%",
            "",
            "Source scores",
            f"- Telegram: {scores.get('telegram') if scores.get('telegram') is not None else '-'}",
            f"- Legacy strategy: {scores.get('strategy_legacy') if scores.get('strategy_legacy') is not None else '-'}",
            f"- CLI v6: {scores.get('cli_v6') if scores.get('cli_v6') is not None else '-'}",
            f"- Daily Excel: {scores.get('daily_report') if scores.get('daily_report') is not None else '-'}",
            f"- TradingView: {scores.get('tradingview') if scores.get('tradingview') is not None else '-'}",
            f"- Backtest: {scores.get('backtest') if scores.get('backtest') is not None else '-'}",
            f"- Risk/freshness: {scores.get('risk') if scores.get('risk') is not None else '-'}",
            "",
            f"Backtest: {backtest.get('recommendation') or '-'} | WR {backtest.get('win_rate') if backtest.get('win_rate') is not None else '-'}% | DD {backtest.get('max_drawdown') if backtest.get('max_drawdown') is not None else '-'}%",
            "",
            f"Reason: {data.get('reason') or '-'}",
            f"Risk Note: {RISK_NOTE}",
        ]
        return "\n".join(lines)
