"""Tests for the pre-trade validator and enhanced reporting."""

from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.services.pre_trade_validator import (
    ACTION_STAGES,
    MAX_ALERTS_PER_STOCK_PER_DAY,
    MAX_BUY_ALERTS_PER_DAY,
    NoTradeFilterResult,
    PreTradeResult,
    RecommendationStage,
    analyze_market_condition,
    check_alert_policy,
    check_no_trade_filters,
    classify_stage,
    market_regime_downgrade,
    pre_trade_validate,
    should_send_alert,
)


def test_recommendation_stages_enum():
    """All required stages exist."""
    stages = [s.value for s in RecommendationStage]
    assert "WATCH" in stages
    assert "NEAR ENTRY" in stages
    assert "ENTRY CONFIRMED" in stages
    assert "BUY" in stages
    assert "STRONG BUY" in stages
    assert "AVOID" in stages


def test_action_stages_correct():
    """Only actionable stages are in ACTION_STAGES."""
    action_values = {s.value for s in ACTION_STAGES}
    assert "ENTRY CONFIRMED" in action_values
    assert "BUY" in action_values
    assert "STRONG BUY" in action_values
    assert "WATCH" not in action_values
    assert "NEAR ENTRY" not in action_values


def test_max_buy_alerts_per_day():
    """MAX_BUY_ALERTS_PER_DAY is reasonable."""
    assert MAX_BUY_ALERTS_PER_DAY == 5


def test_max_alerts_per_stock_per_day():
    """MAX_ALERTS_PER_STOCK_PER_DAY is reasonable."""
    assert MAX_ALERTS_PER_STOCK_PER_DAY == 2


def test_market_regime_downgrade_bearish_low_score():
    """Bearish market downgrades BUY with score <70 to WATCH."""
    stage, impact = market_regime_downgrade("bearish", RecommendationStage.BUY, 65.0)
    assert stage == RecommendationStage.WATCH
    assert "Bearish" in impact


def test_market_regime_downgrade_bearish_high_score():
    """Bearish market keeps strong BUY if score >=90."""
    stage, impact = market_regime_downgrade("bearish", RecommendationStage.STRONG_BUY, 92.0)
    assert stage == RecommendationStage.STRONG_BUY


def test_market_regime_downgrade_high_volatility():
    """High volatility downgrades BUY to ENTRY CONFIRMED if score >=85."""
    stage, impact = market_regime_downgrade("high_volatility", RecommendationStage.STRONG_BUY, 88.0)
    assert stage == RecommendationStage.ENTRY_CONFIRMED


def test_market_regime_downgrade_sideways():
    """Sideways downgrades STRONG BUY to BUY."""
    stage, impact = market_regime_downgrade("sideways", RecommendationStage.STRONG_BUY, 80.0)
    assert stage == RecommendationStage.BUY


def test_no_trade_filter_rejects_no_entry():
    """Missing entry price triggers rejection."""
    row = {"final_score": 75.0, "target_price": 12.0, "stop_loss": 9.0}
    result = check_no_trade_filters(MagicMock(spec=Session), "TEST", row, current_price=10.0)
    assert not result.entry_validation["has_entry"]
    assert not result.passed


def test_no_trade_filter_rejects_bad_rr():
    """Risk/reward below 1:2 is rejected."""
    row = {"final_score": 75.0, "entry_price": 100.0, "target_price": 101.0, "stop_loss": 99.0}
    result = check_no_trade_filters(MagicMock(spec=Session), "TEST", row, current_price=100.0)
    assert not result.entry_validation["risk_reward_ok"]
    assert not result.passed


def test_no_trade_filter_accepts_good_rr():
    """Risk/reward >= 1:2 passes."""
    row = {"final_score": 75.0, "entry_price": 100.0, "target_price": 120.0, "stop_loss": 95.0}
    result = check_no_trade_filters(MagicMock(spec=Session), "TEST", row, current_price=100.0)
    assert result.entry_validation["risk_reward_ok"]
    assert result.entry_validation["has_entry"]


def test_no_trade_filter_stop_too_wide():
    """Stop loss more than 15% from entry is rejected."""
    row = {"final_score": 75.0, "entry_price": 100.0, "target_price": 120.0, "stop_loss": 80.0}
    result = check_no_trade_filters(MagicMock(spec=Session), "TEST", row, current_price=100.0)
    assert not result.entry_validation["stop_realistic"]


def test_classify_stage_high_score_is_strong_buy():
    """Final score >=85 triggers STRONG BUY."""
    row = {"final_score": 90.0, "recommendation": "BUY", "entry_price": 10, "target_price": 12, "stop_loss": 9.5}
    market = {"regime": "bullish", "trend_score": 70, "market_score": 65, "liquidity_ok": True, "reason": ""}
    ntf = NoTradeFilterResult(passed=True, entry_validation={
        "has_entry": True, "has_target": True, "has_stop": True,
        "price_not_too_far": True, "risk_reward_ok": True,
        "stop_realistic": True, "target_realistic": True,
        "liquidity_ok": True, "technical_confirmed": True,
        "news_not_negative": True, "telegram_not_sole_reason": True,
    })
    result = classify_stage(row, market, ntf)
    assert result.stage == RecommendationStage.STRONG_BUY
    assert result.passed


def test_classify_stage_low_score_is_avoid():
    """Final score <55 is AVOID."""
    row = {"final_score": 40.0, "recommendation": "WATCH", "entry_price": 10, "target_price": 12, "stop_loss": 9.5}
    market = {"regime": "bullish", "trend_score": 70, "market_score": 65, "liquidity_ok": True, "reason": ""}
    ntf = NoTradeFilterResult(passed=True, entry_validation={
        "has_entry": True, "has_target": True, "has_stop": True,
        "price_not_too_far": True, "risk_reward_ok": True,
        "stop_realistic": True, "target_realistic": True,
        "liquidity_ok": True, "technical_confirmed": True,
        "news_not_negative": True, "telegram_not_sole_reason": True,
    })
    result = classify_stage(row, market, ntf)
    assert result.stage == RecommendationStage.AVOID
    assert not result.passed


def test_classify_stage_moderate_is_near_entry():
    """Score 55-69 with entry zone is NEAR ENTRY."""
    row = {"final_score": 62.0, "recommendation": "WATCH", "entry_price": 10, "target_price": 12, "stop_loss": 9.5}
    market = {"regime": "bullish", "trend_score": 70, "market_score": 65, "liquidity_ok": True, "reason": ""}
    ntf = NoTradeFilterResult(passed=True, entry_validation={
        "has_entry": True, "has_target": True, "has_stop": True,
        "price_not_too_far": True, "risk_reward_ok": True,
        "stop_realistic": True, "target_realistic": True,
        "liquidity_ok": True, "technical_confirmed": True,
        "news_not_negative": True, "telegram_not_sole_reason": True,
    })
    result = classify_stage(row, market, ntf)
    assert result.stage == RecommendationStage.NEAR_ENTRY
    assert result.passed


def test_classify_stage_no_filter_rejected():
    """If no-trade filter fails, result is not passed."""
    row = {"final_score": 90.0, "recommendation": "BUY"}
    market = {"regime": "bullish", "trend_score": 70, "market_score": 65, "liquidity_ok": True, "reason": ""}
    ntf = NoTradeFilterResult(passed=False, reasons=["No entry price"])
    result = classify_stage(row, market, ntf)
    assert not result.passed


def test_should_send_alert_watch_rejected():
    """WATCH stage returns False from should_send_alert."""
    result = PreTradeResult(
        stage=RecommendationStage.WATCH, passed=True,
        market_regime="bullish", confidence_score=65.0,
        final_action="WATCHLIST",
    )
    ok, reason = should_send_alert(MagicMock(spec=Session), "TEST", result)
    assert not ok
    assert "not an action stage" in reason


@patch("app.services.pre_trade_validator.get_daily_alert_counts")
def test_should_send_alert_entry_confirmed_allowed(mock_counts):
    """ENTRY CONFIRMED passes the stage check."""
    mock_counts.return_value = {"total_buy_alerts": 0, "per_stock": {}}
    result = PreTradeResult(
        stage=RecommendationStage.ENTRY_CONFIRMED, passed=True,
        market_regime="bullish", confidence_score=80.0,
        final_action="ACTION REQUIRED",
    )
    ok, reason = should_send_alert(MagicMock(spec=Session), "TEST", result)
    assert ok
    assert reason == ""


def test_should_send_alert_not_passed_fails():
    """Not-passed result returns False."""
    result = PreTradeResult(
        stage=RecommendationStage.AVOID, passed=False,
        market_regime="bearish", confidence_score=40.0,
        final_action="AVOID",
        reasons=["Low score"],
    )
    ok, reason = should_send_alert(MagicMock(spec=Session), "TEST", result)
    assert not ok
    assert "failed" in reason


def test_market_regime_downgrade_bearish_buy_70_80():
    """Bearish market downgrades BUY score 70-80 to NEAR ENTRY."""
    stage, impact = market_regime_downgrade("bearish", RecommendationStage.BUY, 75.0)
    assert stage == RecommendationStage.NEAR_ENTRY


def test_market_regime_high_vol_low_score():
    """High volatility with score <85 goes to WATCH."""
    stage, impact = market_regime_downgrade("high_volatility", RecommendationStage.BUY, 70.0)
    assert stage == RecommendationStage.WATCH


def test_market_regime_bullish_no_downgrade():
    """Bullish market does not downgrade BUY."""
    stage, impact = market_regime_downgrade("bullish", RecommendationStage.BUY, 75.0)
    assert stage == RecommendationStage.BUY
    assert impact == ""
