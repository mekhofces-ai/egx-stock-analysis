from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import REPORT_TIMEZONE, RISK_NOTE
from app.data.market_data import get_ohlcv
from app.database import SessionLocal, init_db, sqlite_write_lock
from app.models import (
    DecisionSnapshot,
    EndOfDayReviewItem,
    EndOfDayReviewReport,
    FinancialSignal,
    IntradayScanItem,
    IntradayScanRun,
    NewsSignal,
    OHLCVData,
    PumpRiskSnapshot,
    RecommendationEvaluation,
    RecommendationItem,
    RecommendationQualitySnapshot,
    RecommendationReport,
    RiskExpectancySnapshot,
    SourceAccuracySnapshot,
    Stock,
    StockNews,
    StrategyLearningReport,
    TelegramMessage,
    TelegramMessageSymbol,
    WalkForwardPeriod,
    WalkForwardRun,
)
from app.services.daily_loss_audit import (
    EVAL_DATA_MISSING,
    EVAL_ENTRY_NOT_REACHED,
    EVAL_NOT_EVALUATED,
    EVAL_STOP_HIT,
    EVAL_TARGET_HIT,
)
from app.services.market_daily_evaluation import evaluate_daily_market
from app.technical.indicators import add_indicators
from app.technical.support_resistance import breakout_state, support_resistance


logger = logging.getLogger(__name__)
CAIRO_TZ = ZoneInfo(REPORT_TIMEZONE)
NON_ACCURACY_STATUSES = {EVAL_NOT_EVALUATED, EVAL_DATA_MISSING, EVAL_ENTRY_NOT_REACHED}
BUY_SIGNALS = {"BUY", "STRONG BUY", "CONDITIONAL BUY"}


def cairo_now() -> datetime:
    return datetime.now(CAIRO_TZ)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        number = float(value)
    except Exception:
        return default
    if pd.isna(number):
        return default
    return number


def _clamp(value: Any, low: float = 0.0, high: float = 100.0, default: float = 50.0) -> float:
    number = _safe_float(value, default)
    return round(max(low, min(high, float(number if number is not None else default))), 2)


def _round(value: Any, digits: int = 2) -> float | None:
    number = _safe_float(value)
    return round(number, digits) if number is not None else None


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _score_from_status(status: str | None, actual_return: float | None = None) -> bool:
    if status == EVAL_TARGET_HIT:
        return True
    if status == EVAL_STOP_HIT:
        return False
    return (actual_return or 0.0) > 0


def _evaluation_rows(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(RecommendationEvaluation, RecommendationItem)
        .join(RecommendationItem, RecommendationItem.id == RecommendationEvaluation.recommendation_item_id)
        .order_by(RecommendationEvaluation.recommendation_datetime.asc(), RecommendationEvaluation.id.asc())
    ).all()
    output: list[dict[str, Any]] = []
    for evaluation, item in rows:
        details = item.details_json or {}
        output.append(
            {
                "evaluation_id": evaluation.id,
                "item_id": item.id,
                "symbol": evaluation.symbol or item.symbol,
                "stage": evaluation.recommendation_stage or item.signal,
                "status": evaluation.final_status,
                "actual_return_pct": _safe_float(evaluation.actual_return_pct),
                "target_hit": bool(evaluation.target_hit),
                "stop_hit": bool(evaluation.stop_hit),
                "days_evaluated": int(evaluation.days_evaluated or 0),
                "recommendation_datetime": evaluation.recommendation_datetime,
                "strategy_source": evaluation.strategy_source or (details.get("strategy", {}) or {}).get("strategy_name"),
                "telegram_source": evaluation.telegram_source,
                "technical_score": _safe_float(item.technical_score),
                "financial_score": _safe_float(details.get("financial_score") or (details.get("financial") or {}).get("score")),
                "news_score": _safe_float(item.news_score),
                "telegram_score": _safe_float(item.telegram_score),
                "strategy_score": _safe_float(item.strategy_score),
                "combined_score": _safe_float(item.final_score),
                "risk_reward": _safe_float(item.risk_reward),
                "risk_liquidity_score": _safe_float(item.risk_liquidity_score),
                "details": details,
            }
        )
    return output


def _filtered_evaluated(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("status") not in NON_ACCURACY_STATUSES]


def _latest_past_ohlcv(db: Session, symbol: str, snapshot_time: datetime) -> dict[str, Any]:
    frame = get_ohlcv(db, symbol, timeframe="1D", limit=700)
    if frame is None or frame.empty:
        return {"data_quality": "MISSING"}
    frame = frame.copy().reset_index(drop=True)
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    past = frame[frame["datetime"] <= pd.Timestamp(_naive(snapshot_time))].dropna(subset=["datetime"]).sort_values("datetime")
    if past.empty:
        return {"data_quality": "NO_PAST_CANDLE"}
    last = past.iloc[-1]
    close = _safe_float(last.get("close"))
    volume = _safe_float(last.get("volume"), 0.0) or 0.0
    value_traded = close * volume if close is not None else None
    technical: dict[str, Any] = {"data_quality": "OK"}
    try:
        enriched = add_indicators(past.tail(260).copy())
        if not enriched.empty:
            latest = enriched.iloc[-1]
            sr = support_resistance(enriched)
            technical = {
                "rsi14": _round(latest.get("rsi14")),
                "macd": _round(latest.get("macd")),
                "macd_signal": _round(latest.get("macd_signal")),
                "macd_hist": _round(latest.get("macd_hist")),
                "ema20": _round(latest.get("ema20")),
                "ema50": _round(latest.get("ema50")),
                "ema200": _round(latest.get("ema200")),
                "atr14": _round(latest.get("atr14")),
                "breakout_state": breakout_state(enriched),
                "support": _round(sr.get("support")),
                "resistance": _round(sr.get("resistance")),
            }
    except Exception as exc:
        technical = {"data_quality": "INDICATOR_ERROR", "error": str(exc)}
    return {
        "data_quality": "OK",
        "stock_price": close,
        "open": _safe_float(last.get("open")),
        "high": _safe_float(last.get("high")),
        "low": _safe_float(last.get("low")),
        "close": close,
        "volume": volume,
        "value_traded": value_traded,
        "technical": technical,
        "latest_candle_time": str(last.get("datetime")),
    }


def _telegram_before(db: Session, symbol: str, snapshot_time: datetime) -> dict[str, Any]:
    start = _naive(snapshot_time) - timedelta(days=2)
    end = _naive(snapshot_time)
    rows = db.execute(
        select(TelegramMessageSymbol, TelegramMessage)
        .outerjoin(TelegramMessage, TelegramMessage.id == TelegramMessageSymbol.telegram_message_id)
        .where(
            TelegramMessageSymbol.symbol == symbol,
            TelegramMessageSymbol.created_at >= start,
            TelegramMessageSymbol.created_at <= end,
        )
        .order_by(TelegramMessageSymbol.created_at.desc())
        .limit(100)
    ).all()
    channels = {message.channel_name or message.channel_id or "unknown" for _symbol, message in rows if message}
    confidences = [_safe_float(symbol_row.confidence) for symbol_row, _message in rows]
    confidences = [value for value in confidences if value is not None]
    aggressive_words = ("هدف", "صاروخ", "انفجار", "فرصة العمر", "buy now", "strong buy", "rocket")
    aggressive = 0
    for _symbol, message in rows:
        text = ((message.text if message else "") or (message.message_text if message else "") or "").lower()
        if any(word in text for word in aggressive_words):
            aggressive += 1
    return {
        "mentions": len(rows),
        "channels": sorted(channels),
        "channels_count": len(channels),
        "avg_confidence": round(sum(confidences) / len(confidences), 2) if confidences else None,
        "low_confidence_mentions": sum(1 for value in confidences if value < 50),
        "aggressive_mentions": aggressive,
    }


def _news_before(db: Session, symbol: str, snapshot_time: datetime) -> dict[str, Any]:
    start = _naive(snapshot_time) - timedelta(days=7)
    end = _naive(snapshot_time)
    rows = db.scalars(
        select(StockNews)
        .where(StockNews.symbol == symbol, StockNews.published_at >= start, StockNews.published_at <= end)
        .order_by(StockNews.published_at.desc())
        .limit(20)
    ).all()
    scores = [_safe_float(row.sentiment_score) for row in rows]
    scores = [value for value in scores if value is not None]
    return {
        "items": len(rows),
        "avg_sentiment_score": round(sum(scores) / len(scores), 3) if scores else None,
        "latest_titles": [row.title for row in rows[:5] if row.title],
    }


def _latest_financial_before(db: Session, symbol: str, snapshot_time: datetime) -> float | None:
    row = db.scalar(
        select(FinancialSignal)
        .where(FinancialSignal.symbol == symbol, FinancialSignal.signal_date <= _naive(snapshot_time))
        .order_by(FinancialSignal.signal_date.desc(), FinancialSignal.id.desc())
    )
    return _safe_float(row.financial_score) if row else None


def create_decision_snapshot(
    db: Session,
    recommendation_item: RecommendationItem,
    *,
    recommendation_time: datetime | None = None,
    market_condition: dict[str, Any] | None = None,
    commit: bool = False,
) -> DecisionSnapshot:
    if recommendation_time is not None:
        snapshot_time = _naive(recommendation_time)
    elif getattr(recommendation_item, "report", None) is not None:
        snapshot_time = _naive(recommendation_item.report.report_time)
    else:
        snapshot_time = datetime.now(UTC).replace(tzinfo=None)
    symbol = str(recommendation_item.symbol or "").upper()
    existing = None
    if recommendation_item.id is not None:
        existing = db.scalar(select(DecisionSnapshot).where(DecisionSnapshot.recommendation_item_id == recommendation_item.id))
    snapshot = existing or DecisionSnapshot(recommendation_item_id=recommendation_item.id, recommendation_report_id=recommendation_item.report_id, symbol=symbol, snapshot_time=snapshot_time)
    ohlcv = _latest_past_ohlcv(db, symbol, snapshot_time)
    details = recommendation_item.details_json or {}
    validation = (details.get("validation") or {}) if isinstance(details, dict) else {}
    failed_filters = validation.get("no_trade_reasons") or validation.get("failed_filters") or []
    technical = dict(ohlcv.get("technical") or {})
    technical["score"] = recommendation_item.technical_score
    telegram = _telegram_before(db, symbol, snapshot_time)
    news = _news_before(db, symbol, snapshot_time)
    market = market_condition or evaluate_daily_market(db, target_date=snapshot_time.date(), persist=False)
    risk_reward = _safe_float(recommendation_item.risk_reward)
    snapshot.stock_price = _safe_float(ohlcv.get("stock_price"))
    snapshot.open = _safe_float(ohlcv.get("open"))
    snapshot.high = _safe_float(ohlcv.get("high"))
    snapshot.low = _safe_float(ohlcv.get("low"))
    snapshot.close = _safe_float(ohlcv.get("close"))
    snapshot.volume = _safe_float(ohlcv.get("volume"))
    snapshot.value_traded = _safe_float(ohlcv.get("value_traded"))
    snapshot.spread_pct = _safe_float(details.get("spread_pct"))
    snapshot.technical_json = technical
    snapshot.telegram_json = telegram
    snapshot.news_json = news
    snapshot.financial_score = _latest_financial_before(db, symbol, snapshot_time)
    snapshot.liquidity_score = _safe_float(recommendation_item.risk_liquidity_score)
    snapshot.risk_reward_score = _clamp((risk_reward or 0.0) * 40.0, default=0.0)
    snapshot.market_condition_json = market
    snapshot.final_score = _safe_float(recommendation_item.final_score)
    snapshot.decision = recommendation_item.signal
    snapshot.selected_rejected = "selected" if str(recommendation_item.signal or "").upper() not in {"AVOID", "WATCH ONLY"} else "rejected_or_watch"
    snapshot.reason_selected = recommendation_item.explanation
    snapshot.failed_filters_json = list(failed_filters) if isinstance(failed_filters, list) else [str(failed_filters)]
    snapshot.strategy_version = str((details.get("strategy") or {}).get("strategy_name") or "combined_model_v1")
    snapshot.weights_version = json.dumps((details.get("weights") or {}), sort_keys=True)
    snapshot.raw_json = {"recommendation_details": details, "ohlcv_quality": ohlcv.get("data_quality")}
    if existing is None:
        db.add(snapshot)
    if commit:
        db.commit()
    return snapshot


def capture_report_decision_snapshots(db: Session, report: RecommendationReport) -> list[DecisionSnapshot]:
    market = evaluate_daily_market(db, target_date=report.report_time.date(), persist=False)
    items = db.scalars(select(RecommendationItem).where(RecommendationItem.report_id == report.id)).all()
    snapshots = [create_decision_snapshot(db, item, recommendation_time=report.report_time, market_condition=market) for item in items]
    db.flush()
    return snapshots


def calculate_pump_risk(
    *,
    symbol: str,
    telegram_mentions: int = 0,
    low_confidence_sources: int = 0,
    multi_channel_mentions: int = 0,
    aggressive_mentions: int = 0,
    pre_signal_move_pct: float | None = None,
    liquidity_score: float | None = None,
    spread_pct: float | None = None,
    technical_score: float | None = None,
    financial_score: float | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    score = 0.0
    if telegram_mentions >= 8:
        score += min(30.0, telegram_mentions * 2.0)
        reasons.append("many repeated Telegram mentions")
    if multi_channel_mentions >= 3:
        score += 15.0
        reasons.append("same stock promoted by multiple channels")
    if low_confidence_sources >= 2:
        score += min(20.0, low_confidence_sources * 7.0)
        reasons.append("mentions came from low-confidence sources")
    if aggressive_mentions:
        score += min(20.0, aggressive_mentions * 6.0)
        reasons.append("aggressive promotional language detected")
    if (pre_signal_move_pct or 0.0) >= 5.0:
        score += 15.0
        reasons.append("price already moved strongly before the signal")
    if liquidity_score is not None and liquidity_score < 45:
        score += 15.0
        reasons.append("liquidity confirmation is weak")
    if spread_pct is not None and spread_pct > 1.5:
        score += 10.0
        reasons.append("spread is wide")
    if (technical_score or 50.0) < 60 and (financial_score or 50.0) < 55:
        score += 15.0
        reasons.append("Telegram move lacks technical/financial confirmation")
    score = _clamp(score, default=0.0)
    if score >= 75:
        level = "HIGH"
    elif score >= 50:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {
        "symbol": symbol.upper(),
        "pump_risk_score": score,
        "risk_level": level,
        "downgrade_action": "WATCH_ONLY" if score >= 70 else "NONE",
        "reason": "; ".join(reasons) if reasons else "No major pump-risk pattern detected.",
        "reasons": reasons,
    }


def calculate_pump_risk_for_row(db: Session, row: dict[str, Any], *, now: datetime | None = None, persist: bool = False) -> dict[str, Any]:
    local_now = _naive(now or datetime.now(UTC))
    symbol = str(row.get("symbol") or row.get("Stock Symbol") or "").upper()
    telegram = _telegram_before(db, symbol, local_now)
    frame = get_ohlcv(db, symbol, timeframe="1D", limit=30)
    pre_move = None
    if frame is not None and len(frame) >= 2:
        latest = frame.iloc[-1]
        prev = frame.iloc[-2]
        prev_close = _safe_float(prev.get("close"))
        close = _safe_float(latest.get("close"))
        if prev_close:
            pre_move = (float(close or prev_close) - prev_close) / prev_close * 100.0
    result = calculate_pump_risk(
        symbol=symbol,
        telegram_mentions=int(telegram.get("mentions") or 0),
        low_confidence_sources=int(telegram.get("low_confidence_mentions") or 0),
        multi_channel_mentions=int(telegram.get("channels_count") or 0),
        aggressive_mentions=int(telegram.get("aggressive_mentions") or 0),
        pre_signal_move_pct=pre_move,
        liquidity_score=_safe_float(row.get("risk_liquidity_score") or row.get("Liquidity Score")),
        spread_pct=_safe_float(row.get("spread_pct")),
        technical_score=_safe_float(row.get("technical_score") or row.get("Technical Score")),
        financial_score=_safe_float(row.get("financial_score") or row.get("Financial Score")),
    )
    result.update(
        {
            "repeated_messages": int(telegram.get("mentions") or 0),
            "low_confidence_sources": int(telegram.get("low_confidence_mentions") or 0),
            "pre_signal_move_pct": _round(pre_move),
        }
    )
    if persist:
        db.add(
            PumpRiskSnapshot(
                symbol=symbol,
                as_of=local_now,
                pump_risk_score=result["pump_risk_score"],
                risk_level=result["risk_level"],
                repeated_messages=result["repeated_messages"],
                low_confidence_sources=result["low_confidence_sources"],
                pre_signal_move_pct=result["pre_signal_move_pct"],
                liquidity_score=_safe_float(row.get("risk_liquidity_score") or row.get("Liquidity Score")),
                spread_pct=_safe_float(row.get("spread_pct")),
                technical_confirmation=(_safe_float(row.get("technical_score") or row.get("Technical Score"), 50.0) or 50.0) >= 60,
                financial_confirmation=(_safe_float(row.get("financial_score") or row.get("Financial Score"), 50.0) or 50.0) >= 55,
                downgrade_action=result["downgrade_action"],
                reason=result["reason"],
                details_json=result,
            )
        )
    return result


def apply_pump_risk_guard(db: Session, row: dict[str, Any], *, now: datetime | None = None, persist: bool = False) -> dict[str, Any]:
    updated = dict(row)
    pump = calculate_pump_risk_for_row(db, updated, now=now, persist=persist)
    details = dict(updated.get("details") or {})
    details["pump_risk"] = pump
    signal = str(updated.get("signal") or "").upper()
    if pump["pump_risk_score"] >= 70 and signal in BUY_SIGNALS:
        updated["signal"] = "WATCH ONLY"
        updated["signal_grade"] = "B"
        reason = f"Pump-risk downgrade: {pump['reason']}"
        updated["explanation"] = f"{updated.get('explanation') or ''}\n{reason}".strip()
    updated["pump_risk_score"] = pump["pump_risk_score"]
    updated["details"] = details
    return updated


def apply_market_regime_guard(row: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    details = dict(updated.get("details") or {})
    details["market_regime"] = market
    permission = str(market.get("trade_permission") or "")
    regime = str(market.get("market_regime") or "")
    signal = str(updated.get("signal") or "").upper()
    blocked = permission in {"BUY_BLOCKED", "NO_TRADING", "SELL_ONLY", "DATA_INSUFFICIENT"} and regime != "MARKET_CLOSED"
    if blocked and signal in BUY_SIGNALS:
        updated["signal"] = "WATCH ONLY" if permission != "NO_TRADING" else "AVOID"
        updated["signal_grade"] = "B" if updated["signal"] == "WATCH ONLY" else "D"
        updated["explanation"] = (
            f"{updated.get('explanation') or ''}\nMarket-regime downgrade: {regime}/{permission}. {market.get('explanation') or ''}"
        ).strip()
    updated["live_trade_allowed"] = permission == "TRADE_ALLOWED" and signal in BUY_SIGNALS
    updated["market_regime"] = regime
    updated["market_score"] = market.get("market_score")
    updated["details"] = details
    return updated


def calculate_recommendation_quality(row: dict[str, Any], *, pump_risk_score: float | None = None) -> dict[str, Any]:
    confidence = _clamp(row.get("final_score") or row.get("Final Score"), default=50.0)
    entry_low = _safe_float(row.get("entry_zone_low") or row.get("Entry From"))
    entry_high = _safe_float(row.get("entry_zone_high") or row.get("Entry To"))
    stop = _safe_float(row.get("stop_loss") or row.get("Stop Loss"))
    rr = _safe_float(row.get("risk_reward") or row.get("Risk/Reward"))
    execution_realism = 80.0 if entry_low and entry_high and entry_low <= entry_high else 35.0
    if stop is None or (entry_low is not None and stop >= entry_low):
        execution_realism -= 25.0
    liquidity = _clamp(row.get("risk_liquidity_score") or row.get("Liquidity Score"), default=50.0)
    timing = 75.0
    validation = ((row.get("details") or {}).get("validation") or {}) if isinstance(row.get("details"), dict) else {}
    reasons = " ".join(str(item) for item in validation.get("no_trade_reasons", []))
    if "late signal" in reasons.lower():
        timing = 35.0
    risk_reward_score = _clamp((rr or 0.0) * 40.0, default=0.0)
    source_scores = [
        _safe_float(row.get("technical_score") or row.get("Technical Score")),
        _safe_float(row.get("telegram_score") or row.get("Telegram Score")),
        _safe_float(row.get("strategy_score") or row.get("Strategy Score")),
        _safe_float(row.get("news_score") or row.get("News Score")),
    ]
    source_scores = [value for value in source_scores if value is not None]
    source_confirmation = round(sum(source_scores) / len(source_scores), 2) if source_scores else 50.0
    pump = _clamp(pump_risk_score if pump_risk_score is not None else row.get("pump_risk_score"), default=0.0)
    final_quality = _clamp(
        confidence * 0.20
        + execution_realism * 0.18
        + liquidity * 0.15
        + timing * 0.15
        + risk_reward_score * 0.15
        + source_confirmation * 0.17
        - pump * 0.20,
        default=50.0,
    )
    grade = "A+" if final_quality >= 85 else "A" if final_quality >= 75 else "B" if final_quality >= 60 else "C" if final_quality >= 45 else "D"
    return {
        "confidence_score": confidence,
        "execution_realism_score": _clamp(execution_realism, default=50.0),
        "liquidity_score": liquidity,
        "timing_score": timing,
        "risk_reward_score": risk_reward_score,
        "source_confirmation_score": source_confirmation,
        "pump_risk_score": pump,
        "final_quality_score": final_quality,
        "quality_grade": grade,
    }


def store_recommendation_quality(db: Session, item: RecommendationItem, *, pump_risk_score: float | None = None) -> RecommendationQualitySnapshot:
    row = {
        "symbol": item.symbol,
        "final_score": item.final_score,
        "telegram_score": item.telegram_score,
        "technical_score": item.technical_score,
        "strategy_score": item.strategy_score,
        "news_score": item.news_score,
        "risk_liquidity_score": item.risk_liquidity_score,
        "entry_zone_low": item.entry_zone_low,
        "entry_zone_high": item.entry_zone_high,
        "stop_loss": item.stop_loss,
        "risk_reward": item.risk_reward,
        "details": item.details_json or {},
    }
    quality = calculate_recommendation_quality(row, pump_risk_score=pump_risk_score)
    existing = db.scalar(select(RecommendationQualitySnapshot).where(RecommendationQualitySnapshot.recommendation_item_id == item.id))
    snapshot = existing or RecommendationQualitySnapshot(recommendation_item_id=item.id, symbol=str(item.symbol or ""))
    for key, value in quality.items():
        setattr(snapshot, key, value)
    snapshot.details_json = quality
    if existing is None:
        db.add(snapshot)
    return snapshot


def compute_source_accuracy(db: Session, *, persist: bool = False, as_of: datetime | None = None) -> pd.DataFrame:
    as_of = _naive(as_of or datetime.now(UTC))
    rows = _filtered_evaluated(_evaluation_rows(db))
    specs = [
        ("technical", "technical_score"),
        ("financial", "financial_score"),
        ("news", "news_score"),
        ("telegram", "telegram_score"),
        ("strategy_1", "strategy_score"),
        ("strategy_2_cli_v6", "strategy_score"),
        ("combined_model", "combined_score"),
        ("ocr_chart_signals", "telegram_score"),
    ]
    output: list[dict[str, Any]] = []
    for source_name, score_key in specs:
        source_rows = [row for row in rows if row.get(score_key) is not None]
        if source_name == "strategy_2_cli_v6":
            source_rows = [row for row in source_rows if "cli" in str(row.get("strategy_source") or "").lower() or row.get("strategy_score") is not None]
        if source_name == "ocr_chart_signals":
            source_rows = [row for row in source_rows if "ocr" in str((row.get("details") or {})).lower() or row.get("telegram_score") is not None]
        positives = [row for row in source_rows if (_safe_float(row.get(score_key)) or 0.0) >= 60]
        evaluated = positives or source_rows
        wins = [row for row in evaluated if _score_from_status(row.get("status"), row.get("actual_return_pct"))]
        stops = [row for row in evaluated if row.get("status") == EVAL_STOP_HIT or row.get("stop_hit")]
        targets = [row for row in evaluated if row.get("status") == EVAL_TARGET_HIT or row.get("target_hit")]
        returns = [_safe_float(row.get("actual_return_pct")) for row in evaluated]
        returns = [value for value in returns if value is not None]
        best = sorted(evaluated, key=lambda row: _safe_float(row.get("actual_return_pct"), -999.0) or -999.0, reverse=True)[:5]
        worst = sorted(evaluated, key=lambda row: _safe_float(row.get("actual_return_pct"), 999.0) or 999.0)[:5]
        count = len(evaluated)
        win_rate = round(len(wins) / count * 100.0, 2) if count else None
        false_positive = round(len(stops) / count * 100.0, 2) if count else None
        avg_return = round(sum(returns) / len(returns), 2) if returns else None
        reliability = None
        if count:
            reliability = _clamp((win_rate or 0.0) * 0.55 + max(0.0, (avg_return or 0.0) + 5.0) * 4.0 - (false_positive or 0.0) * 0.25, default=0.0)
        record = {
            "Source": source_name,
            "Source Type": source_name,
            "Signals": len(source_rows),
            "Evaluated": count,
            "Win Rate %": win_rate,
            "Average Return %": avg_return,
            "False Positive Rate %": false_positive,
            "Target Hit Rate %": round(len(targets) / count * 100.0, 2) if count else None,
            "Stop Hit Rate %": round(len(stops) / count * 100.0, 2) if count else None,
            "Best Stocks": ", ".join(str(row.get("symbol")) for row in best[:3]),
            "Worst Stocks": ", ".join(str(row.get("symbol")) for row in worst[:3]),
            "Reliability Score": reliability,
            "Sample Warning": "Sample below 5; reliability is directional only." if count < 5 else "",
        }
        output.append(record)
        if persist:
            db.add(
                SourceAccuracySnapshot(
                    source_name=source_name,
                    source_type=source_name,
                    as_of=as_of,
                    signals_count=len(source_rows),
                    evaluated_count=count,
                    win_rate=win_rate,
                    average_return=avg_return,
                    false_positive_rate=false_positive,
                    target_hit_rate=record["Target Hit Rate %"],
                    stop_hit_rate=record["Stop Hit Rate %"],
                    best_stocks_json=[{"symbol": row.get("symbol"), "return": row.get("actual_return_pct")} for row in best],
                    worst_stocks_json=[{"symbol": row.get("symbol"), "return": row.get("actual_return_pct")} for row in worst],
                    reliability_score=reliability,
                    details_json=record,
                )
            )
    return pd.DataFrame(output)


def compute_risk_expectancy(db: Session, *, persist: bool = False, as_of: datetime | None = None) -> dict[str, Any]:
    as_of = _naive(as_of or datetime.now(UTC))
    all_rows = _evaluation_rows(db)
    evaluated = _filtered_evaluated(all_rows)
    returns = [_safe_float(row.get("actual_return_pct")) for row in evaluated]
    returns = [value for value in returns if value is not None]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    consecutive = current_losses = 0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if value <= 0:
            current_losses += 1
            consecutive = max(consecutive, current_losses)
        else:
            current_losses = 0
    target_count = sum(1 for row in evaluated if row.get("status") == EVAL_TARGET_HIT or row.get("target_hit"))
    stop_count = sum(1 for row in evaluated if row.get("status") == EVAL_STOP_HIT or row.get("stop_hit"))
    data_rows = [row for row in all_rows if row.get("status") not in {EVAL_NOT_EVALUATED, EVAL_DATA_MISSING}]
    entry_reached = [row for row in data_rows if row.get("status") != EVAL_ENTRY_NOT_REACHED]
    strategies: dict[str, list[float]] = {}
    for row in evaluated:
        key = str(row.get("strategy_source") or "combined_model")
        if row.get("actual_return_pct") is not None:
            strategies.setdefault(key, []).append(float(row["actual_return_pct"]))
    best_strategy = None
    if strategies:
        best_strategy = max(strategies.items(), key=lambda item: sum(item[1]) / max(1, len(item[1])))[0]
    result = {
        "Scope": "combined_model",
        "Evaluated Count": len(evaluated),
        "Average Win %": round(sum(wins) / len(wins), 2) if wins else None,
        "Average Loss %": round(sum(losses) / len(losses), 2) if losses else None,
        "Profit Factor": round(gross_win / gross_loss, 2) if gross_loss else (round(gross_win, 2) if gross_win else None),
        "Expected Value %": round(sum(returns) / len(returns), 2) if returns else None,
        "Max Drawdown %": round(max_drawdown, 2) if returns else None,
        "Consecutive Losses": consecutive,
        "Average Holding Days": round(sum(int(row.get("days_evaluated") or 0) for row in evaluated) / len(evaluated), 2) if evaluated else None,
        "Entry Reached Rate %": round(len(entry_reached) / len(data_rows) * 100.0, 2) if data_rows else None,
        "Target Hit Rate %": round(target_count / len(evaluated) * 100.0, 2) if evaluated else None,
        "Stop Hit Rate %": round(stop_count / len(evaluated) * 100.0, 2) if evaluated else None,
        "Risk/Reward Accuracy %": round(sum(1 for row in evaluated if (row.get("risk_reward") or 0) >= 1.8 and _score_from_status(row.get("status"), row.get("actual_return_pct"))) / len(evaluated) * 100.0, 2) if evaluated else None,
        "Best Strategy By Expectancy": best_strategy,
        "Sample Warning": "Expectancy is not reliable yet because evaluated sample size is below 5." if len(evaluated) < 5 else "",
    }
    if persist:
        db.add(
            RiskExpectancySnapshot(
                scope="combined_model",
                as_of=as_of,
                evaluated_count=len(evaluated),
                average_win=result["Average Win %"],
                average_loss=result["Average Loss %"],
                profit_factor=result["Profit Factor"],
                expected_value=result["Expected Value %"],
                max_drawdown=result["Max Drawdown %"],
                consecutive_losses=consecutive,
                average_holding_days=result["Average Holding Days"],
                entry_reached_rate=result["Entry Reached Rate %"],
                target_hit_rate=result["Target Hit Rate %"],
                stop_hit_rate=result["Stop Hit Rate %"],
                risk_reward_accuracy=result["Risk/Reward Accuracy %"],
                best_strategy_by_expectancy=best_strategy,
                details_json=result,
            )
        )
    return result


def run_walk_forward_validation(
    db: Session,
    *,
    train_days: int = 60,
    test_days: int = 20,
    min_trades: int = 1,
    persist: bool = False,
) -> dict[str, Any]:
    rows = _filtered_evaluated(_evaluation_rows(db))
    rows = [row for row in rows if row.get("recommendation_datetime")]
    if not rows:
        return {"run_id": None, "summary": {"periods": 0, "overfit_warning": "No evaluated recommendations."}, "periods": pd.DataFrame()}
    min_date = min(row["recommendation_datetime"] for row in rows).date()
    max_date = max(row["recommendation_datetime"] for row in rows).date()
    period_rows: list[dict[str, Any]] = []
    idx = 0
    cursor = min_date
    while cursor + timedelta(days=train_days + test_days) <= max_date + timedelta(days=1):
        train_start = cursor
        train_end = cursor + timedelta(days=train_days)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=test_days)
        train = [row for row in rows if train_start <= row["recommendation_datetime"].date() <= train_end]
        test = [row for row in rows if test_start <= row["recommendation_datetime"].date() <= test_end]
        if len(train) >= min_trades and len(test) >= min_trades:
            train_wins = sum(1 for row in train if _score_from_status(row.get("status"), row.get("actual_return_pct")))
            test_wins = sum(1 for row in test if _score_from_status(row.get("status"), row.get("actual_return_pct")))
            train_wr = round(train_wins / len(train) * 100.0, 2)
            test_wr = round(test_wins / len(test) * 100.0, 2)
            test_returns = [_safe_float(row.get("actual_return_pct"), 0.0) or 0.0 for row in test]
            decay = round(train_wr - test_wr, 2)
            period_rows.append(
                {
                    "Period": idx + 1,
                    "Train Start": train_start.isoformat(),
                    "Train End": train_end.isoformat(),
                    "Test Start": test_start.isoformat(),
                    "Test End": test_end.isoformat(),
                    "Train Trades": len(train),
                    "Test Trades": len(test),
                    "In-Sample Win Rate %": train_wr,
                    "Out-of-Sample Win Rate %": test_wr,
                    "Forward Return %": round(sum(test_returns) / len(test_returns), 2) if test_returns else None,
                    "Performance Decay %": decay,
                    "Overfit Flag": decay >= 20,
                }
            )
            idx += 1
        cursor = cursor + timedelta(days=test_days)
    df = pd.DataFrame(period_rows)
    avg_decay = round(float(df["Performance Decay %"].mean()), 2) if not df.empty else None
    warning = "Possible overfitting: out-of-sample win rate decays materially." if avg_decay is not None and avg_decay >= 15 else "No material decay detected yet."
    run_id = f"wf_{uuid.uuid4().hex[:12]}"
    if persist:
        run = WalkForwardRun(
            run_id=run_id,
            strategy_name="combined_model",
            status="success",
            finished_at=datetime.now(UTC).replace(tzinfo=None),
            periods_count=len(period_rows),
            performance_decay_pct=avg_decay,
            overfit_warning=warning,
            summary_json={"train_days": train_days, "test_days": test_days, "warning": warning},
        )
        db.add(run)
        db.flush()
        for row in period_rows:
            db.add(
                WalkForwardPeriod(
                    run_id=run_id,
                    period_index=int(row["Period"]),
                    train_start=datetime.fromisoformat(row["Train Start"]),
                    train_end=datetime.fromisoformat(row["Train End"]),
                    test_start=datetime.fromisoformat(row["Test Start"]),
                    test_end=datetime.fromisoformat(row["Test End"]),
                    in_sample_win_rate=row["In-Sample Win Rate %"],
                    out_of_sample_win_rate=row["Out-of-Sample Win Rate %"],
                    forward_return=row["Forward Return %"],
                    train_trades=row["Train Trades"],
                    test_trades=row["Test Trades"],
                    performance_decay_pct=row["Performance Decay %"],
                    overfit_flag=bool(row["Overfit Flag"]),
                    details_json=row,
                )
            )
    return {"run_id": run_id, "summary": {"periods": len(period_rows), "performance_decay_pct": avg_decay, "overfit_warning": warning}, "periods": df}


def run_intraday_rescan(
    db: Session,
    *,
    scan_type: str = "manual",
    scan_time: datetime | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    scan_time = _naive(scan_time or datetime.now(UTC))
    market = evaluate_daily_market(db, target_date=scan_time.date(), persist=False)
    run_id = f"scan_{uuid.uuid4().hex[:12]}"
    stocks = db.scalars(select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.symbol.asc())).all()
    latest_report = db.scalar(select(RecommendationReport).order_by(RecommendationReport.report_time.desc(), RecommendationReport.id.desc()))
    recommended_today: set[str] = set()
    if latest_report and latest_report.report_time.date() == scan_time.date():
        recommended_today = {
            str(symbol).upper()
            for symbol in db.scalars(select(RecommendationItem.symbol).where(RecommendationItem.report_id == latest_report.id)).all()
        }
    items: list[dict[str, Any]] = []
    for stock in stocks:
        frame = get_ohlcv(db, stock.symbol, timeframe="15m", limit=120)
        if frame.empty:
            frame = get_ohlcv(db, stock.symbol, timeframe="1D", limit=80)
        if frame.empty or len(frame) < 2:
            continue
        frame = frame.copy().reset_index(drop=True)
        frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
        past = frame[frame["datetime"] <= pd.Timestamp(scan_time)].dropna(subset=["datetime"]).sort_values("datetime")
        if len(past) < 2:
            continue
        latest = past.iloc[-1]
        prior = past.iloc[:-1]
        close = _safe_float(latest.get("close"))
        prev_close = _safe_float(prior.iloc[-1].get("close"))
        move_pct = ((close - prev_close) / prev_close * 100.0) if close is not None and prev_close else None
        avg_volume = float(prior.tail(20)["volume"].fillna(0).mean() or 0.0) if "volume" in prior else 0.0
        volume = _safe_float(latest.get("volume"), 0.0) or 0.0
        volume_change_pct = ((volume - avg_volume) / avg_volume * 100.0) if avg_volume > 0 else None
        event_types: list[str] = []
        if close is not None and len(prior) >= 20 and close > float(prior.tail(20)["high"].max()):
            event_types.append("NEW_BREAKOUT")
        if volume_change_pct is not None and volume_change_pct >= 75:
            event_types.append("SUDDEN_VOLUME_SPIKE")
        if move_pct is not None and move_pct >= 3:
            event_types.append("LATE_MOVER")
        if stock.symbol.upper() not in recommended_today and event_types:
            event_types.append("MOVED_WITHOUT_RECOMMENDATION")
        for event in event_types:
            item = {
                "Run ID": run_id,
                "Scan Type": scan_type,
                "Scan Time": scan_time.isoformat(sep=" "),
                "Stock Symbol": stock.symbol,
                "Event Type": event,
                "Price": _round(close),
                "Move %": _round(move_pct),
                "Volume Change %": _round(volume_change_pct),
                "Recommendation Status": "RECOMMENDED_TODAY" if stock.symbol.upper() in recommended_today else "NOT_RECOMMENDED_TODAY",
                "Reason": f"{event.replace('_', ' ').title()} detected using completed candles available before scan.",
            }
            items.append(item)
    if persist:
        run = IntradayScanRun(
            run_id=run_id,
            scan_type=scan_type,
            scan_time=scan_time,
            market_regime=market.get("market_regime"),
            status="success",
            symbols_scanned=len(stocks),
            alerts_count=len(items),
            summary_json={"market": market, "items_count": len(items)},
        )
        db.add(run)
        for row in items:
            db.add(
                IntradayScanItem(
                    run_id=run_id,
                    symbol=row["Stock Symbol"],
                    event_type=row["Event Type"],
                    price=row["Price"],
                    volume_change_pct=row["Volume Change %"],
                    move_pct=row["Move %"],
                    recommendation_status=row["Recommendation Status"],
                    reason=row["Reason"],
                    details_json=row,
                )
            )
    return {"run_id": run_id, "market": market, "items": pd.DataFrame(items)}


def diagnose_missed_opportunities(db: Session, *, target_date: date | None = None) -> pd.DataFrame:
    day = target_date or cairo_now().date()
    start, end = _day_bounds(day)
    report = db.scalar(
        select(EndOfDayReviewReport)
        .where(EndOfDayReviewReport.review_date >= start, EndOfDayReviewReport.review_date < end)
        .order_by(EndOfDayReviewReport.created_at.desc(), EndOfDayReviewReport.id.desc())
    )
    rows = []
    if report:
        items = db.scalars(
            select(EndOfDayReviewItem)
            .where(
                EndOfDayReviewItem.report_id == report.id,
                EndOfDayReviewItem.row_type.in_(["MISSED_OPPORTUNITY", "WHY_NOT_SELECTED"]),
            )
            .order_by(EndOfDayReviewItem.actual_return_pct.desc(), EndOfDayReviewItem.id.asc())
        ).all()
        rows = [item.details_json or {} for item in items]
    enriched = pd.DataFrame(rows)
    if enriched.empty:
        return pd.DataFrame([{"Status": "No persisted missed-opportunity review for this date yet. Run the end-of-day review to populate it."}])
    enriched["Filter Correct?"] = enriched["Why Not Selected Code"].apply(lambda value: "Review" if value in {"FILTER_TOO_STRICT", "LATE_BREAKOUT", "STRATEGY_NOT_COVERED"} else "Likely Correct")
    enriched["Intraday Scan Would Catch?"] = enriched["Why Not Selected Code"].apply(lambda value: "Yes" if value in {"LATE_BREAKOUT", "FILTER_TOO_STRICT"} else "Maybe")
    enriched["Telegram/News Early?"] = enriched.apply(lambda row: "Yes" if (_safe_float(row.get("Telegram Score")) or 0) >= 60 or (_safe_float(row.get("News Score")) or 0) >= 60 else "No", axis=1)
    return enriched


def build_strategy_learning_report(
    db: Session,
    *,
    target_date: date | None = None,
    missed_df: pd.DataFrame | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    day = target_date or cairo_now().date()
    source_accuracy = compute_source_accuracy(db, persist=False)
    missed = missed_df.copy() if isinstance(missed_df, pd.DataFrame) else diagnose_missed_opportunities(db, target_date=day)
    expectancy = compute_risk_expectancy(db, persist=False)
    filters_blocked_good = []
    filters_allowed_bad = []
    if not missed.empty and "Why Not Selected Code" in missed.columns:
        for code, count in missed["Why Not Selected Code"].value_counts().head(5).items():
            filters_blocked_good.append({"Filter": code, "Count": int(count), "Suggestion": "Review threshold; do not auto-change."})
    rows = _filtered_evaluated(_evaluation_rows(db))
    for row in rows:
        if row.get("status") == EVAL_STOP_HIT:
            filters_allowed_bad.append({"Symbol": row.get("symbol"), "Stage": row.get("stage"), "Issue": "Allowed recommendation later hit stop."})
    accurate_sources = []
    misleading_sources = []
    if not source_accuracy.empty:
        for row in source_accuracy.to_dict("records"):
            reliability = _safe_float(row.get("Reliability Score"), 0.0) or 0.0
            target = accurate_sources if reliability >= 60 else misleading_sources
            target.append({"Source": row.get("Source"), "Reliability Score": reliability, "Evaluated": row.get("Evaluated")})
    suggested_weight_changes = [
        {
            "Source": row["Source"],
            "Current Action": "No automatic change",
            "Suggested Review": "Consider modest increase tomorrow" if (_safe_float(row.get("Reliability Score"), 0.0) or 0.0) >= 70 else "Do not increase yet",
            "Auto Applied": "No",
        }
        for row in source_accuracy.to_dict("records")
    ] if not source_accuracy.empty else []
    suggested_rules = [
        {"Rule": "Intraday momentum rescan", "Suggestion": "Run scans after open, mid-session, before close, and after close.", "Auto Applied": "No"},
        {"Rule": "Pump risk", "Suggestion": "Keep high Telegram-only excitement as WATCH unless technical/liquidity confirmation exists.", "Auto Applied": "No"},
        {"Rule": "Sample size", "Suggestion": "Do not tune weights until at least 5 evaluated recommendations exist.", "Auto Applied": "No"},
    ]
    summary = (
        "Learning report generated in advisory mode only. "
        f"Evaluated recommendations: {expectancy.get('Evaluated Count', 0)}. "
        "No strategy weights were changed automatically."
    )
    payload = {
        "report_date": day.isoformat(),
        "filters_helped": pd.DataFrame([{"Filter": "ENTRY_NOT_REACHED exclusion", "Effect": "Prevents fake win/loss accuracy."}]),
        "filters_blocked_good": pd.DataFrame(filters_blocked_good or [{"Status": "No blocked-good filters identified yet."}]),
        "filters_allowed_bad": pd.DataFrame(filters_allowed_bad or [{"Status": "No bad allowed recommendations identified yet."}]),
        "accurate_sources": pd.DataFrame(accurate_sources or [{"Status": "No reliable source ranking yet."}]),
        "misleading_sources": pd.DataFrame(misleading_sources or [{"Status": "No misleading source ranking yet."}]),
        "suggested_weight_changes": pd.DataFrame(suggested_weight_changes or [{"Status": "No weight suggestion until evaluated sample is larger.", "Auto Applied": "No"}]),
        "suggested_rules": pd.DataFrame(suggested_rules),
        "summary": summary,
    }
    if persist:
        start, end = _day_bounds(day)
        existing = db.scalar(select(StrategyLearningReport).where(StrategyLearningReport.report_date >= start, StrategyLearningReport.report_date < end))
        if existing:
            report = existing
        else:
            report = StrategyLearningReport(report_date=start)
            db.add(report)
        report.filters_helped_json = payload["filters_helped"].to_dict("records")
        report.filters_blocked_good_json = payload["filters_blocked_good"].to_dict("records")
        report.filters_allowed_bad_json = payload["filters_allowed_bad"].to_dict("records")
        report.accurate_sources_json = payload["accurate_sources"].to_dict("records")
        report.misleading_sources_json = payload["misleading_sources"].to_dict("records")
        report.suggested_weight_changes_json = payload["suggested_weight_changes"].to_dict("records")
        report.suggested_rules_json = payload["suggested_rules"].to_dict("records")
        report.auto_applied = False
        report.summary = summary
        report.details_json = {"risk_note": RISK_NOTE}
    return payload


def build_learning_payload(
    db: Session,
    *,
    target_date: date | None = None,
    missed_df: pd.DataFrame | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    day = target_date or cairo_now().date()
    as_of = datetime(day.year, day.month, day.day, 21, 0, 0)
    source_accuracy = compute_source_accuracy(db, persist=persist, as_of=as_of)
    expectancy = compute_risk_expectancy(db, persist=persist, as_of=as_of)
    walk_forward = run_walk_forward_validation(db, persist=persist)
    if persist:
        intraday_df = run_intraday_rescan(db, scan_type="end_of_day", scan_time=as_of, persist=True).get("items", pd.DataFrame())
    else:
        latest_scan = db.scalar(select(IntradayScanRun).order_by(IntradayScanRun.scan_time.desc(), IntradayScanRun.id.desc()))
        if latest_scan:
            scan_items = db.scalars(
                select(IntradayScanItem).where(IntradayScanItem.run_id == latest_scan.run_id).order_by(IntradayScanItem.created_at.desc()).limit(250)
            ).all()
            intraday_df = pd.DataFrame([item.details_json or {} for item in scan_items])
        else:
            intraday_df = pd.DataFrame([{"Status": "No persisted intraday learning scan yet. Use the Intraday Scanner page or scheduler."}])
    missed = missed_df.copy() if isinstance(missed_df, pd.DataFrame) else diagnose_missed_opportunities(db, target_date=day)
    learning = build_strategy_learning_report(db, target_date=day, missed_df=missed, persist=persist)
    latest_report = db.scalar(select(RecommendationReport).order_by(RecommendationReport.report_time.desc(), RecommendationReport.id.desc()))
    quality_rows: list[dict[str, Any]] = []
    pump_rows: list[dict[str, Any]] = []
    decision_snapshot_rows: list[dict[str, Any]] = []
    if latest_report:
        items = db.scalars(select(RecommendationItem).where(RecommendationItem.report_id == latest_report.id).order_by(RecommendationItem.final_score.desc())).all()
        for item in items:
            pump = calculate_pump_risk_for_row(
                db,
                {
                    "symbol": item.symbol,
                    "technical_score": item.technical_score,
                    "financial_score": (item.details_json or {}).get("financial_score"),
                    "risk_liquidity_score": item.risk_liquidity_score,
                },
                now=latest_report.report_time,
                persist=persist,
            )
            quality = store_recommendation_quality(db, item, pump_risk_score=pump.get("pump_risk_score")) if persist else None
            q_dict = calculate_recommendation_quality(
                {
                    "symbol": item.symbol,
                    "final_score": item.final_score,
                    "telegram_score": item.telegram_score,
                    "technical_score": item.technical_score,
                    "strategy_score": item.strategy_score,
                    "news_score": item.news_score,
                    "risk_liquidity_score": item.risk_liquidity_score,
                    "entry_zone_low": item.entry_zone_low,
                    "entry_zone_high": item.entry_zone_high,
                    "stop_loss": item.stop_loss,
                    "risk_reward": item.risk_reward,
                    "details": item.details_json or {},
                },
                pump_risk_score=pump.get("pump_risk_score"),
            )
            quality_rows.append({"Stock Symbol": item.symbol, **q_dict})
            pump_rows.append({"Stock Symbol": item.symbol, **pump})
            snapshot = create_decision_snapshot(db, item, recommendation_time=latest_report.report_time) if persist else None
            if snapshot:
                decision_snapshot_rows.append(
                    {
                        "Stock Symbol": snapshot.symbol,
                        "Snapshot Time": snapshot.snapshot_time,
                        "Decision": snapshot.decision,
                        "Final Score": snapshot.final_score,
                        "Failed Filters": ", ".join(snapshot.failed_filters_json or []),
                    }
                )
    return {
        "source_accuracy": source_accuracy,
        "risk_expectancy": pd.DataFrame([expectancy]),
        "walk_forward_summary": walk_forward.get("summary", {}),
        "walk_forward_periods": walk_forward.get("periods", pd.DataFrame()),
        "intraday_scan": intraday_df,
        "missed_opportunity_diagnosis": missed,
        "strategy_learning": learning,
        "recommendation_quality": pd.DataFrame(quality_rows),
        "pump_risk_monitor": pd.DataFrame(pump_rows),
        "decision_snapshots": pd.DataFrame(decision_snapshot_rows),
    }


def format_learning_telegram_block(payload: dict[str, Any]) -> str:
    source_df = payload.get("source_accuracy")
    expectancy_df = payload.get("risk_expectancy")
    missed_df = payload.get("missed_opportunity_diagnosis")
    learning = payload.get("strategy_learning") or {}
    lines = [
        "Learning & Accuracy Review",
        "Mode: AUDIT/PAPER ONLY - live trading disabled.",
    ]
    if isinstance(source_df, pd.DataFrame) and not source_df.empty and "Reliability Score" in source_df.columns:
        ranked = source_df.copy()
        ranked["_score"] = pd.to_numeric(ranked["Reliability Score"], errors="coerce")
        ranked = ranked.dropna(subset=["_score"]).sort_values("_score", ascending=False)
        if not ranked.empty:
            lines.append(f"Best source today: {ranked.iloc[0].get('Source')} ({ranked.iloc[0].get('Reliability Score')})")
            lines.append(f"Worst source today: {ranked.iloc[-1].get('Source')} ({ranked.iloc[-1].get('Reliability Score')})")
    if isinstance(expectancy_df, pd.DataFrame) and not expectancy_df.empty:
        row = expectancy_df.iloc[0].to_dict()
        lines.append(
            f"Expectancy: EV {row.get('Expected Value %')}%, PF {row.get('Profit Factor')}, Max DD {row.get('Max Drawdown %')}%, Entry reached {row.get('Entry Reached Rate %')}%"
        )
        if row.get("Sample Warning"):
            lines.append(str(row["Sample Warning"]))
    if isinstance(missed_df, pd.DataFrame) and not missed_df.empty and "Stock Symbol" in missed_df.columns:
        top = missed_df.head(5)
        lines.append("Top missed opportunities:")
        for row in top.to_dict("records"):
            lines.append(f"- {row.get('Stock Symbol')}: {row.get('Today Return %')}% | {row.get('Why Not Selected Code')} | {row.get('Suggested Fix')}")
    suggestions = learning.get("suggested_rules")
    if isinstance(suggestions, pd.DataFrame) and not suggestions.empty:
        lines.append("Tomorrow improvement suggestions:")
        for row in suggestions.head(3).to_dict("records"):
            lines.append(f"- {row.get('Rule')}: {row.get('Suggestion')} (Auto Applied: {row.get('Auto Applied')})")
    lines.append(f"Risk Note: {RISK_NOTE}")
    return "\n".join(lines)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Run EGX accuracy learning system.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    target = cairo_now().date() if args.date == "today" else date.fromisoformat(args.date)
    init_db(seed=True)
    with sqlite_write_lock():
        with SessionLocal() as db:
            payload = build_learning_payload(db, target_date=target, persist=args.persist)
            if args.persist:
                db.commit()
    if args.json:
        printable = {
            key: (value.to_dict("records") if isinstance(value, pd.DataFrame) else value)
            for key, value in payload.items()
            if key != "strategy_learning"
        }
        print(json.dumps(printable, ensure_ascii=False, default=str, indent=2))
    else:
        print(format_learning_telegram_block(payload))


if __name__ == "__main__":
    _cli()
