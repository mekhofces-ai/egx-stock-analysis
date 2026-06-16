from __future__ import annotations

import pandas as pd


OHLCV_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]


def normalize_symbol(symbol: str | None) -> str:
    if not symbol:
        return ""
    value = str(symbol).upper().strip()
    value = value.replace("EGX:", "").replace(".CA", "")
    return "".join(ch for ch in value if ch.isalnum() or ch == "_")


def clean_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    out = df.copy()
    if "timestamp" in out.columns and "datetime" not in out.columns:
        out = out.rename(columns={"timestamp": "datetime"})
    missing = [col for col in OHLCV_COLUMNS if col not in out.columns]
    for col in missing:
        out[col] = None
    out = out[OHLCV_COLUMNS]
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["datetime", "close"]).sort_values("datetime")
    out = out.drop_duplicates(subset=["datetime"], keep="last")
    out = out.set_index("datetime", drop=False)
    return out


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    cleaned = clean_ohlcv_frame(df)
    if cleaned.empty:
        return cleaned
    rule_map = {
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1D",
        "1D": "1D",
        "D": "1D",
    }
    pandas_rule = rule_map.get(rule, rule)
    resampled = cleaned.resample(pandas_rule).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    resampled = resampled.dropna(subset=["open", "high", "low", "close"])
    resampled["datetime"] = resampled.index
    return resampled[OHLCV_COLUMNS]

