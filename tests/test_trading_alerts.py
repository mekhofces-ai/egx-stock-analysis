from __future__ import annotations

from app.services.trading_alerts import classify_position_alert, format_trading_alerts_message


def test_position_take_profit_alert_prefers_tp2() -> None:
    alert = classify_position_alert(current_price=15, stop_loss=9, take_profit_1=12, take_profit_2=14)
    assert alert is not None
    assert alert["alert_type"] == "TAKE PROFIT"
    assert alert["trigger"] == "TP2 reached"


def test_position_stop_loss_alert() -> None:
    alert = classify_position_alert(current_price=8.5, stop_loss=9, take_profit_1=12, take_profit_2=14)
    assert alert is not None
    assert alert["alert_type"] == "SELL"
    assert alert["trigger"] == "Stop loss hit"


def test_position_sell_alert_when_score_breaks_hold_threshold() -> None:
    alert = classify_position_alert(current_price=10, stop_loss=8, take_profit_1=12, take_profit_2=14, final_signal="WATCH", final_score=39)
    assert alert is not None
    assert alert["alert_type"] == "SELL"


def test_alert_message_contains_risk_note() -> None:
    message = format_trading_alerts_message([{"alert_type": "BUY", "symbol": "COMI", "confidence": 80, "final_score": 75}])
    assert "EGX Trading Alerts" in message
    assert "Risk Note:" in message
