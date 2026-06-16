from __future__ import annotations

from app.services import trading_safety


def test_execution_block_reason_reports_emergency(monkeypatch) -> None:
    monkeypatch.setattr(
        trading_safety,
        "safety_snapshot",
        lambda db: {
            "audit_mode": True,
            "live_trading_enabled": True,
            "emergency_stop_trading": True,
            "daily_loss_pct": 0.0,
            "daily_loss_limit_pct": 5.0,
            "blocked_reasons": ["audit mode enabled", "emergency stop enabled"],
        },
    )
    reason = trading_safety.execution_block_reason(object(), block_paper_execution=True)
    assert "audit mode enabled" in reason
    assert "paper execution blocked" in reason
