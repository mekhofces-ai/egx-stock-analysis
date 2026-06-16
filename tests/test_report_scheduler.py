from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services import scheduler


def test_run_due_reports_once_runs_file_report_at_three_pm(monkeypatch) -> None:
    calls = {"file": 0}
    monkeypatch.setattr(scheduler, "daily_reports_enabled", lambda: False)
    monkeypatch.setattr(scheduler, "daily_file_report_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_daily_file_report_time_from_db", lambda: "15:00")

    def fake_file_report():
        calls["file"] += 1
        return {"status": "created"}

    monkeypatch.setattr(scheduler, "run_scheduled_file_report", fake_file_report)
    results = scheduler.run_due_reports_once(datetime(2026, 6, 7, 15, 0, tzinfo=ZoneInfo("Africa/Cairo")))
    assert calls["file"] == 1
    assert results == [{"status": "created"}]
