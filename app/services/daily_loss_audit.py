from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import MAX_DISTANCE_FROM_ENTRY_PCT, REPORT_TIMEZONE, RISK_NOTE
from app.data.market_data import get_ohlcv, latest_price
from app.database import SessionLocal, init_db, sqlite_write_lock
from app.models import (
    DailyLossAuditItem,
    DailyLossAuditReport,
    MarketPrice,
    OHLCVData,
    PortfolioTrade,
    RecommendationItem,
    RecommendationReport,
)
from app.services.market_daily_evaluation import evaluate_daily_market


CAIRO_TZ = ZoneInfo(REPORT_TIMEZONE)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = PROJECT_ROOT / "data" / "audits"


GOOD_CALL = "GOOD_CALL"
BAD_CALL = "BAD_CALL"
NO_ENTRY = "NO_ENTRY"
BAD_ENTRY = "BAD_ENTRY"
LATE_SIGNAL = "LATE_SIGNAL"
LOW_LIQUIDITY = "LOW_LIQUIDITY"
CONFLICTED_SIGNAL = "CONFLICTED_SIGNAL"
DATA_PROBLEM = "DATA_PROBLEM"
RISK_PROBLEM = "RISK_PROBLEM"
OPEN_PROFIT = "OPEN_PROFIT"
OPEN_LOSS = "OPEN_LOSS"
OPEN_FLAT = "OPEN_FLAT"

HIGH_INTRADAY = "HIGH_INTRADAY"
MEDIUM_DAILY = "MEDIUM_DAILY"
LOW_MISSING_DATA = "LOW_MISSING_DATA"
NOT_EVALUATED = "NOT_EVALUATED"

EVAL_NOT_EVALUATED = "NOT_EVALUATED"
EVAL_EVALUATED = "EVALUATED"
EVAL_TARGET_HIT = "TARGET_HIT"
EVAL_STOP_HIT = "STOP_HIT"
EVAL_ENTRY_NOT_REACHED = "ENTRY_NOT_REACHED"
EVAL_EXPIRED = "EXPIRED"
EVAL_DATA_MISSING = "DATA_MISSING"

QUALITY_EXCELLENT = "Excellent"
QUALITY_GOOD = "Good"
QUALITY_WEAK = "Weak"
QUALITY_BAD = "Bad"
QUALITY_NOT_EVALUATED = "Not Evaluated"


@dataclass
class PathAudit:
    evaluation_status: str
    result: str
    entry_touched: bool
    stop_loss_hit: bool
    target_1_hit: bool
    target_2_hit: bool
    target_3_hit: bool
    actual_entry_price: float | None
    signal_price: float | None
    next_available_open: float | None
    close_after_recommendation: float | None
    max_drawdown_after_entry: float | None
    max_profit_after_entry: float | None
    max_favorable_move_pct: float | None
    max_adverse_move_pct: float | None
    actual_return: float | None
    estimated_pnl: float | None
    max_price_after_signal: float | None
    min_price_after_signal: float | None
    time_to_target_minutes: float | None
    time_to_stop_minutes: float | None
    days_evaluated: int
    evaluation_quality: str
    final_quality: str
    root_cause: str
    mistake_type: str
    fix_required: str
    priority: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_status": self.evaluation_status,
            "result": self.result,
            "entry_touched": self.entry_touched,
            "stop_loss_hit": self.stop_loss_hit,
            "target_1_hit": self.target_1_hit,
            "target_2_hit": self.target_2_hit,
            "target_3_hit": self.target_3_hit,
            "actual_entry_price": self.actual_entry_price,
            "signal_price": self.signal_price,
            "next_available_open": self.next_available_open,
            "close_after_recommendation": self.close_after_recommendation,
            "max_drawdown_after_entry": self.max_drawdown_after_entry,
            "max_profit_after_entry": self.max_profit_after_entry,
            "max_favorable_move_pct": self.max_favorable_move_pct,
            "max_adverse_move_pct": self.max_adverse_move_pct,
            "actual_return": self.actual_return,
            "estimated_pnl": self.estimated_pnl,
            "max_price_after_signal": self.max_price_after_signal,
            "min_price_after_signal": self.min_price_after_signal,
            "time_to_target_minutes": self.time_to_target_minutes,
            "time_to_stop_minutes": self.time_to_stop_minutes,
            "days_evaluated": self.days_evaluated,
            "evaluation_quality": self.evaluation_quality,
            "final_quality": self.final_quality,
            "root_cause": self.root_cause,
            "mistake_type": self.mistake_type,
            "fix_required": self.fix_required,
            "priority": self.priority,
            "details": self.details,
        }


def _build_path_audit(**kwargs: Any) -> PathAudit:
    """Create a PathAudit with safe defaults for optional comparison fields."""
    kwargs.setdefault("evaluation_status", EVAL_NOT_EVALUATED)
    kwargs.setdefault("signal_price", None)
    kwargs.setdefault("next_available_open", None)
    kwargs.setdefault("max_favorable_move_pct", None)
    kwargs.setdefault("max_adverse_move_pct", None)
    kwargs.setdefault("days_evaluated", 0)
    kwargs.setdefault("final_quality", QUALITY_NOT_EVALUATED)
    return PathAudit(**kwargs)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if pd.isna(number):
        return None
    return number


def parse_audit_date(value: str | None) -> date:
    if not value or value.lower() == "today":
        return datetime.now(CAIRO_TZ).date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def day_bounds(target_date: date) -> tuple[datetime, datetime]:
    start = datetime(target_date.year, target_date.month, target_date.day)
    return start, start + timedelta(days=1)


def reports_for_date(db: Session, target_date: date) -> list[RecommendationReport]:
    start, end = day_bounds(target_date)
    rows = db.scalars(
        select(RecommendationReport)
        .where(RecommendationReport.report_time >= start, RecommendationReport.report_time < end)
        .order_by(RecommendationReport.report_time.asc(), RecommendationReport.id.asc())
    ).all()
    if not rows:
        rows = db.scalars(
        select(RecommendationReport)
        .where(RecommendationReport.created_at >= start, RecommendationReport.created_at < end)
        .order_by(RecommendationReport.created_at.asc(), RecommendationReport.id.asc())
        ).all()
    latest_by_type: dict[str, RecommendationReport] = {}
    for row in rows:
        key = row.report_type or "unknown"
        current = latest_by_type.get(key)
        if current is None or (row.created_at or row.report_time) >= (current.created_at or current.report_time):
            latest_by_type[key] = row
    return [latest_by_type[key] for key in sorted(latest_by_type)]


def _candles_after_recommendation(db: Session, symbol: str, report_time: datetime, target_date: date) -> tuple[pd.DataFrame, str, str]:
    for timeframe in ["15m", "30m", "1h", "4h"]:
        frame = get_ohlcv(db, symbol, timeframe=timeframe, limit=1500)
        if frame.empty:
            continue
        frame = frame.copy().reset_index(drop=True)
        frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
        after = frame[frame["datetime"] > pd.Timestamp(report_time)]
        if not after.empty:
            return after.sort_values("datetime"), HIGH_INTRADAY, timeframe

    frame = get_ohlcv(db, symbol, timeframe="1D", limit=700)
    if frame.empty:
        return frame, LOW_MISSING_DATA, "none"
    frame = frame.copy().reset_index(drop=True)
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    after = frame[frame["datetime"].dt.date > target_date]
    if after.empty:
        return after, NOT_EVALUATED, "none"
    return after.sort_values("datetime"), MEDIUM_DAILY, "1D"


def _first_non_null(*values: Any) -> float | None:
    for value in values:
        number = _safe_float(value)
        if number is not None:
            return number
    return None


def _days_evaluated(candles: pd.DataFrame) -> int:
    if candles is None or candles.empty or "datetime" not in candles.columns:
        return 0
    dates = pd.to_datetime(candles["datetime"], errors="coerce").dropna().dt.date
    return int(dates.nunique())


def _move_pct(value: float | None, base: float | None) -> float | None:
    if value is None or base in {None, 0}:
        return None
    return round(((value - base) / base) * 100.0, 2)


def _status_quality(
    *,
    status: str,
    actual_return: float | None,
    max_favorable_move_pct: float | None,
    max_adverse_move_pct: float | None,
) -> str:
    if status in {EVAL_NOT_EVALUATED, EVAL_DATA_MISSING, EVAL_ENTRY_NOT_REACHED}:
        return QUALITY_NOT_EVALUATED
    if status == EVAL_TARGET_HIT:
        return QUALITY_EXCELLENT
    if status == EVAL_STOP_HIT:
        return QUALITY_BAD
    if actual_return is not None and actual_return >= 2.0:
        return QUALITY_GOOD
    if actual_return is not None and actual_return <= -1.0:
        return QUALITY_BAD
    if max_favorable_move_pct is not None and max_favorable_move_pct >= 1.0:
        return QUALITY_GOOD
    if max_adverse_move_pct is not None and max_adverse_move_pct <= -1.0:
        return QUALITY_WEAK
    return QUALITY_WEAK


def _latest_signal_price_before(db: Session, symbol: str, report_time: datetime) -> float | None:
    market_price = db.scalar(
        select(MarketPrice.close)
        .where(MarketPrice.symbol == symbol, MarketPrice.close.is_not(None), MarketPrice.timestamp <= report_time)
        .order_by(MarketPrice.timestamp.desc(), MarketPrice.id.desc())
    )
    if market_price is not None:
        return float(market_price)
    ohlcv_price = db.scalar(
        select(OHLCVData.close)
        .where(OHLCVData.symbol == symbol, OHLCVData.close.is_not(None), OHLCVData.datetime <= report_time)
        .order_by(OHLCVData.datetime.desc(), OHLCVData.id.desc())
    )
    if ohlcv_price is not None:
        return float(ohlcv_price)
    return None


def _evaluation_status(
    *,
    result: str,
    target_hit: bool,
    stop_hit: bool,
    days_evaluated: int,
    evaluation_quality: str,
) -> str:
    if evaluation_quality == LOW_MISSING_DATA:
        return EVAL_DATA_MISSING
    if evaluation_quality == NOT_EVALUATED:
        return EVAL_NOT_EVALUATED
    if target_hit:
        return EVAL_TARGET_HIT
    if stop_hit:
        return EVAL_STOP_HIT
    if result in {NO_ENTRY, LATE_SIGNAL}:
        return EVAL_ENTRY_NOT_REACHED
    return EVAL_EVALUATED


def _component_problem(item: RecommendationItem, audit: PathAudit) -> tuple[str, str]:
    telegram = _safe_float(item.telegram_score) or 50.0
    technical = _safe_float(item.technical_score) or 50.0
    strategy = _safe_float(item.strategy_score) or 50.0
    news = _safe_float(item.news_score) or 50.0
    backtest = _safe_float(item.backtest_score) or 50.0
    liquidity = _safe_float(item.risk_liquidity_score) or 50.0
    rr = _safe_float(item.risk_reward)

    if audit.result == DATA_PROBLEM:
        return "Data", "DATA_ERROR"
    if audit.result == NO_ENTRY:
        return "Entry zone", "BAD_ENTRY"
    if audit.result == LATE_SIGNAL:
        return "Timing", "LATE_ENTRY"
    if liquidity < 60:
        return "Liquidity/risk", "LOW_LIQUIDITY"
    if rr is None or rr < 1.8:
        return "Risk/reward", "POSITION_SIZE_TOO_BIG"
    if telegram >= 75 and technical < 70:
        return "Telegram sentiment", "TELEGRAM_HYPE"
    if technical < 70:
        return "Technical analysis", "BAD_SIGNAL"
    if strategy < 65:
        return "Strategy confirmation", "BAD_SIGNAL"
    if backtest < 60:
        return "Backtest realism", "BACKTEST_OVERFIT"
    if news < 40:
        return "News risk", "NEWS_RISK"
    return "Risk management", "BAD_SIGNAL"


def classify_recommendation_path(
    candles: pd.DataFrame,
    *,
    entry_zone_low: float | None,
    entry_zone_high: float | None,
    stop_loss: float | None,
    target_1: float | None,
    target_2: float | None,
    target_3: float | None,
    signal: str | None = None,
    evaluation_quality: str = MEDIUM_DAILY,
    signal_price: float | None = None,
) -> PathAudit:
    if candles is None or candles.empty:
        status = EVAL_DATA_MISSING if evaluation_quality == LOW_MISSING_DATA else EVAL_NOT_EVALUATED
        reason = (
            "No OHLCV candles were available for this symbol."
            if status == EVAL_DATA_MISSING
            else "No future candle exists after the recommendation timestamp yet."
        )
        return _build_path_audit(
            evaluation_status=status,
            result=DATA_PROBLEM,
            entry_touched=False,
            stop_loss_hit=False,
            target_1_hit=False,
            target_2_hit=False,
            target_3_hit=False,
            actual_entry_price=None,
            close_after_recommendation=None,
            max_drawdown_after_entry=None,
            max_profit_after_entry=None,
            actual_return=None,
            estimated_pnl=None,
            max_price_after_signal=None,
            min_price_after_signal=None,
            time_to_target_minutes=None,
            time_to_stop_minutes=None,
            evaluation_quality=evaluation_quality,
            root_cause=reason,
            mistake_type="DATA_ERROR",
            fix_required="Refresh/import real OHLCV data before trusting the recommendation.",
            priority="P0",
            details={"candles": 0},
        )

    entry_low = _safe_float(entry_zone_low)
    entry_high = _safe_float(entry_zone_high)
    stop = _safe_float(stop_loss)
    t1 = _safe_float(target_1)
    t2 = _safe_float(target_2)
    t3 = _safe_float(target_3)
    close_after = _safe_float(candles.iloc[-1].get("close"))
    highs_all = [_safe_float(row.get("high")) for _, row in candles.iterrows()]
    lows_all = [_safe_float(row.get("low")) for _, row in candles.iterrows()]
    highs_all = [value for value in highs_all if value is not None]
    lows_all = [value for value in lows_all if value is not None]
    max_price_after_signal = max(highs_all) if highs_all else None
    min_price_after_signal = min(lows_all) if lows_all else None
    next_available_open = _safe_float(candles.iloc[0].get("open"))
    first_close = _safe_float(candles.iloc[0].get("close"))
    base_signal_price = _first_non_null(signal_price, next_available_open, first_close, entry_high)
    days_evaluated = _days_evaluated(candles)
    max_favorable_move_pct = _move_pct(max_price_after_signal, base_signal_price)
    max_adverse_move_pct = _move_pct(min_price_after_signal, base_signal_price)
    signal_return = (
        ((close_after - base_signal_price) / base_signal_price) * 100
        if close_after is not None and base_signal_price not in {None, 0}
        else None
    )

    if entry_low is None or entry_high is None or entry_low <= 0 or entry_high <= 0 or entry_low > entry_high:
        return _build_path_audit(
            evaluation_status=EVAL_EVALUATED,
            result=BAD_ENTRY,
            entry_touched=False,
            stop_loss_hit=False,
            target_1_hit=False,
            target_2_hit=False,
            target_3_hit=False,
            actual_entry_price=None,
            signal_price=round(base_signal_price, 4) if base_signal_price is not None else None,
            next_available_open=round(next_available_open, 4) if next_available_open is not None else None,
            close_after_recommendation=close_after,
            max_drawdown_after_entry=None,
            max_profit_after_entry=None,
            max_favorable_move_pct=max_favorable_move_pct,
            max_adverse_move_pct=max_adverse_move_pct,
            actual_return=None,
            estimated_pnl=None,
            max_price_after_signal=round(max_price_after_signal, 4) if max_price_after_signal is not None else None,
            min_price_after_signal=round(min_price_after_signal, 4) if min_price_after_signal is not None else None,
            time_to_target_minutes=None,
            time_to_stop_minutes=None,
            days_evaluated=days_evaluated,
            evaluation_quality=evaluation_quality,
            final_quality=QUALITY_BAD,
            root_cause="Entry zone was invalid or missing.",
            mistake_type="BAD_ENTRY",
            fix_required="Downgrade to WATCH ONLY unless a valid entry zone exists.",
            priority="P0",
            details={"entry_zone_low": entry_low, "entry_zone_high": entry_high},
        )

    if stop is None or stop <= 0 or stop >= entry_low:
        return _build_path_audit(
            evaluation_status=EVAL_EVALUATED,
            result=RISK_PROBLEM,
            entry_touched=False,
            stop_loss_hit=False,
            target_1_hit=False,
            target_2_hit=False,
            target_3_hit=False,
            actual_entry_price=None,
            signal_price=round(base_signal_price, 4) if base_signal_price is not None else None,
            next_available_open=round(next_available_open, 4) if next_available_open is not None else None,
            close_after_recommendation=close_after,
            max_drawdown_after_entry=None,
            max_profit_after_entry=None,
            max_favorable_move_pct=max_favorable_move_pct,
            max_adverse_move_pct=max_adverse_move_pct,
            actual_return=None,
            estimated_pnl=None,
            max_price_after_signal=round(max_price_after_signal, 4) if max_price_after_signal is not None else None,
            min_price_after_signal=round(min_price_after_signal, 4) if min_price_after_signal is not None else None,
            time_to_target_minutes=None,
            time_to_stop_minutes=None,
            days_evaluated=days_evaluated,
            evaluation_quality=evaluation_quality,
            final_quality=QUALITY_BAD,
            root_cause="Stop loss was invalid, missing, or above the entry zone.",
            mistake_type="NO_STOP_LOSS",
            fix_required="Block BUY until stop loss is below the entry zone.",
            priority="P0",
            details={"stop_loss": stop, "entry_zone_low": entry_low},
        )

    entered = False
    entry_price = entry_high
    lows_after_entry: list[float] = []
    highs_after_entry: list[float] = []
    stop_hit = False
    target1_hit = False
    target2_hit = False
    target3_hit = False
    result = OPEN_FLAT
    event_time: str | None = None
    first_event_time: Any = None
    entry_time: Any = None
    target_time: Any = None
    stop_time: Any = None
    event_reason = "Recommendation remains unresolved."
    if first_close is not None and first_close > entry_high * (1 + MAX_DISTANCE_FROM_ENTRY_PCT):
        late_candidate = True
    else:
        late_candidate = False

    for _, row in candles.iterrows():
        high = _safe_float(row.get("high"))
        low = _safe_float(row.get("low"))
        close = _safe_float(row.get("close"))
        when = row.get("datetime")
        if high is None or low is None:
            continue
        if not entered:
            if low <= entry_high and high >= entry_low:
                entered = True
                entry_price = max(entry_low, min(entry_high, close or entry_high))
                entry_time = when
            else:
                continue

        lows_after_entry.append(low)
        highs_after_entry.append(high)
        stop_now = low <= stop
        t1_now = t1 is not None and high >= t1
        t2_now = t2 is not None and high >= t2
        t3_now = t3 is not None and high >= t3
        stop_hit = stop_hit or stop_now
        target1_hit = target1_hit or t1_now
        target2_hit = target2_hit or t2_now
        target3_hit = target3_hit or t3_now

        if stop_now:
            stop_time = when
            first_event_time = first_event_time or when
            result = BAD_CALL
            event_time = str(when)
            event_reason = "Stop loss hit after entry. If target and stop both appear in one candle, stop is assumed first."
            break
        if t1_now or t2_now or t3_now:
            target_time = when
            first_event_time = first_event_time or when
            result = GOOD_CALL
            event_time = str(when)
            event_reason = "Target reached before stop loss."
            break

    if not entered:
        result = LATE_SIGNAL if late_candidate else NO_ENTRY
        reason = (
            "Entry was not reached because the signal was late and price was already too far above the entry zone."
            if late_candidate
            else "Price never touched the entry zone after the recommendation; setup remains unfilled, not a win or loss."
        )
        status = _evaluation_status(
            result=result,
            target_hit=False,
            stop_hit=False,
            days_evaluated=days_evaluated,
            evaluation_quality=evaluation_quality,
        )
        final_quality = _status_quality(
            status=status,
            actual_return=round(signal_return, 2) if signal_return is not None else None,
            max_favorable_move_pct=max_favorable_move_pct,
            max_adverse_move_pct=max_adverse_move_pct,
        )
        return _build_path_audit(
            evaluation_status=status,
            result=result,
            entry_touched=False,
            stop_loss_hit=False,
            target_1_hit=False,
            target_2_hit=False,
            target_3_hit=False,
            actual_entry_price=None,
            signal_price=round(base_signal_price, 4) if base_signal_price is not None else None,
            next_available_open=round(next_available_open, 4) if next_available_open is not None else None,
            close_after_recommendation=close_after,
            max_drawdown_after_entry=None,
            max_profit_after_entry=None,
            max_favorable_move_pct=max_favorable_move_pct,
            max_adverse_move_pct=max_adverse_move_pct,
            actual_return=round(signal_return, 2) if signal_return is not None else None,
            estimated_pnl=0.0,
            max_price_after_signal=round(max_price_after_signal, 4) if max_price_after_signal is not None else None,
            min_price_after_signal=round(min_price_after_signal, 4) if min_price_after_signal is not None else None,
            time_to_target_minutes=None,
            time_to_stop_minutes=None,
            days_evaluated=days_evaluated,
            evaluation_quality=evaluation_quality,
            final_quality=final_quality,
            root_cause=reason,
            mistake_type="LATE_ENTRY" if late_candidate else "BAD_ENTRY",
            fix_required="Review whether entry zone is too conservative or unrealistic; keep as no-trade until price reaches the zone.",
            priority="P1",
            details={"signal": signal, "late_candidate": late_candidate},
        )

    min_low = min(lows_after_entry) if lows_after_entry else entry_price
    max_high = max(highs_after_entry) if highs_after_entry else entry_price
    max_drawdown = ((min_low - entry_price) / entry_price) * 100 if entry_price else None
    max_profit = ((max_high - entry_price) / entry_price) * 100 if entry_price else None
    actual_return = ((close_after - entry_price) / entry_price) * 100 if close_after is not None and entry_price else None
    if result == OPEN_FLAT and actual_return is not None:
        if actual_return > 0.25:
            result = OPEN_PROFIT
        elif actual_return < -0.25:
            result = OPEN_LOSS
    if result == OPEN_LOSS:
        event_reason = "Entry touched but price closed below entry without hitting target."
    elif result == OPEN_PROFIT:
        event_reason = "Entry touched and current close is profitable, but target has not been confirmed."

    mistake_type = "BAD_SIGNAL" if result in {BAD_CALL, OPEN_LOSS} else ""
    def _minutes_between(start: Any, end: Any) -> float | None:
        try:
            if start is None or end is None:
                return None
            return round((pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 60, 2)
        except Exception:
            return None

    target_hit = target1_hit or target2_hit or target3_hit
    status = _evaluation_status(
        result=result,
        target_hit=target_hit,
        stop_hit=stop_hit,
        days_evaluated=days_evaluated,
        evaluation_quality=evaluation_quality,
    )
    final_quality = _status_quality(
        status=status,
        actual_return=round(actual_return, 2) if actual_return is not None else None,
        max_favorable_move_pct=max_favorable_move_pct,
        max_adverse_move_pct=max_adverse_move_pct,
    )
    return _build_path_audit(
        evaluation_status=status,
        result=result,
        entry_touched=True,
        stop_loss_hit=stop_hit,
        target_1_hit=target1_hit,
        target_2_hit=target2_hit,
        target_3_hit=target3_hit,
        actual_entry_price=round(entry_price, 4),
        signal_price=round(base_signal_price, 4) if base_signal_price is not None else None,
        next_available_open=round(next_available_open, 4) if next_available_open is not None else None,
        close_after_recommendation=close_after,
        max_drawdown_after_entry=round(max_drawdown, 2) if max_drawdown is not None else None,
        max_profit_after_entry=round(max_profit, 2) if max_profit is not None else None,
        max_favorable_move_pct=max_favorable_move_pct,
        max_adverse_move_pct=max_adverse_move_pct,
        actual_return=round(actual_return, 2) if actual_return is not None else None,
        estimated_pnl=round(actual_return, 2) if actual_return is not None else None,
        max_price_after_signal=round(max_price_after_signal, 4) if max_price_after_signal is not None else None,
        min_price_after_signal=round(min_price_after_signal, 4) if min_price_after_signal is not None else None,
        time_to_target_minutes=_minutes_between(entry_time, target_time),
        time_to_stop_minutes=_minutes_between(entry_time, stop_time),
        days_evaluated=days_evaluated,
        evaluation_quality=evaluation_quality,
        final_quality=final_quality,
        root_cause=event_reason,
        mistake_type=mistake_type,
        fix_required="Keep the stronger validation gates and require conditional entry confirmation.",
        priority="P0" if result == BAD_CALL else "P1" if result == OPEN_LOSS else "P2",
        details={"event_time": event_time, "first_event_time": str(first_event_time) if first_event_time is not None else None, "signal": signal, "candles": len(candles)},
    )


def _latest_real_trade(db: Session, symbol: str, target_date: date) -> PortfolioTrade | None:
    start, end = day_bounds(target_date)
    return db.scalar(
        select(PortfolioTrade)
        .where(PortfolioTrade.symbol == symbol, PortfolioTrade.trade_date >= start, PortfolioTrade.trade_date < end)
        .order_by(PortfolioTrade.trade_date.desc(), PortfolioTrade.id.desc())
    )


def audit_report_item(db: Session, report: RecommendationReport, item: RecommendationItem, target_date: date) -> dict[str, Any]:
    candles, evaluation_quality, timeframe_used = _candles_after_recommendation(db, item.symbol, report.report_time, target_date)
    signal_price = _latest_signal_price_before(db, item.symbol, report.report_time)
    audit = classify_recommendation_path(
        candles,
        entry_zone_low=item.entry_zone_low,
        entry_zone_high=item.entry_zone_high,
        stop_loss=item.stop_loss,
        target_1=item.target_1,
        target_2=item.target_2,
        target_3=item.target_3,
        signal=item.signal,
        evaluation_quality=evaluation_quality,
        signal_price=signal_price,
    )
    try:
        market = evaluate_daily_market(db, target_date=target_date, persist=False)
    except Exception as exc:
        market = {
            "market_score": None,
            "market_regime": "DATA_INSUFFICIENT",
            "trade_permission": "DATA_INSUFFICIENT",
            "warnings": [f"Market evaluation failed: {exc}"],
        }
    permission = str(market.get("trade_permission") or "DATA_INSUFFICIENT")
    rec = str(item.signal or "").upper()
    should_trade = "Yes" if rec in {"BUY", "STRONG BUY", "CONDITIONAL BUY"} and permission == "TRADE_ALLOWED" else "No"
    problem_factor, mistake_type = _component_problem(item, audit)
    if mistake_type and not audit.mistake_type:
        audit.mistake_type = mistake_type
    if audit.result in {BAD_CALL, OPEN_LOSS, LATE_SIGNAL, NO_ENTRY, LOW_LIQUIDITY, RISK_PROBLEM, DATA_PROBLEM, BAD_ENTRY}:
        audit.root_cause = f"{audit.root_cause} Problem factor: {problem_factor}."
    real_trade = _latest_real_trade(db, item.symbol, target_date)
    details = audit.to_dict()
    details["problem_factor"] = problem_factor
    details["evaluation_quality"] = audit.evaluation_quality
    details["timeframe_used"] = timeframe_used
    details["market_evaluation"] = market
    details["scores"] = {
        "telegram": item.telegram_score,
        "technical": item.technical_score,
        "strategy": item.strategy_score,
        "news": item.news_score,
        "backtest": item.backtest_score,
        "liquidity": item.risk_liquidity_score,
        "final": item.final_score,
    }
    details["real_trade"] = {
        "trade_type": real_trade.trade_type,
        "price": real_trade.price,
        "quantity": real_trade.quantity,
        "profit_loss": real_trade.profit_loss,
        "profit_loss_pct": real_trade.profit_loss_pct,
        "trade_date": real_trade.trade_date.isoformat(sep=" ", timespec="seconds"),
    } if real_trade else None
    return {
        "date": target_date.isoformat(),
        "report_type": report.report_type,
        "report_id": report.id,
        "report_time": report.report_time.isoformat(sep=" ", timespec="seconds"),
        "symbol": item.symbol,
        "recommended_signal": item.signal,
        "final_score": item.final_score,
        "telegram_score": item.telegram_score,
        "technical_score": item.technical_score,
        "strategy_score": item.strategy_score,
        "news_score": item.news_score,
        "backtest_score": item.backtest_score,
        "risk_liquidity_score": item.risk_liquidity_score,
        "entry_zone": f"{item.entry_zone_low} - {item.entry_zone_high}",
        "entry_zone_low": item.entry_zone_low,
        "entry_zone_high": item.entry_zone_high,
        "actual_entry_price": audit.actual_entry_price,
        "signal_price": audit.signal_price,
        "next_available_open": audit.next_available_open,
        "stop_loss": item.stop_loss,
        "target_1": item.target_1,
        "target_2": item.target_2,
        "target_3": item.target_3,
        "max_price_after_signal": audit.max_price_after_signal,
        "min_price_after_signal": audit.min_price_after_signal,
        "highest_price_after_signal": audit.max_price_after_signal,
        "lowest_price_after_signal": audit.min_price_after_signal,
        "latest_close": audit.close_after_recommendation,
        "time_to_target_minutes": audit.time_to_target_minutes,
        "time_to_stop_minutes": audit.time_to_stop_minutes,
        "max_drawdown_after_entry": audit.max_drawdown_after_entry,
        "max_profit_after_entry": audit.max_profit_after_entry,
        "max_favorable_move_pct": audit.max_favorable_move_pct,
        "max_adverse_move_pct": audit.max_adverse_move_pct,
        "actual_return": audit.actual_return,
        "estimated_pnl": audit.estimated_pnl,
        "evaluation_status": audit.evaluation_status,
        "evaluation_quality": audit.evaluation_quality,
        "days_evaluated": audit.days_evaluated,
        "final_quality": audit.final_quality,
        "not_evaluated_reason": audit.root_cause if audit.evaluation_status in {EVAL_NOT_EVALUATED, EVAL_DATA_MISSING, EVAL_ENTRY_NOT_REACHED} else "",
        "strategy_source": (item.details_json or {}).get("source") if isinstance(item.details_json, dict) else report.report_type,
        "market_score_at_signal": market.get("market_score"),
        "market_regime_at_signal": market.get("market_regime"),
        "trade_permission_at_signal": permission,
        "should_trade_yes_no": should_trade,
        "result": audit.result,
        "root_cause": audit.root_cause,
        "mistake_type": audit.mistake_type,
        "fix_required": audit.fix_required,
        "priority": audit.priority,
        "was_recommendation_valid": "Pending" if audit.evaluation_status in {EVAL_NOT_EVALUATED, EVAL_DATA_MISSING, EVAL_ENTRY_NOT_REACHED} else "Yes" if audit.result in {GOOD_CALL, OPEN_PROFIT, NO_ENTRY} else "No",
        "was_entry_valid": "Yes" if audit.entry_touched and audit.actual_entry_price else "No",
        "was_stop_loss_valid": "Yes" if item.stop_loss and item.entry_zone_low and item.stop_loss < item.entry_zone_low else "No",
        "was_risk_management_valid": "Yes" if (item.risk_reward or 0) >= 1.8 and (item.risk_liquidity_score or 0) >= 60 else "No",
        "details": details,
    }


def _summarize_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    evaluated_rows = [row for row in items if row.get("evaluation_status") not in {EVAL_NOT_EVALUATED, EVAL_DATA_MISSING, EVAL_ENTRY_NOT_REACHED}]
    evaluated_count = len(evaluated_rows)
    not_evaluated_count = sum(1 for row in items if row.get("evaluation_status") == EVAL_NOT_EVALUATED)
    missing_count = sum(1 for row in items if row.get("evaluation_status") == EVAL_DATA_MISSING)
    entry_not_reached_count = sum(1 for row in items if row.get("evaluation_status") == EVAL_ENTRY_NOT_REACHED)
    target_hit = sum(1 for row in evaluated_rows if row.get("evaluation_status") == EVAL_TARGET_HIT or row.get("details", {}).get("target_1_hit") or row.get("details", {}).get("target_2_hit") or row.get("details", {}).get("target_3_hit"))
    stop_loss_hit = sum(1 for row in evaluated_rows if row.get("evaluation_status") == EVAL_STOP_HIT or row.get("details", {}).get("stop_loss_hit"))
    good = sum(1 for row in evaluated_rows if row.get("evaluation_status") == EVAL_TARGET_HIT or row.get("final_quality") in {QUALITY_EXCELLENT, QUALITY_GOOD})
    bad = sum(1 for row in evaluated_rows if row.get("evaluation_status") == EVAL_STOP_HIT or row.get("final_quality") == QUALITY_BAD or row["result"] in {BAD_CALL, OPEN_LOSS, BAD_ENTRY, RISK_PROBLEM, LOW_LIQUIDITY})
    no_entry_count = sum(1 for row in evaluated_rows if row["result"] in {NO_ENTRY, LATE_SIGNAL})
    returns = [float(row.get("actual_return")) for row in evaluated_rows if _safe_float(row.get("actual_return")) is not None]
    mfe = [float(row.get("max_favorable_move_pct")) for row in evaluated_rows if _safe_float(row.get("max_favorable_move_pct")) is not None]
    mae = [float(row.get("max_adverse_move_pct")) for row in evaluated_rows if _safe_float(row.get("max_adverse_move_pct")) is not None]
    estimated_pnl = sum(returns)
    win_rate = round((good / evaluated_count) * 100.0, 2) if evaluated_count else None
    avg_return = round(sum(returns) / len(returns), 2) if returns else None
    avg_mfe = round(sum(mfe) / len(mfe), 2) if mfe else None
    avg_mae = round(sum(mae) / len(mae), 2) if mae else None
    best = max(evaluated_rows, key=lambda row: _safe_float(row.get("actual_return")) if _safe_float(row.get("actual_return")) is not None else -999999, default=None)
    worst = min(evaluated_rows, key=lambda row: _safe_float(row.get("actual_return")) if _safe_float(row.get("actual_return")) is not None else 999999, default=None)
    mistake_counts: dict[str, int] = {}
    for row in items:
        mistake = row.get("mistake_type") or row.get("result") or "UNKNOWN"
        mistake_counts[mistake] = mistake_counts.get(mistake, 0) + 1
    biggest_problem = max(mistake_counts.items(), key=lambda pair: pair[1])[0] if mistake_counts else "No recommendations"
    return {
        "total_recommendations": total,
        "evaluated_recommendations": evaluated_count,
        "not_evaluated": not_evaluated_count,
        "data_missing": missing_count,
        "entry_not_reached": entry_not_reached_count,
        "good_calls": good,
        "bad_calls": bad,
        "no_entry": no_entry_count,
        "stop_loss_hit": stop_loss_hit,
        "target_hit": target_hit,
        "win_rate_pct": win_rate,
        "average_return_pct": avg_return,
        "average_max_favorable_move_pct": avg_mfe,
        "average_max_adverse_move_pct": avg_mae,
        "best_recommendation": f"{best.get('symbol')} ({best.get('actual_return')}%)" if best else None,
        "worst_recommendation": f"{worst.get('symbol')} ({worst.get('actual_return')}%)" if worst else None,
        "estimated_pnl": round(estimated_pnl, 2),
        "biggest_problem": biggest_problem,
        "mistake_counts": mistake_counts,
    }


def _diagnosis(summary: dict[str, Any], items: list[dict[str, Any]]) -> str:
    if not items:
        return "No recommendation reports were found for this date."
    not_ready_count = int(summary.get("not_evaluated") or 0) + int(summary.get("data_missing") or 0)
    if not_ready_count >= max(1, len(items) // 2):
        return (
            "The main issue is post-signal validation coverage: most recommendations are either not evaluated yet or "
            "missing future OHLCV candles. Do not show win-rate as real accuracy until enough rows are evaluated."
        )
    if int(summary.get("evaluated_recommendations") or 0) < 5:
        return (
            "Accuracy is not reliable yet because evaluated sample size is too small. Keep the rows visible for review, "
            "but do not use the win-rate as a decision metric."
        )
    weak_validation = [
        row for row in items
        if (row.get("technical_score") or 0) < 70
        or (row.get("strategy_score") or 0) < 65
        or (row.get("backtest_score") or 0) < 60
        or (row.get("risk_liquidity_score") or 0) < 60
        or (row.get("details", {}).get("scores", {}).get("final") or 0) < 70
    ]
    if weak_validation:
        return (
            "The main issue is recommendation validation: at least one Top 5 item did not satisfy the stricter "
            "technical, strategy, backtest, liquidity, or score gates. These should be downgraded to WATCH ONLY."
        )
    if summary["stop_loss_hit"]:
        return "The main issue is adverse movement after entry; stop-loss and timing checks need stricter confirmation."
    if summary["no_entry"]:
        return "The main issue is entry timing: price did not return to the planned zone or the signal was late."
    return "No critical recommendation logic failure was detected from available candle data, but audit mode remains enabled."


def persist_audit(db: Session, target_date: date, items: list[dict[str, Any]], summary: dict[str, Any]) -> DailyLossAuditReport:
    report = DailyLossAuditReport(
        audit_date=datetime(target_date.year, target_date.month, target_date.day),
        total_recommendations=summary["total_recommendations"],
        good_calls=summary["good_calls"],
        bad_calls=summary["bad_calls"],
        no_entry=summary["no_entry"],
        stop_loss_hit=summary["stop_loss_hit"],
        target_hit=summary["target_hit"],
        estimated_pnl=summary["estimated_pnl"],
        biggest_problem=summary["biggest_problem"],
        final_diagnosis=_diagnosis(summary, items),
        action_plan=(
            "1. Keep live trading disabled.\n"
            "2. Require conditional buy validation gates.\n"
            "3. Use paper trading for at least 14 days.\n"
            "4. Penalize weak liquidity, weak backtests, and late entries."
        ),
        status="created",
        details_json={"mistake_counts": summary["mistake_counts"]},
    )
    db.add(report)
    db.flush()
    for row in items:
        db.add(
            DailyLossAuditItem(
                report_id=report.id,
                symbol=row["symbol"],
                recommendation=row.get("recommended_signal"),
                final_score=row.get("final_score"),
                entry_zone=row.get("entry_zone"),
                actual_entry_price=row.get("actual_entry_price"),
                stop_loss=row.get("stop_loss"),
                targets_json={"target_1": row.get("target_1"), "target_2": row.get("target_2"), "target_3": row.get("target_3")},
                max_drawdown_after_entry=row.get("max_drawdown_after_entry"),
                max_profit_after_entry=row.get("max_profit_after_entry"),
                actual_return=row.get("actual_return"),
                estimated_pnl=row.get("estimated_pnl"),
                evaluation_quality=row.get("evaluation_quality"),
                market_score_at_signal=row.get("market_score_at_signal"),
                market_regime_at_signal=row.get("market_regime_at_signal"),
                trade_permission_at_signal=row.get("trade_permission_at_signal"),
                should_trade_yes_no=row.get("should_trade_yes_no"),
                time_to_target_minutes=row.get("time_to_target_minutes"),
                time_to_stop_minutes=row.get("time_to_stop_minutes"),
                result=row.get("result"),
                mistake_type=row.get("mistake_type"),
                root_cause=row.get("root_cause"),
                fix_required=row.get("fix_required"),
                priority=row.get("priority"),
                details_json=row.get("details"),
            )
        )
    return report


def build_daily_loss_audit(
    *,
    target_date: date,
    persist: bool = True,
    db: Session | None = None,
) -> dict[str, Any]:
    def _run(active_db: Session) -> dict[str, Any]:
        reports = reports_for_date(active_db, target_date)
        audited: list[dict[str, Any]] = []
        for report in reports:
            report_items = active_db.scalars(
                select(RecommendationItem)
                .where(RecommendationItem.report_id == report.id)
                .order_by(RecommendationItem.final_score.desc(), RecommendationItem.id.asc())
            ).all()
            for item in report_items:
                audited.append(audit_report_item(active_db, report, item, target_date))
        summary = _summarize_items(audited)
        persisted_id = None
        if persist:
            persisted = persist_audit(active_db, target_date, audited, summary)
            active_db.commit()
            persisted_id = persisted.id
        return {
            "audit_date": target_date.isoformat(),
            "report_id": persisted_id,
            "summary": summary,
            "diagnosis": _diagnosis(summary, audited),
            "items": audited,
            "risk_note": RISK_NOTE,
        }

    if db is not None:
        return _run(db)
    init_db(seed=True)
    with sqlite_write_lock():
        with SessionLocal() as active_db:
            return _run(active_db)


def format_text_audit(result: dict[str, Any]) -> str:
    lines = [
        f"Date: {result['audit_date']}",
        "Report Type: Daily Loss Audit",
        "",
        f"Total Recommendations: {result['summary']['total_recommendations']}",
        f"Evaluated Recommendations: {result['summary'].get('evaluated_recommendations', 0)}",
        f"Not Evaluated: {result['summary'].get('not_evaluated', 0)}",
        f"Missing Data: {result['summary'].get('data_missing', 0)}",
        f"Good Calls: {result['summary']['good_calls']}",
        f"Bad Calls: {result['summary']['bad_calls']}",
        f"Win Rate: {result['summary'].get('win_rate_pct') if (result['summary'].get('evaluated_recommendations') or 0) >= 5 else 'Not reliable yet'}",
        f"No Entry: {result['summary']['no_entry']}",
        f"Stop Loss Hit: {result['summary']['stop_loss_hit']}",
        f"Target Hit: {result['summary']['target_hit']}",
        f"Estimated P&L %: {result['summary']['estimated_pnl']}",
        f"Biggest Problem: {result['summary']['biggest_problem']}",
        "",
        f"Final Diagnosis: {result['diagnosis']}",
        "",
    ]
    for row in result["items"]:
        lines.extend(
            [
                "-" * 70,
                f"Symbol: {row['symbol']}",
                f"Report Type: {row['report_type']}",
                f"Recommended Stock Signal: {row.get('recommended_signal')}",
                f"Actual Stock Movement: {row.get('actual_return')}%",
                f"Evaluation Status: {row.get('evaluation_status')}",
                f"Evaluation Quality: {row.get('evaluation_quality')}",
                f"Final Quality: {row.get('final_quality')}",
                f"Market At Signal: {row.get('market_regime_at_signal')} / {row.get('trade_permission_at_signal')} ({row.get('market_score_at_signal')})",
                f"Entry Zone: {row.get('entry_zone')}",
                f"Signal Price: {row.get('signal_price')}",
                f"Next Available Open: {row.get('next_available_open')}",
                f"Actual Entry Price: {row.get('actual_entry_price')}",
                f"Stop Loss: {row.get('stop_loss')}",
                f"Targets: {row.get('target_1')} / {row.get('target_2')} / {row.get('target_3')}",
                f"Highest/Lowest After Signal: {row.get('highest_price_after_signal')} / {row.get('lowest_price_after_signal')}",
                f"Latest Close: {row.get('latest_close')}",
                f"Max Favorable/Adverse Move: {row.get('max_favorable_move_pct')}% / {row.get('max_adverse_move_pct')}%",
                f"Days Evaluated: {row.get('days_evaluated')}",
                f"Max Drawdown After Entry: {row.get('max_drawdown_after_entry')}%",
                f"Time To Target: {row.get('time_to_target_minutes')} min",
                f"Time To Stop: {row.get('time_to_stop_minutes')} min",
                f"Result: {row.get('result')}",
                f"Reason For Loss: {row.get('root_cause')}",
                f"Root Cause: {row.get('mistake_type')}",
                f"Was The Recommendation Valid? {row.get('was_recommendation_valid')}",
                f"Was The Entry Valid? {row.get('was_entry_valid')}",
                f"Was The Stop Loss Valid? {row.get('was_stop_loss_valid')}",
                f"Was Risk Management Valid? {row.get('was_risk_management_valid')}",
                f"Fix Required: {row.get('fix_required')}",
                f"Priority: {row.get('priority')}",
            ]
        )
    lines.extend(["", f"Risk Note: {RISK_NOTE}"])
    return "\n".join(lines)


def format_telegram_audit(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "EGX Recommendation Audit Report",
        f"Date: {result['audit_date']}",
        "",
        "Today's Result:",
        f"Total Recommendations: {summary['total_recommendations']}",
        f"Evaluated: {summary.get('evaluated_recommendations', 0)}",
        f"Not Evaluated: {summary.get('not_evaluated', 0)}",
        f"Missing Data: {summary.get('data_missing', 0)}",
        f"Win Rate: {summary.get('win_rate_pct') if (summary.get('evaluated_recommendations') or 0) >= 5 else 'Not reliable yet'}",
        f"Good Calls: {summary['good_calls']}",
        f"Bad Calls: {summary['bad_calls']}",
        f"No Entry: {summary['no_entry']}",
        f"Stop Loss Hit: {summary['stop_loss_hit']}",
        f"Target Hit: {summary['target_hit']}",
        f"Estimated P&L %: {summary['estimated_pnl']}",
        "",
        f"Biggest Problem: {summary['biggest_problem']}",
        "",
        "Stock Breakdown:",
        "",
    ]
    for idx, row in enumerate(result["items"][:8], start=1):
        lines.extend(
            [
                f"{idx}) {row['symbol']}",
                f"Recommendation: {row.get('recommended_signal')}",
                f"Evaluation: {row.get('evaluation_status')} | Quality: {row.get('final_quality')}",
                f"Entry Zone: {row.get('entry_zone')}",
                f"Signal/Next Open: {row.get('signal_price')} / {row.get('next_available_open')}",
                f"Stop Loss: {row.get('stop_loss')}",
                f"Targets: {row.get('target_1')} / {row.get('target_2')} / {row.get('target_3')}",
                f"Actual Move: {row.get('actual_return')}% | MFE/MAE: {row.get('max_favorable_move_pct')}% / {row.get('max_adverse_move_pct')}%",
                f"Result: {row.get('result')}",
                f"Root Cause: {row.get('root_cause')}",
                f"Mistake Type: {row.get('mistake_type')}",
                f"Fix: {row.get('fix_required')}",
                "",
            ]
        )
    lines.extend(
        [
            "Final Diagnosis:",
            f"- Main issue: {result['diagnosis']}",
            "- Risk issue: direct BUY wording and weak validation gates are now blocked.",
            "- Data issue: missing and no-future candles are excluded from accuracy.",
            "",
            "Action Plan:",
            "1. Disable live trading until fixed.",
            "2. Reduce risk per trade.",
            "3. Require stronger technical confirmation.",
            "4. Add liquidity filter.",
            "5. Improve backtest realism.",
            "",
            f"Risk Note: {RISK_NOTE}",
        ]
    )
    return "\n".join(lines)


def export_csv(result: dict[str, Any]) -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIT_DIR / f"daily_loss_audit_{result['audit_date']}.csv"
    rows = [{key: value for key, value in row.items() if key != "details"} for row in result["items"]]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def send_telegram_audit(result: dict[str, Any]) -> None:
    from app.services.telegram_bot import send_private_message_sync

    send_private_message_sync(format_telegram_audit(result))


def _cli() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Audit EGX daily recommendations against actual movement.")
    parser.add_argument("--date", default="today", help="today or YYYY-MM-DD")
    parser.add_argument("--export-csv", action="store_true", help="Export audit rows to data/audits.")
    parser.add_argument("--send-telegram", action="store_true", help="Send the audit summary to Telegram.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    target_date = parse_audit_date(args.date)
    result = build_daily_loss_audit(target_date=target_date, persist=True)
    if args.export_csv:
        path = export_csv(result)
        result["csv_path"] = str(path)
    if args.send_telegram:
        send_telegram_audit(result)
        result["telegram_sent"] = True
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text_audit(result))
        if args.export_csv:
            print("")
            print(f"CSV exported: {result['csv_path']}")
        if args.send_telegram:
            print("")
            print("Telegram audit report sent.")


if __name__ == "__main__":
    _cli()
