from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DISCLAIMER, Settings, get_settings
from app.models import Stock
from app.services.market_data.base import MarketQuote, ProviderChain, ProviderUnavailable, build_provider_chain
from app.services.screener_recommendations import build_final_recommendations


TIMEFRAME_ORDER = {"15m": 0, "1h": 1, "4h": 2, "1D": 3}


@dataclass
class StrategyTrade:
    entry_time: datetime
    exit_time: datetime
    entry: float
    exit: float
    return_pct: float
    reason: str
    bars_held: int


@dataclass
class StrategyResult:
    symbol: str
    timeframe: str
    action: str
    score: float
    trend: str
    last_price: float | None
    entry: float | None
    stop: float | None
    target: float | None
    win_rate: float | None
    trades: int
    total_return_pct: float
    max_drawdown_pct: float
    provider: str
    is_mock: bool
    as_of: datetime | None
    data_quality: str = "ok"
    reference_price: float | None = None
    price_difference_percent: float | None = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "action": self.action,
            "score": round(self.score, 2),
            "trend": self.trend,
            "last_price": self.last_price,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "win_rate": self.win_rate,
            "trades": self.trades,
            "total_return_pct": round(self.total_return_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "provider": self.provider,
            "is_mock": self.is_mock,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "data_quality": self.data_quality,
            "reference_price": self.reference_price,
            "price_difference_percent": self.price_difference_percent,
            "notes": self.notes,
            "error": self.error,
        }


def normalize_timeframe(timeframe: str) -> str:
    value = timeframe.strip().lower()
    aliases = {"15": "15m", "15min": "15m", "15m": "15m", "60": "1h", "60m": "1h", "1h": "1h", "240": "4h", "240m": "4h", "4h": "4h", "d": "1D", "1d": "1D"}
    return aliases.get(value, timeframe)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period).mean()


def _min_required_bars(timeframe: str) -> int:
    return 45 if timeframe == "15m" else 70


def _prepare_frame(df: pd.DataFrame, max_bars: int, min_bars: int = 70) -> pd.DataFrame:
    frame = df.copy()
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    frame = frame.rename(columns={"datetime": "date", "time": "date", "adj close": "close"})
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ProviderUnavailable(f"OHLCV data missing columns: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").tail(max_bars).reset_index(drop=True)
    if len(frame) < min_bars:
        raise ProviderUnavailable(f"Strategy needs at least {min_bars} candles")
    close = frame["close"]
    frame["ema_20"] = close.ewm(span=20, adjust=False).mean()
    frame["ema_50"] = close.ewm(span=50, adjust=False).mean()
    frame["ema_200"] = close.ewm(span=200, adjust=False).mean()
    frame["rsi_14"] = _rsi(close)
    frame["macd"] = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    frame["macd_signal"] = frame["macd"].ewm(span=9, adjust=False).mean()
    frame["atr_14"] = _atr(frame)
    frame["avg_volume_20"] = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / frame["avg_volume_20"].replace(0, np.nan)
    frame["resistance_20"] = frame["high"].rolling(20).max().shift(1)
    frame["support_20"] = frame["low"].rolling(20).min().shift(1)
    return frame


def _age_days(timestamp: Any) -> float:
    value = pd.to_datetime(timestamp)
    now = pd.Timestamp.utcnow()
    if value.tzinfo is None:
        return max(0.0, (now.tz_localize(None) - value).total_seconds() / 86400)
    return max(0.0, (now - value.tz_convert("UTC")).total_seconds() / 86400)


def _reference_quote(provider_chain: ProviderChain, symbol: str) -> MarketQuote | None:
    try:
        quote = provider_chain.get_last_price(symbol)
    except Exception:
        return None
    if quote.close is None or quote.is_mock:
        return None
    return quote


def _price_difference_percent(last_price: float | None, reference_price: float | None) -> float | None:
    if not last_price or not reference_price:
        return None
    return round(abs(last_price - reference_price) / reference_price * 100, 2)


def _validate_strategy_frame(
    frame: pd.DataFrame,
    symbol: str,
    timeframe: str,
    provider: str,
    is_mock: bool,
    settings: Settings,
    reference_quote: MarketQuote | None,
) -> tuple[str, float | None, float | None]:
    if is_mock and not settings.strategy_allow_mock_data:
        raise ProviderUnavailable("Mock strategy candles are disabled; import real OHLCV or enable STRATEGY_ALLOW_MOCK_DATA for testing.")

    last = frame.iloc[-1]
    age = _age_days(last["date"])
    max_age = settings.strategy_max_daily_age_days if timeframe == "1D" else settings.strategy_max_intraday_age_days
    if age > max_age:
        raise ProviderUnavailable(
            f"{provider} {timeframe} candles for {symbol} are stale: latest candle is {age:.1f} days old, max allowed is {max_age}."
        )

    reference_price = float(reference_quote.close) if reference_quote and reference_quote.close is not None else None
    diff = _price_difference_percent(float(last["close"]), reference_price)
    if diff is not None and diff > settings.strategy_price_tolerance_percent:
        raise ProviderUnavailable(
            f"{provider} {timeframe} close {float(last['close']):.2f} differs from reference price "
            f"{reference_price:.2f} by {diff:.1f}%, above tolerance {settings.strategy_price_tolerance_percent:.1f}%."
        )

    quality = "mock" if is_mock else "fresh"
    if len(frame) < 70:
        quality = f"{quality}_limited_history"
    if reference_price is None:
        quality = f"{quality}_unverified"
    return quality, reference_price, diff


def _signal_score(row: pd.Series, prev: pd.Series) -> tuple[float, list[str], str]:
    close = float(row["close"])
    ema20 = float(row["ema_20"])
    ema50 = float(row["ema_50"])
    ema200 = float(row["ema_200"]) if pd.notna(row["ema_200"]) else ema50
    rsi = float(row["rsi_14"]) if pd.notna(row["rsi_14"]) else 50.0
    volume_ratio = float(row["volume_ratio"]) if pd.notna(row["volume_ratio"]) else 1.0
    macd = float(row["macd"]) if pd.notna(row["macd"]) else 0.0
    macd_signal = float(row["macd_signal"]) if pd.notna(row["macd_signal"]) else 0.0
    resistance = float(row["resistance_20"]) if pd.notna(row["resistance_20"]) else None

    score = 0.0
    notes: list[str] = []
    if close > ema20 > ema50:
        score += 25
        notes.append("price above EMA20 and EMA50")
    if ema50 >= ema200 or close >= ema200:
        score += 12
        notes.append("higher timeframe moving-average bias is positive")
    if ema20 > float(prev["ema_20"]):
        score += 10
        notes.append("EMA20 is rising")
    if 45 <= rsi <= 70:
        score += 16
        notes.append("RSI is constructive without being stretched")
    elif 70 < rsi <= 78:
        score += 5
        notes.append("RSI is strong but stretched")
    elif rsi < 35:
        score -= 10
        notes.append("RSI is weak")
    elif rsi > 78:
        score -= 14
        notes.append("RSI is overbought")
    if macd > macd_signal:
        score += 12
        notes.append("MACD is above signal")
    if volume_ratio >= 1.2:
        score += 10
        notes.append("volume confirms the move")
    elif volume_ratio < 0.7:
        score -= 6
        notes.append("volume is weak")
    if resistance and close > resistance:
        score += 15
        notes.append("20-candle breakout")

    if close > ema20 > ema50:
        trend = "BULLISH"
    elif close < ema20 < ema50:
        trend = "BEARISH"
    else:
        trend = "RANGE"
    return max(0.0, min(100.0, score)), notes, trend


def _action_from_score(score: float, trend: str, notes: list[str]) -> str:
    if trend == "BEARISH" or score < 38:
        return "AVOID"
    if score >= 75:
        return "BUY"
    if score >= 58:
        return "WATCH"
    return "NEUTRAL"


def _risk_levels(row: pd.Series) -> tuple[float, float, float]:
    entry = float(row["close"])
    atr = float(row["atr_14"]) if pd.notna(row["atr_14"]) and float(row["atr_14"]) > 0 else entry * 0.02
    support = float(row["support_20"]) if pd.notna(row["support_20"]) else entry - atr * 2
    stop = min(entry * 0.97, entry - atr * 1.6, support * 0.995)
    risk = max(entry - stop, entry * 0.015)
    target = entry + risk * 2.0
    return round(entry, 4), round(stop, 4), round(target, 4)


def _max_hold_bars(timeframe: str) -> int:
    return {"15m": 32, "1h": 28, "4h": 20, "1D": 20}.get(timeframe, 20)


def _backtest(frame: pd.DataFrame, timeframe: str) -> tuple[list[StrategyTrade], float, float]:
    trades: list[StrategyTrade] = []
    equity = 100.0
    equity_curve = [equity]
    open_trade: dict[str, Any] | None = None
    max_hold = _max_hold_bars(timeframe)

    start_idx = min(60, max(20, len(frame) // 3))
    for idx in range(start_idx, len(frame)):
        row = frame.iloc[idx]
        prev = frame.iloc[idx - 1]
        if open_trade:
            exit_price = None
            reason = ""
            if float(row["low"]) <= open_trade["stop"]:
                exit_price = open_trade["stop"]
                reason = "stop"
            elif float(row["high"]) >= open_trade["target"]:
                exit_price = open_trade["target"]
                reason = "target"
            else:
                score, _, trend = _signal_score(row, prev)
                if trend == "BEARISH" or score < 42:
                    exit_price = float(row["close"])
                    reason = "trend_exit"
                elif idx - open_trade["entry_idx"] >= max_hold:
                    exit_price = float(row["close"])
                    reason = "time_exit"
            if exit_price is not None:
                return_pct = (exit_price - open_trade["entry"]) / open_trade["entry"] * 100
                equity *= 1 + return_pct / 100
                equity_curve.append(equity)
                trades.append(
                    StrategyTrade(
                        entry_time=open_trade["entry_time"],
                        exit_time=pd.to_datetime(row["date"]).to_pydatetime(),
                        entry=round(open_trade["entry"], 4),
                        exit=round(exit_price, 4),
                        return_pct=round(return_pct, 2),
                        reason=reason,
                        bars_held=idx - open_trade["entry_idx"],
                    )
                )
                open_trade = None
            continue

        score, _, trend = _signal_score(row, prev)
        if _action_from_score(score, trend, []) == "BUY":
            entry, stop, target = _risk_levels(row)
            open_trade = {
                "entry_idx": idx,
                "entry_time": pd.to_datetime(row["date"]).to_pydatetime(),
                "entry": entry,
                "stop": stop,
                "target": target,
            }

    if open_trade:
        row = frame.iloc[-1]
        exit_price = float(row["close"])
        return_pct = (exit_price - open_trade["entry"]) / open_trade["entry"] * 100
        equity *= 1 + return_pct / 100
        equity_curve.append(equity)
        trades.append(
            StrategyTrade(
                entry_time=open_trade["entry_time"],
                exit_time=pd.to_datetime(row["date"]).to_pydatetime(),
                entry=round(open_trade["entry"], 4),
                exit=round(exit_price, 4),
                return_pct=round(return_pct, 2),
                reason="open_marked_to_market",
                bars_held=len(frame) - 1 - open_trade["entry_idx"],
            )
        )

    curve = pd.Series(equity_curve)
    drawdown = ((curve / curve.cummax()) - 1.0).min() * 100 if not curve.empty else 0.0
    total_return = equity - 100.0
    return trades, round(total_return, 2), round(abs(float(drawdown)), 2)


def _ohlcv_for_timeframe(provider_chain: ProviderChain, symbol: str, timeframe: str) -> pd.DataFrame:
    if timeframe == "1D":
        return provider_chain.get_daily_ohlcv(symbol)
    return provider_chain.get_intraday_ohlcv(symbol, timeframe)


def _provider_ohlcv(provider, symbol: str, timeframe: str) -> pd.DataFrame:
    if timeframe == "1D":
        return provider.get_daily_ohlcv(symbol)
    return provider.get_intraday_ohlcv(symbol, timeframe)


def _load_strategy_frame(
    provider_chain: ProviderChain,
    symbol: str,
    timeframe: str,
    settings: Settings,
    reference_quote: MarketQuote | None,
) -> tuple[pd.DataFrame, str, bool, str, float | None, float | None]:
    last_error: Exception | None = None
    provider_errors: list[str] = []
    for provider in provider_chain.providers:
        try:
            raw = _provider_ohlcv(provider, symbol, timeframe)
            provider_name = str(raw.attrs.get("provider", provider.provider_name))
            is_mock = bool(raw.attrs.get("is_mock", provider.is_mock))
            frame = _prepare_frame(raw, max_bars=settings.strategy_backtest_bars, min_bars=_min_required_bars(timeframe))
            data_quality, reference_price, price_diff = _validate_strategy_frame(
                frame=frame,
                symbol=symbol,
                timeframe=timeframe,
                provider=provider_name,
                is_mock=is_mock,
                settings=settings,
                reference_quote=reference_quote,
            )
            return frame, provider_name, is_mock, data_quality, reference_price, price_diff
        except ProviderUnavailable as exc:
            last_error = exc
            provider_errors.append(f"{provider.provider_name}: {exc}")
        except Exception as exc:
            last_error = exc
            provider_errors.append(f"{provider.provider_name}: {exc}")
    details = "; ".join(provider_errors) if provider_errors else str(last_error)
    raise ProviderUnavailable(f"No provider had trusted {timeframe} candles for {symbol}: {details}")


def run_strategy_backtest(
    symbol: str,
    timeframe: str,
    provider_chain: ProviderChain | None = None,
    settings: Settings | None = None,
    reference_quote: MarketQuote | None = None,
) -> StrategyResult:
    settings = settings or get_settings()
    provider_chain = provider_chain or build_provider_chain(settings)
    timeframe = normalize_timeframe(timeframe)
    reference_quote = reference_quote or _reference_quote(provider_chain, symbol.upper())
    try:
        frame, provider, is_mock, data_quality, reference_price, price_diff = _load_strategy_frame(
            provider_chain,
            symbol.upper(),
            timeframe,
            settings,
            reference_quote,
        )
        last = frame.iloc[-1]
        prev = frame.iloc[-2]
        score, notes, trend = _signal_score(last, prev)
        action = _action_from_score(score, trend, notes)
        entry, stop, target = _risk_levels(last)
        trades, total_return, max_drawdown = _backtest(frame, timeframe)
        wins = [trade for trade in trades if trade.return_pct > 0]
        win_rate = round(len(wins) / len(trades) * 100, 2) if trades else None
        return StrategyResult(
            symbol=symbol.upper(),
            timeframe=timeframe,
            action=action,
            score=score,
            trend=trend,
            last_price=round(float(last["close"]), 4),
            entry=entry if action in {"BUY", "WATCH"} else None,
            stop=stop if action in {"BUY", "WATCH"} else None,
            target=target if action in {"BUY", "WATCH"} else None,
            win_rate=win_rate,
            trades=len(trades),
            total_return_pct=total_return,
            max_drawdown_pct=max_drawdown,
            provider=provider,
            is_mock=is_mock,
            as_of=pd.to_datetime(last["date"]).to_pydatetime(),
            data_quality=data_quality,
            reference_price=reference_price,
            price_difference_percent=price_diff,
            notes=notes[:5],
        )
    except Exception as exc:
        return StrategyResult(
            symbol=symbol.upper(),
            timeframe=timeframe,
            action="UNAVAILABLE",
            score=0.0,
            trend="UNKNOWN",
            last_price=None,
            entry=None,
            stop=None,
            target=None,
            win_rate=None,
            trades=0,
            total_return_pct=0.0,
            max_drawdown_pct=0.0,
            provider="unavailable",
            is_mock=False,
            as_of=None,
            data_quality="unavailable",
            reference_price=float(reference_quote.close) if reference_quote and reference_quote.close is not None else None,
            price_difference_percent=None,
            notes=[],
            error=str(exc),
        )


def run_strategy_for_symbol(
    db: Session,
    symbol: str,
    settings: Settings | None = None,
    provider_chain: ProviderChain | None = None,
    timeframes: list[str] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    provider_chain = provider_chain or build_provider_chain(settings)
    frames = [normalize_timeframe(item) for item in (timeframes or settings.strategy_timeframe_list)]
    reference_quote = _reference_quote(provider_chain, symbol.upper())
    results = [
        run_strategy_backtest(symbol, frame, provider_chain=provider_chain, settings=settings, reference_quote=reference_quote).to_dict()
        for frame in frames
    ]
    usable = [row for row in results if row["action"] != "UNAVAILABLE"]
    buy_count = sum(1 for row in usable if row["action"] == "BUY")
    watch_count = sum(1 for row in usable if row["action"] == "WATCH")
    higher = [row for row in usable if row["timeframe"] in {"4h", "1D"}]
    higher_buy = sum(1 for row in higher if row["action"] == "BUY")
    average_score = round(sum(row["score"] for row in usable) / len(usable), 2) if usable else 0.0

    if not usable:
        action = "UNAVAILABLE"
    elif higher_buy >= 1 and buy_count >= 2 and average_score >= 68:
        action = "BUY"
    elif buy_count + watch_count >= 2 and average_score >= 55:
        action = "WATCH"
    elif usable and all(row["action"] in {"AVOID", "NEUTRAL"} for row in usable):
        action = "AVOID"
    else:
        action = "NEUTRAL"

    stock = db.scalar(select(Stock).where(Stock.symbol == symbol.upper()))
    return {
        "symbol": symbol.upper(),
        "name": stock.name_en if stock else symbol.upper(),
        "sector": stock.sector if stock else None,
        "strategy_action": action,
        "strategy_score": average_score,
        "buy_timeframes": buy_count,
        "watch_timeframes": watch_count,
        "available_timeframes": len(usable),
        "uses_mock_data": any(row["is_mock"] for row in usable),
        "reference_price": float(reference_quote.close) if reference_quote and reference_quote.close is not None else None,
        "reference_provider": reference_quote.provider if reference_quote else None,
        "data_quality": "unavailable" if not usable else "mock" if any(row["is_mock"] for row in usable) else "fresh",
        "timeframes": results,
        "disclaimer": DISCLAIMER,
    }


def _candidate_symbols(db: Session, settings: Settings, limit: int) -> list[str]:
    try:
        run = build_final_recommendations(db, settings=settings, limit=max(limit, settings.strategy_symbol_limit))
        symbols = [row["symbol"] for row in run.rows if row.get("final_recommendation") in {"BUY", "WATCH"}]
        if symbols:
            return symbols[:limit]
    except Exception:
        pass
    return db.scalars(select(Stock.symbol).where(Stock.is_active.is_(True)).order_by(Stock.symbol).limit(limit)).all()


def run_strategy_universe(
    db: Session,
    settings: Settings | None = None,
    limit: int | None = None,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    limit = limit or settings.strategy_symbol_limit
    provider_chain = build_provider_chain(settings)
    selected = [symbol.upper() for symbol in symbols] if symbols else _candidate_symbols(db, settings, limit)
    rows = [
        run_strategy_for_symbol(db, symbol, settings=settings, provider_chain=provider_chain)
        for symbol in selected[:limit]
    ]
    rows.sort(key=lambda row: (row["strategy_action"] == "BUY", row["strategy_score"], row["buy_timeframes"]), reverse=True)
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "timeframes": settings.strategy_timeframe_list,
        "rows": rows,
        "disclaimer": DISCLAIMER,
    }
