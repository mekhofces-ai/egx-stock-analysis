from __future__ import annotations

import pandas as pd


def detect_patterns(df: pd.DataFrame) -> list[str]:
    if df is None or len(df) < 2:
        return []
    prev = df.iloc[-2]
    last = df.iloc[-1]
    patterns: list[str] = []
    body = abs(last["close"] - last["open"])
    candle_range = max(float(last["high"] - last["low"]), 0.0001)
    upper_shadow = float(last["high"] - max(last["close"], last["open"]))
    lower_shadow = float(min(last["close"], last["open"]) - last["low"])
    if body / candle_range < 0.15:
        patterns.append("doji")
    if lower_shadow > body * 2 and upper_shadow < body * 1.2:
        patterns.append("hammer")
    if last["close"] > last["open"] and prev["close"] < prev["open"] and last["close"] >= prev["open"] and last["open"] <= prev["close"]:
        patterns.append("bullish_engulfing")
    if last["close"] < last["open"] and prev["close"] > prev["open"] and last["open"] >= prev["close"] and last["close"] <= prev["open"]:
        patterns.append("bearish_engulfing")
    return patterns

