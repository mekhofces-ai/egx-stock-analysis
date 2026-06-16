from __future__ import annotations

import pandas as pd

from app.strategies.base_strategy import BaseStrategy, StrategyResultDTO
from app.technical.indicators import add_indicators


class TrendFollowingStrategy(BaseStrategy):
    name = "Trend Following"

    def analyze(self, symbol: str, df: pd.DataFrame) -> StrategyResultDTO:
        if df is None or len(df) < 60:
            return self._result(symbol, "HOLD", 50, None, None, "Insufficient candles for trend following.")
        data = add_indicators(df)
        last = data.iloc[-1]
        score = 50.0
        score += 15 if last["close"] > last["sma50"] else -15
        score += 15 if last["ema20"] > last["ema50"] else -10
        score += 10 if last["adx"] > 20 and last["plus_di"] > last["minus_di"] else 0
        signal = "BUY" if score >= 70 else "SELL" if score <= 35 else "HOLD"
        stop = min(float(last["low"]), float(last["close"]) - float(last["atr14"] or 0) * 1.5)
        return self._result(symbol, signal, score, float(last["close"]), stop, "Trend alignment from SMA/EMA/ADX.", {"adx": float(last["adx"])})

