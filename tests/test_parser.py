from app.services.parser import parse_message


def test_parse_english_buy_signal() -> None:
    signal = parse_message(
        "BUY COMI entry 100 target 110 118 stop loss 95 daily breakout",
        known_symbols=["COMI", "HRHO"],
    )

    assert signal.stock_symbol == "COMI"
    assert signal.direction == "BUY"
    assert signal.entry_price == 100
    assert signal.targets == [110, 118]
    assert signal.stop_loss == 95
    assert signal.timeframe == "daily"
    assert "missing_stop_loss" not in signal.risk_flags


def test_parse_arabic_signal_with_hype_flag() -> None:
    signal = parse_message(
        "شراء FWRY دخول 7.50 هدف 8.20 وقف خسارة 7.10 صاروخ",
        known_symbols=["COMI", "FWRY"],
    )

    assert signal.stock_symbol == "FWRY"
    assert signal.direction == "BUY"
    assert signal.entry_price == 7.50
    assert signal.targets == [8.20]
    assert signal.stop_loss == 7.10
    assert "صاروخ" in signal.hype_words
    assert "hype_or_pump_language" in signal.risk_flags


def test_missing_stop_loss_is_flagged_for_buy() -> None:
    signal = parse_message("buy HRHO target 26 urgent", known_symbols=["HRHO"])

    assert signal.stock_symbol == "HRHO"
    assert signal.direction == "BUY"
    assert "missing_stop_loss" in signal.risk_flags
    assert "urgent" in signal.hype_words
