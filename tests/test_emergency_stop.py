from __future__ import annotations

from app.config import AUDIT_MODE, EMERGENCY_STOP_TRADING, LIVE_TRADING_ENABLED


def test_live_trading_defaults_disabled_for_audit() -> None:
    assert LIVE_TRADING_ENABLED is False
    assert AUDIT_MODE is True
    assert EMERGENCY_STOP_TRADING is True
