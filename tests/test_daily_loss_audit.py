from __future__ import annotations

from datetime import date

from app.services.daily_loss_audit import parse_audit_date


def test_parse_specific_audit_date() -> None:
    assert parse_audit_date("2026-06-07") == date(2026, 6, 7)
