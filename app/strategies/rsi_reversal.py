from __future__ import annotations

import pandas as pd

from app.strategies.base_strategy import BaseStrategy, StrategyResultDTO
from app.technical.indicators import add_indicators


class RSIReversalStrategy(BaseStrategy):
    name = "RSI Reversal"

    def analyze(self, symbol: str, df: pd.DataFrame) -> StrategyResultDTO:
        if df is None or len(df) < 30:
            return self._result(symbol, "HOLD", 50, None, None, "Insufficient candles for RSI reversal.")
        data = add_indicators(df)
        last, prev = data.iloc[-1], data.iloc[-2]
        score = 50.0
        if prev["rsi14"] < 35 and last["rsi14"] > prev["rsi14"] and last["close"] > last["open"]:
            score = 76.0
        elif last["rsi14"] > 72 and last["close"] < last["open"]:
            score = 28.0
        signal = "BUY" if score >= 70 else "SELL" if score <= 35 else "HOLD"
        stop = float(data.tail(10)["low"].min()) if signal == "BUY" else float(last["close"] * 0.97)
        return self._result(symbol, signal, score, float(last["close"]), stop, "RSI reversal and latest candle confirmation.", {"rsi": float(last["rsi14"])})

