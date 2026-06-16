from __future__ import annotations

import pandas as pd


def support_resistance(df: pd.DataFrame, lookback: int = 60) -> dict[str, float | None]:
    if df is None or df.empty:
        return {"support": None, "resistance": None}
    window = df.tail(max(5, lookback))
    support = float(window["low"].min()) if "low" in window else None
    resistance = float(window["high"].max()) if "high" in window else None
    return {"support": support, "resistance": resistance}


def breakout_state(df: pd.DataFrame, lookback: int = 40) -> str:
    if df is None or len(df) < 3:
        return "NONE"
    prev = df.iloc[:-1].tail(max(5, lookback))
    if prev.empty:
        return "NONE"
    last = df.iloc[-1]
    resistance = prev["high"].max()
    support = prev["low"].min()
    if last["close"] > resistance:
        return "BULLISH_BREAKOUT"
    if last["close"] < support:
        return "BEARISH_BREAKDOWN"
    return "NONE"

