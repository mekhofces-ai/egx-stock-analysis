from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from app.services.market_data.base import ProviderChain, ProviderUnavailable


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period).mean()


def _bound(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


@dataclass
class TechnicalSnapshot:
    symbol: str
    as_of: datetime
    timeframe: str
    indicators: dict[str, Any]
    trend_direction: str
    volatility_score: float
    liquidity_score: float
    technical_score: float
    risk_score: float
    support: float | None
    resistance: float | None
    breakout: bool
    provider: str
    is_mock: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "as_of": self.as_of,
            "timeframe": self.timeframe,
            "indicators": self.indicators,
            "trend_direction": self.trend_direction,
            "volatility_score": self.volatility_score,
            "liquidity_score": self.liquidity_score,
            "technical_score": self.technical_score,
            "risk_score": self.risk_score,
            "support": self.support,
            "resistance": self.resistance,
            "breakout": self.breakout,
            "provider": self.provider,
            "is_mock": self.is_mock,
        }


def calculate_from_ohlcv(symbol: str, df: pd.DataFrame, provider: str = "unknown", is_mock: bool = False) -> TechnicalSnapshot:
    if df.empty or len(df) < 30:
        raise ProviderUnavailable(f"Not enough OHLCV rows for {symbol}")

    frame = df.copy()
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    frame = frame.sort_values("date").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    close = frame["close"]
    volume = frame["volume"]
    frame["sma_20"] = close.rolling(20).mean()
    frame["sma_50"] = close.rolling(50).mean()
    frame["sma_200"] = close.rolling(200).mean()
    frame["ema_20"] = close.ewm(span=20, adjust=False).mean()
    frame["ema_50"] = close.ewm(span=50, adjust=False).mean()
    frame["rsi_14"] = _rsi(close, 14)
    frame["macd"] = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    frame["macd_signal"] = frame["macd"].ewm(span=9, adjust=False).mean()
    frame["bb_mid"] = frame["sma_20"]
    frame["bb_std"] = close.rolling(20).std()
    frame["bb_upper"] = frame["bb_mid"] + frame["bb_std"] * 2
    frame["bb_lower"] = frame["bb_mid"] - frame["bb_std"] * 2
    frame["atr_14"] = _atr(frame, 14)
    frame["avg_volume_20"] = volume.rolling(20).mean()
    frame["volume_ratio"] = volume / frame["avg_volume_20"].replace(0, np.nan)
    frame["support_20"] = frame["low"].rolling(20).min()
    frame["resistance_20"] = frame["high"].rolling(20).max()

    last = frame.iloc[-1]
    previous = frame.iloc[-2]
    last_close = float(last["close"])
    sma_50 = last["sma_50"]
    sma_200 = last["sma_200"]
    ema_20 = last["ema_20"]
    ema_50 = last["ema_50"]
    rsi = last["rsi_14"]
    atr = last["atr_14"]
    volume_ratio = last["volume_ratio"] if pd.notna(last["volume_ratio"]) else 1.0
    resistance = float(previous["resistance_20"]) if pd.notna(previous["resistance_20"]) else None
    support = float(previous["support_20"]) if pd.notna(previous["support_20"]) else None
    breakout = bool(resistance and last_close > resistance and volume_ratio >= 1.2)

    if pd.notna(sma_50) and pd.notna(sma_200) and last_close > sma_50 > sma_200 and ema_20 > ema_50:
        trend = "UPTREND"
    elif pd.notna(sma_50) and pd.notna(sma_200) and last_close < sma_50 < sma_200 and ema_20 < ema_50:
        trend = "DOWNTREND"
    else:
        trend = "RANGE"

    technical_score = 50.0
    if trend == "UPTREND":
        technical_score += 18
    elif trend == "DOWNTREND":
        technical_score -= 18
    if pd.notna(rsi):
        if 45 <= rsi <= 68:
            technical_score += 8
        elif rsi < 30:
            technical_score += 4
        elif rsi > 75:
            technical_score -= 10
    if last["macd"] > last["macd_signal"]:
        technical_score += 8
    else:
        technical_score -= 5
    if breakout:
        technical_score += 12
    if volume_ratio >= 1.5:
        technical_score += 7

    volatility_percent = (float(atr) / last_close * 100) if pd.notna(atr) and last_close else 0.0
    volatility_score = _bound(volatility_percent * 12)
    liquidity_score = _bound(float(volume_ratio) * 45 + 35)

    risk_score = 45.0
    risk_score += max(0, volatility_score - 40) * 0.45
    if pd.notna(rsi) and rsi > 72:
        risk_score += 12
    if resistance and abs(last_close - resistance) / last_close <= 0.02:
        risk_score += 10
    if trend == "DOWNTREND":
        risk_score += 15
    if liquidity_score < 45:
        risk_score += 10

    indicators = {
        "last_price": _safe_float(last_close),
        "sma_20": _safe_float(last["sma_20"]),
        "sma_50": _safe_float(sma_50),
        "sma_200": _safe_float(sma_200),
        "ema_20": _safe_float(ema_20),
        "ema_50": _safe_float(ema_50),
        "rsi_14": _safe_float(rsi),
        "macd": _safe_float(last["macd"]),
        "macd_signal": _safe_float(last["macd_signal"]),
        "bb_upper": _safe_float(last["bb_upper"]),
        "bb_mid": _safe_float(last["bb_mid"]),
        "bb_lower": _safe_float(last["bb_lower"]),
        "atr_14": _safe_float(atr),
        "volume": _safe_float(last["volume"]),
        "avg_volume_20": _safe_float(last["avg_volume_20"]),
        "volume_ratio": _safe_float(volume_ratio),
        "volume_spike": bool(volume_ratio >= 1.5),
    }

    as_of = pd.to_datetime(last["date"]).to_pydatetime()
    return TechnicalSnapshot(
        symbol=symbol.upper(),
        as_of=as_of,
        timeframe="1D",
        indicators=indicators,
        trend_direction=trend,
        volatility_score=volatility_score,
        liquidity_score=liquidity_score,
        technical_score=_bound(technical_score),
        risk_score=_bound(risk_score),
        support=_safe_float(support),
        resistance=_safe_float(resistance),
        breakout=breakout,
        provider=provider,
        is_mock=is_mock,
    )


def analyze_symbol(symbol: str, provider_chain: ProviderChain) -> TechnicalSnapshot:
    df = provider_chain.get_daily_ohlcv(symbol)
    provider = str(df.attrs.get("provider", "provider_chain"))
    is_mock = bool(df.attrs.get("is_mock", False))
    return calculate_from_ohlcv(symbol=symbol.upper(), df=df, provider=provider, is_mock=is_mock)

