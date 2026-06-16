from __future__ import annotations

import pandas as pd

from app.strategies.base_strategy import BaseStrategy, StrategyResultDTO
from app.technical.indicators import add_indicators


class VolumeBreakoutStrategy(BaseStrategy):
    name = "Volume Breakout"

    def analyze(self, symbol: str, df: pd.DataFrame) -> StrategyResultDTO:
        if df is None or len(df) < 30:
            return self._result(symbol, "HOLD", 50, None, None, "Insufficient candles for volume breakout.")
        data = add_indicators(df)
        last = data.iloc[-1]
        previous_high = data.iloc[:-1].tail(20)["high"].max()
        volume_surge = bool(last["volume_ma20"] and last["volume"] > last["volume_ma20"] * 1.5)
        price_break = bool(last["close"] > previous_high)
        score = 86.0 if volume_surge and price_break else 42.0 if last["close"] < last["ema20"] else 55.0
        signal = "BUY" if score >= 70 else "SELL" if score <= 35 else "HOLD"
        return self._result(symbol, signal, score, float(last["close"]), float(data.tail(10)["low"].min()), "Volume surge with price breakout.", {"volume_surge": volume_surge, "price_break": price_break})

