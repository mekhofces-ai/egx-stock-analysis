from __future__ import annotations

import pandas as pd

from dashboard.ui_components import filter_table_rows


def test_filter_table_rows_searches_all_columns() -> None:
    df = pd.DataFrame(
        [
            {"symbol": "COMI", "name": "Commercial International Bank", "signal": "BUY"},
            {"symbol": "EFIH", "name": "e-finance", "signal": "WATCH"},
            {"symbol": "HRHO", "name": "EFG Holding", "signal": "SELL"},
        ]
    )
    result = filter_table_rows(df, "finance watch")
    assert result["symbol"].tolist() == ["EFIH"]


def test_filter_table_rows_returns_original_for_empty_query() -> None:
    df = pd.DataFrame([{"symbol": "COMI"}])
    assert filter_table_rows(df, "").equals(df)
