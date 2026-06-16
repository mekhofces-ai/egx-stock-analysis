from __future__ import annotations

from app.services.backtest_cli_v6 import DEFAULT_COMMISSION, DEFAULT_SLIPPAGE
from app.services.backtest_engine import DEFAULT_COMMISSION_BPS, DEFAULT_SLIPPAGE_BPS


def test_backtest_defaults_include_more_realistic_costs() -> None:
    assert DEFAULT_COMMISSION == 0.0015
    assert DEFAULT_SLIPPAGE == 0.002
    assert DEFAULT_COMMISSION_BPS == 15.0
    assert DEFAULT_SLIPPAGE_BPS == 20.0
