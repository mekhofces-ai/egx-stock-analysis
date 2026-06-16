from __future__ import annotations

import pandas as pd

from app.strategies.base_strategy import BaseStrategy, StrategyResultDTO
from app.technical.indicators import add_indicators


class PullbackEMAStrategy(BaseStrategy):
    name = "Pullback EMA"

    def analyze(self, symbol: str, df: pd.DataFrame) -> StrategyResultDTO:
        if df is None or len(df) < 50:
            return self._result(symbol, "HOLD", 50, None, None, "Insufficient candles for EMA pullback.")
        data = add_indicators(df)
        last = data.iloc[-1]
        near_ema20 = abs(last["close"] - last["ema20"]) / max(last["close"], 0.0001) <= 0.025
        uptrend = last["ema20"] > last["ema50"]
        bullish_candle = last["close"] > last["open"]
        score = 80.0 if near_ema20 and uptrend and bullish_candle else 48.0
        signal = "BUY" if score >= 70 else "HOLD"
        return self._result(symbol, signal, score, float(last["close"]), float(last["ema50"]), "EMA pullback in trend.", {"near_ema20": bool(near_ema20), "uptrend": bool(uptrend)})

