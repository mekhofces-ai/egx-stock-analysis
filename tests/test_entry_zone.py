from __future__ import annotations

import pandas as pd

from app.services.daily_stock_report import calculate_entry_zone


def _sample_frame(rows: int = 90) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=rows, freq="D")
    close = pd.Series([20 + idx * 0.08 for idx in range(rows)])
    return pd.DataFrame(
        {
            "datetime": dates,
            "open": close - 0.03,
            "high": close + 0.25,
            "low": close - 0.25,
            "close": close,
            "volume": [100_000 + idx * 500 for idx in range(rows)],
        }
    )


def test_entry_zone_calculates_stop_targets_and_risk_reward() -> None:
    zone = calculate_entry_zone(_sample_frame())
    assert zone["valid"] is True
    assert zone["entry_zone_low"] < zone["entry_zone_high"]
    assert zone["stop_loss"] < zone["entry_zone_low"]
    assert zone["target_1"] > zone["entry_zone_high"]
    assert zone["risk_reward"] > 0


def test_entry_zone_requires_sufficient_data() -> None:
    zone = calculate_entry_zone(_sample_frame(10))
    assert zone["valid"] is False
    assert "Insufficient" in zone["reason"]
