from __future__ import annotations

from app.services.recommendation_validation import CONDITIONAL_BUY, AVOID, WATCH_ONLY, validate_recommendation


def _base_row(**overrides):
    row = {
        "signal": "BUY",
        "final_score": 82,
        "telegram_score": 70,
        "technical_score": 74,
        "strategy_score": 70,
        "news_score": 55,
        "backtest_score": 68,
        "risk_liquidity_score": 72,
        "entry_zone_low": 10,
        "entry_zone_high": 10.5,
        "stop_loss": 9.5,
        "risk_reward": 2.0,
    }
    row.update(overrides)
    return row


def test_strong_setup_becomes_conditional_buy() -> None:
    result = validate_recommendation(_base_row(), current_price=10.4, portfolio_value=100_000)
    assert result.signal == CONDITIONAL_BUY
    assert result.grade in {"A", "A+"}
    assert result.position_size and result.position_size > 0


def test_high_telegram_weak_technical_becomes_watch_only() -> None:
    result = validate_recommendation(_base_row(telegram_score=90, technical_score=55), current_price=10.4)
    assert result.signal == WATCH_ONLY
    assert any("Telegram attention" in reason for reason in result.no_trade_reasons)


def test_negative_news_blocks_buy() -> None:
    result = validate_recommendation(_base_row(news_score=25), current_price=10.4)
    assert result.signal == WATCH_ONLY
    assert any("news score is negative" in reason for reason in result.no_trade_reasons)


def test_low_liquidity_rejected() -> None:
    result = validate_recommendation(_base_row(risk_liquidity_score=30), current_price=10.4)
    assert result.signal == AVOID


def test_late_signal_waits_for_pullback() -> None:
    result = validate_recommendation(_base_row(), current_price=11.5)
    assert result.signal == "WAIT FOR PULLBACK"
