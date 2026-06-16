from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import RISK_NOTE, Settings, get_settings
from app.database import SessionLocal, init_db, sqlite_write_lock
from app.models import MarketPrice, Stock, StrategyCliV6Result, StrategyResult, StrategyRun
from app.services.market_data.base import ProviderChain, ProviderUnavailable, build_provider_chain


logger = logging.getLogger(__name__)

STRATEGY_NAME = "CLI v6 EGX"
FULL_STRATEGY_NAME = "COMPOSITE LEADING INDICATOR v6 - EGX OPTIMIZED"
TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]
MIN_CANDLES = 55
RSI_PERIOD = 21
HMA_FAST = 14
HMA_SLOW = 34

RECOMMENDATION_AR = {
    "STRONG BUY": "شراء قوي",
    "WEAK BUY": "شراء ضعيف",
    "NEUTRAL": "محايد",
    "WEAK SELL": "بيع ضعيف",
    "STRONG SELL": "بيع قوي",
    "INSUFFICIENT DATA": "بيانات غير كافية",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace("EGX:", "")


def normalize_timeframe(timeframe: str) -> str:
    value = str(timeframe).strip().lower()
    aliases = {
        "15": "15m",
        "15min": "15m",
        "15m": "15m",
        "30": "30m",
        "30min": "30m",
        "30m": "30m",
        "60": "1h",
        "60m": "1h",
        "1h": "1h",
        "240": "4h",
        "240m": "4h",
        "4h": "4h",
        "d": "1d",
        "1d": "1d",
        "1D": "1d",
    }
    return aliases.get(value, value)


def _timeframe_delta(timeframe: str) -> timedelta | None:
    return {
        "15m": timedelta(minutes=15),
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
    }.get(normalize_timeframe(timeframe))


def _freq(timeframe: str) -> str:
    mapping = {"15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1D"}
    return mapping[normalize_timeframe(timeframe)]


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    if frame.empty:
        raise ProviderUnavailable("OHLCV data is empty.")
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    frame = frame.rename(columns={"datetime": "date", "timestamp": "date", "time": "date", "adj close": "close"})
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ProviderUnavailable(f"OHLCV data missing columns: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = (
        frame.dropna(subset=["date", "open", "high", "low", "close", "volume"])
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )
    if frame.empty:
        raise ProviderUnavailable("OHLCV data is empty after cleaning.")
    return frame[["date", "open", "high", "low", "close", "volume"]]


def _resample_frame(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    timeframe = normalize_timeframe(timeframe)
    base = _normalize_frame(frame)
    if timeframe != "1d" and not (base["date"].dt.hour.ne(0).any() or base["date"].dt.minute.ne(0).any()):
        raise ProviderUnavailable(f"Only daily candles are available; cannot build {timeframe}.")
    resampled = (
        base.set_index("date")
        .sort_index()
        .resample(_freq(timeframe))
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    if resampled.empty:
        raise ProviderUnavailable(f"Resampling returned no candles for {timeframe}.")
    return resampled


def _drop_uncompleted_last(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    delta = _timeframe_delta(timeframe)
    if delta is None or frame.empty:
        return frame
    last = pd.to_datetime(frame.iloc[-1]["date"])
    now = pd.Timestamp.utcnow()
    if last.tzinfo is None:
        age_seconds = (now.tz_localize(None) - last).total_seconds()
    else:
        age_seconds = (now - last.tz_convert("UTC")).total_seconds()
    if age_seconds < delta.total_seconds() * 0.95 and len(frame) > 1:
        return frame.iloc[:-1].copy()
    return frame


def _load_db_timeframe(db: Session, symbol: str, timeframe: str) -> pd.DataFrame:
    symbol = normalize_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    rows = db.scalars(select(MarketPrice).where(MarketPrice.symbol == symbol).order_by(MarketPrice.timestamp.asc())).all()
    if not rows:
        raise ProviderUnavailable(f"No OHLCV rows stored in market_prices for {symbol}.")
    data = [
        {
            "date": row.timestamp,
            "timeframe": normalize_timeframe(row.timeframe),
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
        }
        for row in rows
    ]
    all_frame = pd.DataFrame(data)
    exact = all_frame[all_frame["timeframe"] == timeframe].copy()
    if not exact.empty:
        frame = _normalize_frame(exact)
        frame.attrs["provider"] = "sqlite_market_prices"
        return frame

    if timeframe == "30m":
        lower = all_frame[all_frame["timeframe"].isin(["15m"])].copy()
    elif timeframe == "1h":
        lower = all_frame[all_frame["timeframe"].isin(["15m", "30m"])].copy()
    elif timeframe == "4h":
        lower = all_frame[all_frame["timeframe"].isin(["15m", "30m", "1h"])].copy()
    elif timeframe == "1d":
        lower = all_frame[all_frame["timeframe"].isin(["15m", "30m", "1h", "4h"])].copy()
    else:
        lower = pd.DataFrame()
    if lower.empty:
        raise ProviderUnavailable(f"No stored {timeframe} candles for {symbol}.")
    frame = _resample_frame(lower, timeframe)
    frame.attrs["provider"] = "sqlite_market_prices_resampled"
    return frame


def _load_provider_timeframe(provider_chain: ProviderChain, symbol: str, timeframe: str) -> pd.DataFrame:
    timeframe = normalize_timeframe(timeframe)
    if timeframe == "1d":
        frame = provider_chain.get_daily_ohlcv(symbol)
    elif timeframe == "30m":
        try:
            frame = _resample_frame(provider_chain.get_intraday_ohlcv(symbol, "15m"), "30m")
            frame.attrs["provider"] = "tradingview_resampled_15m"
        except Exception:
            frame = provider_chain.get_intraday_ohlcv(symbol, "30m")
    else:
        frame = provider_chain.get_intraday_ohlcv(symbol, timeframe)
    provider = str(frame.attrs.get("provider", "provider_chain"))
    is_mock = bool(frame.attrs.get("is_mock", False))
    normalized = _normalize_frame(frame)
    normalized.attrs["provider"] = provider
    normalized.attrs["is_mock"] = is_mock
    return normalized


def load_ohlcv_for_timeframe(
    db: Session,
    symbol: str,
    timeframe: str,
    settings: Settings | None = None,
    provider_chain: ProviderChain | None = None,
) -> pd.DataFrame:
    settings = settings or get_settings()
    provider_chain = provider_chain or build_provider_chain(settings)
    symbol = normalize_symbol(symbol)
    db_error: Exception | None = None
    try:
        frame = _load_db_timeframe(db, symbol, timeframe)
        return _drop_uncompleted_last(frame, timeframe)
    except Exception as exc:
        db_error = exc
    try:
        frame = _load_provider_timeframe(provider_chain, symbol, timeframe)
        return _drop_uncompleted_last(frame, timeframe)
    except Exception as provider_exc:
        raise ProviderUnavailable(
            f"No real OHLCV data for {symbol} {normalize_timeframe(timeframe)}. "
            f"Run TradingView/data import first. Stored-data error: {db_error}; provider error: {provider_exc}"
        ) from provider_exc


def _wma(series: pd.Series, length: int) -> pd.Series:
    length = max(1, int(length))
    weights = np.arange(1, length + 1, dtype=float)
    divisor = float(weights.sum())
    return series.rolling(length).apply(lambda values: float(np.dot(values, weights) / divisor), raw=True)


def _hma(series: pd.Series, length: int) -> pd.Series:
    half = max(1, int(length / 2))
    sqrt_len = max(1, int(np.sqrt(length)))
    return _wma(2 * _wma(series, half) - _wma(series, length), sqrt_len)


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return rsi


def _macd_histogram(close: pd.Series) -> pd.Series:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd - signal


def _rsi_score(rsi: float) -> int:
    if rsi >= 60:
        return 2
    if rsi >= 50:
        return 1
    if rsi <= 32:
        return -2
    if rsi <= 40:
        return -1
    return 0


def _direction_score(close: float, open_price: float, positive: int = 1, negative: int = -1) -> int:
    if close > open_price:
        return positive
    if close < open_price:
        return negative
    return 0


def _status(total_score: float) -> str:
    if total_score >= 4:
        return "BULLISH"
    if total_score <= -4:
        return "BEARISH"
    return "NEUTRAL"


def score_frame(frame: pd.DataFrame, timeframe: str) -> dict[str, Any]:
    timeframe = normalize_timeframe(timeframe)
    frame = _drop_uncompleted_last(_normalize_frame(frame), timeframe)
    provider = str(frame.attrs.get("provider", "unknown"))
    if len(frame) < MIN_CANDLES:
        raise ProviderUnavailable(f"{timeframe} needs at least {MIN_CANDLES} completed candles; found {len(frame)}.")

    close = frame["close"].astype(float)
    enriched = frame.copy()
    enriched["rsi_21"] = _rsi(close, RSI_PERIOD)
    enriched["avg_volume_20"] = enriched["volume"].rolling(20).mean()
    enriched["bar_range"] = enriched["high"] - enriched["low"]
    enriched["range_avg_14"] = enriched["bar_range"].rolling(14).mean()
    enriched["macd_histogram"] = _macd_histogram(close)
    enriched["hma_fast"] = _hma(close, HMA_FAST)
    enriched["hma_slow"] = _hma(close, HMA_SLOW)

    last = enriched.iloc[-1]
    prev = enriched.iloc[-2]
    required = ["rsi_21", "avg_volume_20", "range_avg_14", "macd_histogram", "hma_fast", "hma_slow"]
    if any(pd.isna(last[col]) for col in required) or pd.isna(prev["macd_histogram"]):
        raise ProviderUnavailable(f"{timeframe} indicators are not ready; more completed candles are required.")

    rsi_value = float(last["rsi_21"])
    open_price = float(last["open"])
    close_price = float(last["close"])
    volume = float(last["volume"])
    avg_volume = float(last["avg_volume_20"])
    bar_range = float(last["bar_range"])
    range_avg = float(last["range_avg_14"])
    histogram = float(last["macd_histogram"])
    previous_histogram = float(prev["macd_histogram"])

    rsi_component = _rsi_score(rsi_value)
    volume_component = _direction_score(close_price, open_price) if volume > 1.5 * avg_volume else 0
    range_component = _direction_score(close_price, open_price) if bar_range > 1.2 * range_avg else 0
    if histogram > 0 and histogram > previous_histogram:
        macd_component = 1
    elif histogram < 0 and histogram < previous_histogram:
        macd_component = -1
    else:
        macd_component = 0
    hma_fast_component = 1 if close_price > float(last["hma_fast"]) else -1
    hma_slow_component = 1 if close_price > float(last["hma_slow"]) else -1
    leading = rsi_component + volume_component + range_component + macd_component
    lagging = hma_fast_component + hma_slow_component
    total = leading + lagging

    notes = [
        f"RSI {rsi_value:.1f} score {rsi_component}",
        f"volume surge score {volume_component}",
        f"range expansion score {range_component}",
        f"MACD histogram score {macd_component}",
        f"HMA scores {hma_fast_component}/{hma_slow_component}",
    ]
    return {
        "timeframe": timeframe,
        "score": float(total),
        "leading": float(leading),
        "lagging": float(lagging),
        "status": _status(total),
        "rsi": round(rsi_value, 2),
        "volume_score": volume_component,
        "range_score": range_component,
        "macd_score": macd_component,
        "hma_fast_score": hma_fast_component,
        "hma_slow_score": hma_slow_component,
        "last_price": round(close_price, 4),
        "as_of": pd.to_datetime(last["date"]).to_pydatetime(),
        "provider": provider,
        "reason": "; ".join(notes),
    }


def insufficient_timeframe(timeframe: str, reason: str) -> dict[str, Any]:
    return {
        "timeframe": normalize_timeframe(timeframe),
        "score": None,
        "leading": None,
        "lagging": None,
        "status": "INSUFFICIENT DATA",
        "reason": reason,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _recommendation_from_counts(bullish: int, bearish: int, neutral: int) -> str:
    active = bullish + bearish + neutral
    if active < 3:
        return "INSUFFICIENT DATA"
    if bullish >= active * 0.60:
        return "STRONG BUY"
    if bearish >= active * 0.60:
        return "STRONG SELL"
    if bullish > bearish:
        return "WEAK BUY"
    if bearish > bullish:
        return "WEAK SELL"
    return "NEUTRAL"


def _confidence(active_rows: list[dict[str, Any]], recommendation: str) -> float:
    if recommendation == "INSUFFICIENT DATA" or not active_rows:
        return 0.0
    active = len(active_rows)
    bullish = sum(1 for row in active_rows if row.get("status") == "BULLISH")
    bearish = sum(1 for row in active_rows if row.get("status") == "BEARISH")
    neutral = sum(1 for row in active_rows if row.get("status") == "NEUTRAL")
    majority = max(bullish, bearish, neutral) / active
    avg_strength = sum(min(abs(float(row.get("score") or 0)) / 7.0, 1.0) for row in active_rows) / active
    return round((majority * 0.65 + avg_strength * 0.35) * 100, 2)


def _summary_reason(active_rows: list[dict[str, Any]], recommendation: str, errors: list[str]) -> str:
    if recommendation == "INSUFFICIENT DATA":
        return f"Only {len(active_rows)} active timeframe(s). Run TradingView/data import first for missing intraday candles."
    bullish = [row["timeframe"] for row in active_rows if row.get("status") == "BULLISH"]
    bearish = [row["timeframe"] for row in active_rows if row.get("status") == "BEARISH"]
    neutral = [row["timeframe"] for row in active_rows if row.get("status") == "NEUTRAL"]
    parts = [
        f"Bullish: {', '.join(bullish) or '-'}",
        f"Bearish: {', '.join(bearish) or '-'}",
        f"Neutral: {', '.join(neutral) or '-'}",
    ]
    if errors:
        parts.append(f"Missing data on {len(errors)} timeframe(s)")
    return ". ".join(parts)


def run_cli_v6_for_symbol(
    db: Session,
    symbol: str,
    settings: Settings | None = None,
    provider_chain: ProviderChain | None = None,
    run_id: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    settings = settings or get_settings()
    provider_chain = provider_chain or build_provider_chain(settings)
    symbol = normalize_symbol(symbol)
    run_id = run_id or f"cli_v6_{utcnow():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"
    created_at = utcnow()

    timeframe_results: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for timeframe in TIMEFRAMES:
        try:
            frame = load_ohlcv_for_timeframe(db, symbol, timeframe, settings=settings, provider_chain=provider_chain)
            scored = score_frame(frame, timeframe)
        except Exception as exc:
            message = str(exc)
            errors.append(f"{timeframe}: {message}")
            scored = insufficient_timeframe(timeframe, message)
        timeframe_results[timeframe] = scored

    active_rows = [row for row in timeframe_results.values() if row.get("status") != "INSUFFICIENT DATA"]
    bullish_count = sum(1 for row in active_rows if row.get("status") == "BULLISH")
    bearish_count = sum(1 for row in active_rows if row.get("status") == "BEARISH")
    neutral_count = sum(1 for row in active_rows if row.get("status") == "NEUTRAL")
    recommendation = _recommendation_from_counts(bullish_count, bearish_count, neutral_count)
    confidence = _confidence(active_rows, recommendation)
    current_row = timeframe_results.get("1d")
    if not current_row or current_row.get("score") is None:
        current_row = next((row for row in reversed(list(timeframe_results.values())) if row.get("score") is not None), None)
    current_score = current_row.get("score") if current_row else None
    reason = _summary_reason(active_rows, recommendation, errors)

    result = {
        "symbol": symbol,
        "strategy_name": STRATEGY_NAME,
        "full_strategy_name": FULL_STRATEGY_NAME,
        "current_score": current_score,
        "timeframes": timeframe_results,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "neutral_count": neutral_count,
        "active_timeframes": len(active_rows),
        "recommendation": recommendation,
        "recommendation_ar": RECOMMENDATION_AR.get(recommendation, recommendation),
        "confidence": confidence,
        "reason": reason,
        "run_id": run_id,
        "created_at": created_at.isoformat(),
        "risk_note": RISK_NOTE,
    }
    if persist:
        persist_cli_v6_result(db, result)
    return result


def persist_cli_v6_result(db: Session, result: dict[str, Any]) -> None:
    created_at = pd.to_datetime(result["created_at"]).to_pydatetime()
    rows: list[StrategyCliV6Result] = []
    common_rows: list[StrategyResult] = []
    rows.append(
        StrategyCliV6Result(
            symbol=result["symbol"],
            strategy_name=STRATEGY_NAME,
            timeframe="summary",
            total_score=result.get("current_score"),
            leading_score=None,
            lagging_score=None,
            status=result.get("recommendation"),
            recommendation=result.get("recommendation"),
            recommendation_ar=result.get("recommendation_ar"),
            bullish_count=result.get("bullish_count"),
            bearish_count=result.get("bearish_count"),
            neutral_count=result.get("neutral_count"),
            confidence=result.get("confidence"),
            reason=result.get("reason"),
            run_id=result["run_id"],
            created_at=created_at,
        )
    )
    common_rows.append(
        StrategyResult(
            strategy_code="cli_v6_egx",
            strategy_name=STRATEGY_NAME,
            symbol=result["symbol"],
            timeframe="summary",
            signal=result.get("recommendation"),
            recommendation=result.get("recommendation"),
            score=result.get("current_score"),
            confidence=result.get("confidence"),
            trend=result.get("recommendation"),
            reason=result.get("reason"),
            details_json=_json_safe(result),
            run_id=result["run_id"],
            created_at=created_at,
        )
    )
    for timeframe, row in (result.get("timeframes") or {}).items():
        rows.append(
            StrategyCliV6Result(
                symbol=result["symbol"],
                strategy_name=STRATEGY_NAME,
                timeframe=timeframe,
                total_score=row.get("score"),
                leading_score=row.get("leading"),
                lagging_score=row.get("lagging"),
                status=row.get("status"),
                recommendation=result.get("recommendation"),
                recommendation_ar=result.get("recommendation_ar"),
                bullish_count=result.get("bullish_count"),
                bearish_count=result.get("bearish_count"),
                neutral_count=result.get("neutral_count"),
                confidence=result.get("confidence"),
                reason=row.get("reason") or result.get("reason"),
                run_id=result["run_id"],
                created_at=created_at,
            )
        )
        common_rows.append(
            StrategyResult(
                strategy_code="cli_v6_egx",
                strategy_name=STRATEGY_NAME,
                symbol=result["symbol"],
                timeframe=timeframe,
                signal=row.get("status"),
                recommendation=result.get("recommendation"),
                score=row.get("score"),
                confidence=result.get("confidence"),
                trend=row.get("status"),
                reason=row.get("reason") or result.get("reason"),
                details_json=_json_safe(row),
                run_id=result["run_id"],
                created_at=created_at,
            )
        )
    with sqlite_write_lock():
        db.add_all(rows + common_rows)
        db.commit()


def candidate_symbols(db: Session, limit: int | None = None) -> list[str]:
    stmt = select(Stock.symbol).where(Stock.is_active.is_(True)).order_by(Stock.symbol.asc())
    if limit:
        stmt = stmt.limit(limit)
    return [normalize_symbol(symbol) for symbol in db.scalars(stmt).all()]


def run_cli_v6_universe(
    db: Session,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    selected = [normalize_symbol(symbol) for symbol in symbols] if symbols else candidate_symbols(db, limit=limit)
    run_id = f"cli_v6_{utcnow():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"
    started_at = utcnow()
    run = StrategyRun(run_id=run_id, strategy_name=STRATEGY_NAME, started_at=started_at, status="running", symbols_count=0)
    with sqlite_write_lock():
        db.add(run)
        db.commit()

    provider_chain = build_provider_chain(settings)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for symbol in selected:
        try:
            rows.append(run_cli_v6_for_symbol(db, symbol, settings=settings, provider_chain=provider_chain, run_id=run_id, persist=True))
        except Exception as exc:
            logger.exception("CLI v6 failed for %s", symbol)
            errors.append(f"{symbol}: {exc}")
    with sqlite_write_lock():
        db_run = db.scalar(select(StrategyRun).where(StrategyRun.run_id == run_id))
        if db_run:
            db_run.finished_at = utcnow()
            db_run.symbols_count = len(rows)
            db_run.status = "success" if not errors else "partial_success" if rows else "failed"
            db_run.error_message = "; ".join(errors[:8]) if errors else None
        db.commit()
    return {
        "run_id": run_id,
        "strategy_name": STRATEGY_NAME,
        "started_at": started_at.isoformat(),
        "finished_at": utcnow().isoformat(),
        "status": "success" if not errors else "partial_success" if rows else "failed",
        "symbols_count": len(rows),
        "errors": errors,
        "rows": rows,
    }


def latest_cli_v6_result(db: Session, symbol: str) -> dict[str, Any] | None:
    symbol = normalize_symbol(symbol)
    summary = db.scalar(
        select(StrategyCliV6Result)
        .where(
            StrategyCliV6Result.symbol == symbol,
            StrategyCliV6Result.strategy_name == STRATEGY_NAME,
            StrategyCliV6Result.timeframe == "summary",
        )
        .order_by(StrategyCliV6Result.created_at.desc(), StrategyCliV6Result.id.desc())
    )
    if not summary:
        return None
    rows = db.scalars(
        select(StrategyCliV6Result)
        .where(
            StrategyCliV6Result.symbol == symbol,
            StrategyCliV6Result.strategy_name == STRATEGY_NAME,
            StrategyCliV6Result.run_id == summary.run_id,
            StrategyCliV6Result.timeframe != "summary",
        )
        .order_by(StrategyCliV6Result.id.asc())
    ).all()
    timeframes = {
        str(row.timeframe): {
            "score": row.total_score,
            "leading": row.leading_score,
            "lagging": row.lagging_score,
            "status": row.status,
            "reason": row.reason,
        }
        for row in rows
    }
    return {
        "symbol": summary.symbol,
        "strategy_name": summary.strategy_name,
        "current_score": summary.total_score,
        "timeframes": timeframes,
        "bullish_count": summary.bullish_count or 0,
        "bearish_count": summary.bearish_count or 0,
        "neutral_count": summary.neutral_count or 0,
        "recommendation": summary.recommendation or "INSUFFICIENT DATA",
        "recommendation_ar": summary.recommendation_ar or RECOMMENDATION_AR["INSUFFICIENT DATA"],
        "confidence": summary.confidence or 0.0,
        "reason": summary.reason or "-",
        "run_id": summary.run_id,
        "created_at": summary.created_at.isoformat() if summary.created_at else None,
        "risk_note": RISK_NOTE,
    }


def recommendation_to_score(recommendation: str | None, confidence: float | None = None) -> float | None:
    if not recommendation or recommendation == "INSUFFICIENT DATA":
        return None
    base = {
        "STRONG BUY": 100.0,
        "WEAK BUY": 75.0,
        "NEUTRAL": 50.0,
        "WEAK SELL": 25.0,
        "STRONG SELL": 0.0,
    }.get(str(recommendation).upper())
    if base is None:
        return None
    if confidence is None:
        return base
    return round(base * 0.65 + float(confidence) * 0.35, 2)


def format_cli_v6_strategy_report(db: Session, symbol: str, settings: Settings | None = None) -> str:
    symbol = normalize_symbol(symbol)
    result = latest_cli_v6_result(db, symbol)
    if result is None:
        try:
            result = run_cli_v6_for_symbol(db, symbol, settings=settings or get_settings(), persist=True)
        except Exception as exc:
            return (
                f"CLI v6 EGX Strategy: {symbol}\n"
                f"No real OHLCV data is available yet: {exc}\n"
                "Run TradingView/data import first.\n"
                f"Risk Note: {RISK_NOTE}"
            )
    lines = [
        f"CLI v6 EGX Strategy: {symbol}",
        f"Recommendation: {result.get('recommendation')} ({result.get('recommendation_ar')})",
        f"Confidence: {float(result.get('confidence') or 0):.0f}%",
        f"Bullish/Bearish/Neutral: {result.get('bullish_count')}/{result.get('bearish_count')}/{result.get('neutral_count')}",
        "",
        "Timeframes",
    ]
    for timeframe in TIMEFRAMES:
        row = (result.get("timeframes") or {}).get(timeframe) or {}
        score = row.get("score")
        score_text = "-" if score is None else f"{float(score):.0f}"
        leading = row.get("leading")
        lagging = row.get("lagging")
        leading_text = "-" if leading is None else f"{float(leading):.0f}"
        lagging_text = "-" if lagging is None else f"{float(lagging):.0f}"
        lines.append(
            f"- {timeframe}: {row.get('status') or 'INSUFFICIENT DATA'} | score {score_text} | leading {leading_text} | lagging {lagging_text}"
        )
    lines.extend(
        [
            "",
            f"Reason: {result.get('reason') or '-'}",
            f"Latest update: {result.get('created_at') or '-'}",
            f"Risk Note: {RISK_NOTE}",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CLI v6 EGX strategy.")
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    init_db(seed=True)
    with SessionLocal() as db:
        if args.symbol:
            result = run_cli_v6_for_symbol(db, args.symbol, persist=not args.no_persist)
            print(json.dumps(result, ensure_ascii=True, indent=2, default=str))
            return
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()] if args.symbols else None
        result = run_cli_v6_universe(db, symbols=symbols, limit=args.limit)
        print(f"CLI v6 run {result['run_id']} completed: {result['symbols_count']} symbols, status={result['status']}.")
        for row in result["rows"][:10]:
            print(f"{row['symbol']} {row['recommendation']} confidence={row['confidence']:.0f}% current_score={row['current_score']}")


if __name__ == "__main__":
    main()
