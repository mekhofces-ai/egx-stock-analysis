from __future__ import annotations

from datetime import datetime, timedelta

from app.models import FinalStockDecision, Opportunity, Stock, StockCombinedAnalysis
from app.services.ai_analysis_service import (
    _score_to_signal,
    get_ai_analysis_for_symbol,
    get_final_details,
    get_market_overview,
    latest_final_decision,
    latest_opportunity,
)


def test_score_to_signal() -> None:
    assert _score_to_signal(85) == "STRONG BUY"
    assert _score_to_signal(70) == "BUY"
    assert _score_to_signal(55) == "WATCH"
    assert _score_to_signal(42) == "NEUTRAL"
    assert _score_to_signal(25) == "AVOID"
    assert _score_to_signal(10) == "SELL"


def test_get_final_details_with_decision() -> None:
    decision = FinalStockDecision(
        symbol="COMI",
        final_signal="BUY",
        final_score=75.0,
        technical_score=80.0,
        financial_score=70.0,
        news_score=65.0,
        telegram_score=60.0,
        strategy_score=72.0,
        liquidity_score=50.0,
        sector_score=5.0,
        market_regime="bullish",
        entry_price=50.0,
        stop_loss=45.0,
        take_profit_1=55.0,
        take_profit_2=60.0,
        risk_level="MEDIUM",
        reason="Strong across all dimensions",
        best_analysis_today="technical",
        best_strategy_today="cli_v6",
    )
    result = get_final_details(decision)
    assert result["signal"] == "BUY"
    assert result["score"] == 75.0
    assert result["market_regime"] == "bullish"
    assert result["risk_level"] == "MEDIUM"


def test_get_final_details_none() -> None:
    result = get_final_details(None)
    assert result["signal"] == "WATCH"
    assert result["score"] is None
    assert result["risk_level"] is None


def test_get_market_overview_empty() -> None:
    overview = get_market_overview()
    assert isinstance(overview, dict)
    assert "total_symbols" in overview


def test_latest_final_decision_none() -> None:
    from app.database import SessionLocal
    with SessionLocal() as db:
        result = latest_final_decision(db, "NONEXISTENT")
    assert result is None


def test_latest_opportunity_none() -> None:
    from app.database import SessionLocal
    with SessionLocal() as db:
        result = latest_opportunity(db, "NONEXISTENT")
    assert result is None
