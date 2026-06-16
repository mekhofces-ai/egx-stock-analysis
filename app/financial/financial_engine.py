from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.market_data import latest_price
from app.financial.financial_ratios import calculate_ratios
from app.models import FinancialData, FinancialSignal


def _bound(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _score_ratios(ratios: dict[str, float | None]) -> dict[str, float]:
    profitability = 0.0
    if (ratios.get("gross_margin") or 0) > 0.25:
        profitability += 8
    if (ratios.get("net_profit_margin") or 0) > 0.1:
        profitability += 8
    if (ratios.get("roe") or 0) > 0.12:
        profitability += 6
    if (ratios.get("roa") or 0) > 0.05:
        profitability += 3

    growth = 0.0
    if (ratios.get("revenue_growth") or 0) > 5:
        growth += 12
    if (ratios.get("net_profit_growth") or 0) > 5:
        growth += 13

    valuation = 10.0
    pe = ratios.get("pe_ratio")
    pb = ratios.get("pb_ratio")
    if pe and 0 < pe <= 12:
        valuation += 6
    elif pe and pe > 25:
        valuation -= 4
    if pb and 0 < pb <= 2:
        valuation += 4

    debt_score = 15.0
    debt_to_equity = ratios.get("debt_to_equity")
    if debt_to_equity and debt_to_equity > 2:
        debt_score = 6.0
    elif debt_to_equity and debt_to_equity > 1:
        debt_score = 10.0

    cashflow = 7.5
    if (ratios.get("cash_flow_quality") or 0) >= 1:
        cashflow = 15.0
    elif (ratios.get("cash_flow_quality") or 0) <= 0:
        cashflow = 3.0

    return {
        "profitability_score": _bound(profitability, 0, 25),
        "growth_score": _bound(growth, 0, 25),
        "valuation_score": _bound(valuation, 0, 20),
        "debt_score": _bound(debt_score, 0, 15),
        "cashflow_score": _bound(cashflow, 0, 15),
    }


def analyze_financial(db: Session, symbol: str, *, persist: bool = True) -> dict[str, Any]:
    rows = db.scalars(
        select(FinancialData).where(FinancialData.symbol == symbol).order_by(FinancialData.period.desc(), FinancialData.created_at.desc()).limit(2)
    ).all()
    if not rows:
        result = {
            "symbol": symbol,
            "financial_signal": "NEUTRAL",
            "financial_score": 50.0,
            "reason": "No stored financial data yet. Upload financial CSV data to improve this score.",
            "risk_level": "MEDIUM",
            "ratios": {},
        }
        if persist:
            db.add(FinancialSignal(symbol=symbol, financial_signal="NEUTRAL", financial_score=50.0, reason=result["reason"], risk_level="MEDIUM"))
        return result

    current = rows[0]
    previous = rows[1] if len(rows) > 1 else None
    ratios = calculate_ratios(current, previous, latest_price(db, symbol))
    components = _score_ratios(ratios)
    total = _bound(sum(components.values()))
    signal = "BULLISH" if total >= 70 else "BEARISH" if total < 40 else "NEUTRAL"
    risk = "LOW" if components["debt_score"] >= 12 and total >= 65 else "HIGH" if components["debt_score"] < 8 else "MEDIUM"
    reason = (
        f"Profitability {components['profitability_score']:.0f}/25, growth {components['growth_score']:.0f}/25, "
        f"valuation {components['valuation_score']:.0f}/20, debt {components['debt_score']:.0f}/15, cash flow {components['cashflow_score']:.0f}/15."
    )
    if persist:
        db.add(
            FinancialSignal(
                symbol=symbol,
                financial_signal=signal,
                financial_score=total,
                profitability_score=components["profitability_score"],
                growth_score=components["growth_score"],
                valuation_score=components["valuation_score"],
                debt_score=components["debt_score"],
                cashflow_score=components["cashflow_score"],
                reason=reason,
                risk_level=risk,
            )
        )
    return {"symbol": symbol, "financial_signal": signal, "financial_score": total, "reason": reason, "risk_level": risk, "ratios": ratios, **components}

