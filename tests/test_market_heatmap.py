from __future__ import annotations

import pandas as pd

from app.services.market_heatmap import top_gainer_loser_summary


def test_top_gainer_loser_summary() -> None:
    data = {
        "top_gainers": pd.DataFrame([{"symbol": "AAA", "change_percent": 5.5}]),
        "top_losers": pd.DataFrame([{"symbol": "BBB", "change_percent": -3.2}]),
    }
    summary = top_gainer_loser_summary(data)
    assert summary["top_gainer"]["symbol"] == "AAA"
    assert summary["top_loser"]["symbol"] == "BBB"
