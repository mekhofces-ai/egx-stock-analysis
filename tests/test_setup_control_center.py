from __future__ import annotations

from dashboard.pages.setup_control_center import _group_settings


def test_group_settings_automation() -> None:
    rows = [
        {"key": "automation_enabled", "value": "true", "value_type": "bool"},
        {"key": "automation_interval_seconds", "value": "120", "value_type": "int"},
    ]
    groups = _group_settings(rows)
    assert "Automation" in groups
    assert len(groups["Automation"]) == 2


def test_group_settings_weights() -> None:
    rows = [
        {"key": "combined_weight_telegram", "value": "20", "value_type": "float"},
        {"key": "final_weight_technical", "value": "35", "value_type": "float"},
    ]
    groups = _group_settings(rows)
    assert "Weights" in groups
    assert len(groups["Weights"]) == 2


def test_group_settings_portfolio() -> None:
    rows = [
        {"key": "portfolio_bot_enabled", "value": "false", "value_type": "bool"},
    ]
    groups = _group_settings(rows)
    assert "Portfolio" in groups


def test_group_settings_risk_guards() -> None:
    rows = [
        {"key": "max_daily_loss_pct", "value": "0.03", "value_type": "float"},
        {"key": "risk_guard_max_drawdown_pct", "value": "15", "value_type": "float"},
    ]
    groups = _group_settings(rows)
    assert "Risk Guards" in groups


def test_group_settings_general() -> None:
    rows = [
        {"key": "some_unknown_setting", "value": "x", "value_type": "string"},
    ]
    groups = _group_settings(rows)
    assert any(v for v in groups.values())


def test_group_settings_empty() -> None:
    groups = _group_settings([])
    assert groups == {}
