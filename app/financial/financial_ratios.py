from __future__ import annotations


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def pct_growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (float(current) - float(previous)) / abs(float(previous)) * 100


def calculate_ratios(current: object, previous: object | None = None, market_price: float | None = None) -> dict[str, float | None]:
    revenue = getattr(current, "revenue", None)
    net_profit = getattr(current, "net_profit", None)
    gross_profit = getattr(current, "gross_profit", None)
    assets = getattr(current, "assets", None)
    liabilities = getattr(current, "liabilities", None)
    equity = getattr(current, "equity", None)
    debt = getattr(current, "debt", None)
    cash_flow = getattr(current, "cash_flow", None)
    eps = getattr(current, "eps", None)
    shares = getattr(current, "shares_outstanding", None)
    price = market_price or getattr(current, "market_price", None)
    book_value_per_share = safe_div(equity, shares)
    return {
        "revenue_growth": pct_growth(revenue, getattr(previous, "revenue", None) if previous else None),
        "net_profit_growth": pct_growth(net_profit, getattr(previous, "net_profit", None) if previous else None),
        "gross_margin": safe_div(gross_profit, revenue),
        "net_profit_margin": safe_div(net_profit, revenue),
        "eps": eps,
        "pe_ratio": safe_div(price, eps),
        "pb_ratio": safe_div(price, book_value_per_share),
        "debt_to_equity": safe_div(debt if debt is not None else liabilities, equity),
        "roe": safe_div(net_profit, equity),
        "roa": safe_div(net_profit, assets),
        "cash_flow_quality": safe_div(cash_flow, net_profit),
    }

