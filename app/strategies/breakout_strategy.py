from __future__ import annotations

import pandas as pd

from app.strategies.base_strategy import BaseStrategy, StrategyResultDTO
from app.technical.indicators import add_indicators
from app.technical.support_resistance import breakout_state


class BreakoutStrategy(BaseStrategy):
    name = "Breakout Strategy"

    def analyze(self, symbol: str, df: pd.DataFrame) -> StrategyResultDTO:
        if df is None or len(df) < 45:
            return self._result(symbol, "HOLD", 50, None, None, "Insufficient candles for breakout strategy.")
        data = add_indicators(df)
        last = data.iloc[-1]
        state = breakout_state(data, lookback=40)
        volume_ok = bool(last["volume_ma20"] and last["volume"] > last["volume_ma20"] * 1.25)
        if state == "BULLISH_BREAKOUT" and volume_ok:
            score = 84.0
        elif state == "BEARISH_BREAKDOWN":
            score = 22.0
        else:
            score = 52.0
        signal = "BUY" if score >= 70 else "SELL" if score <= 35 else "HOLD"
        return self._result(symbol, signal, score, float(last["close"]), float(data.tail(20)["low"].min()), f"Breakout state: {state}; volume confirmation: {volume_ok}.", {"state": state})

