from __future__ import annotations

from app.services.system_health_check import format_health_rows, run_health_check


def test_system_health_check_returns_core_components() -> None:
    rows = run_health_check(save_log=False)
    components = {row["component"] for row in rows}
    assert "Config" in components
    assert "Database" in components
    assert "Trading Safety" in components
    assert "Scheduler" in components
    assert "app.services.daily_file_report" in components


def test_health_check_format_is_readable() -> None:
    text = format_health_rows([{"component": "Config", "status": "OK", "message": "done"}])
    assert "[OK] Config: done" in text
