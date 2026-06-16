from __future__ import annotations

import pandas as pd

from app.technical.indicators import add_indicators, rsi
from app.strategies.trend_following import TrendFollowingStrategy


def _sample_ohlcv(rows: int = 80) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=rows, freq="D")
    close = pd.Series([10 + idx * 0.1 for idx in range(rows)])
    return pd.DataFrame(
        {
            "datetime": dates,
            "open": close - 0.05,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": [1000 + idx * 10 for idx in range(rows)],
        }
    )


def test_indicators_are_added() -> None:
    df = add_indicators(_sample_ohlcv())
    assert {"sma20", "ema20", "rsi14", "macd_hist", "atr14", "adx"}.issubset(df.columns)
    assert df["rsi14"].iloc[-1] >= 50


def test_rsi_bounds() -> None:
    values = rsi(pd.Series([1, 2, 3, 2, 4, 5, 6, 7]), 14)
    assert values.between(0, 100).all()


def test_trend_following_returns_signal() -> None:
    result = TrendFollowingStrategy().analyze("COMI", _sample_ohlcv())
    assert result.signal in {"BUY", "HOLD", "SELL"}
    assert 0 <= result.score <= 100

