from __future__ import annotations

from types import SimpleNamespace

from app.financial.financial_ratios import calculate_ratios, pct_growth


def test_financial_ratios() -> None:
    current = SimpleNamespace(revenue=120, gross_profit=60, net_profit=24, assets=300, liabilities=100, equity=200, debt=40, cash_flow=30, eps=2, shares_outstanding=100)
    previous = SimpleNamespace(revenue=100, net_profit=20)
    ratios = calculate_ratios(current, previous, market_price=10)
    assert ratios["revenue_growth"] == 20
    assert ratios["net_profit_margin"] == 0.2
    assert ratios["pe_ratio"] == 5


def test_pct_growth_handles_missing() -> None:
    assert pct_growth(10, 0) is None

