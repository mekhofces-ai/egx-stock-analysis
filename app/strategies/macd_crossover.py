from __future__ import annotations

import pandas as pd

from app.strategies.base_strategy import BaseStrategy, StrategyResultDTO
from app.technical.indicators import add_indicators


class MACDCrossoverStrategy(BaseStrategy):
    name = "MACD Crossover"

    def analyze(self, symbol: str, df: pd.DataFrame) -> StrategyResultDTO:
        if df is None or len(df) < 40:
            return self._result(symbol, "HOLD", 50, None, None, "Insufficient candles for MACD crossover.")
        data = add_indicators(df)
        last, prev = data.iloc[-1], data.iloc[-2]
        crossed_up = prev["macd"] <= prev["macd_signal"] and last["macd"] > last["macd_signal"]
        crossed_down = prev["macd"] >= prev["macd_signal"] and last["macd"] < last["macd_signal"]
        score = 78.0 if crossed_up else 25.0 if crossed_down else 55.0 if last["macd_hist"] > 0 else 45.0
        signal = "BUY" if score >= 70 else "SELL" if score <= 35 else "HOLD"
        return self._result(symbol, signal, score, float(last["close"]), float(last["close"] - (last["atr14"] or 0) * 1.5), "MACD crossover or histogram bias.", {"hist": float(last["macd_hist"])})

