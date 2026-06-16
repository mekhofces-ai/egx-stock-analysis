from __future__ import annotations

from app.telegram.telegram_analyzer import classify_message_type


def test_telegram_message_type_classification() -> None:
    assert classify_message_type("buy COMI target 90") == "recommendation"
    assert classify_message_type("important news about bank") == "news"
    assert classify_message_type("chart breakout", has_image=True) == "technical chart"

