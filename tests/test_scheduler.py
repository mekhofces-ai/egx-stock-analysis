from __future__ import annotations

import pytest

from app.config import REPORT_TIMEZONE
from app.services import scheduler


def test_report_type_for_time() -> None:
    assert scheduler.report_type_for_time("09:00") == "morning"
    assert scheduler.report_type_for_time("21:00") == "evening"


def test_scheduler_uses_cairo_timezone(monkeypatch) -> None:
    if scheduler.BackgroundScheduler is None:
        pytest.skip("APScheduler is not installed")
    monkeypatch.setattr(scheduler, "_daily_report_times_from_db", lambda: ["09:00", "21:00"])
    monkeypatch.setattr(scheduler, "_daily_file_report_time_from_db", lambda: "15:00")
    monkeypatch.setattr(scheduler, "_daily_dynamic_refresh_time_from_db", lambda: "14:30")
    monkeypatch.setattr(scheduler, "_recommendation_re_evaluation_time_from_db", lambda: "15:30")
    jobs = scheduler.create_daily_report_scheduler().get_jobs()
    job_ids = {job.id for job in jobs}
    assert {
        "daily_stock_report_morning",
        "daily_stock_report_evening",
        "daily_file_report",
        "daily_dynamic_refresh",
        "recommendation_re_evaluation",
    }.issubset(job_ids)
    assert all(REPORT_TIMEZONE in str(job.trigger.timezone) for job in jobs)
