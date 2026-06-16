from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.services import daily_stock_report


def test_dry_run_does_not_send(monkeypatch) -> None:
    sent = {"called": False}

    def fake_payload(db, *, report_type, top_n, report_time=None):
        return {
            "report_type": report_type,
            "report_time": datetime(2026, 6, 7, 9, tzinfo=ZoneInfo("Africa/Cairo")),
            "items": [],
            "message": "dry run report",
        }

    def fake_send(message, retries=3):
        sent["called"] = True
        return 1

    monkeypatch.setattr(daily_stock_report, "generate_report_payload", fake_payload)
    monkeypatch.setattr(daily_stock_report, "_existing_sent_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(daily_stock_report, "send_report_message", fake_send)

    result = daily_stock_report.generate_daily_report(report_type="morning", dry_run=True, db=SimpleNamespace())
    assert result["dry_run"] is True
    assert sent["called"] is False


def test_missing_news_is_neutral() -> None:
    result = daily_stock_report.analyze_news_sentiment([])
    assert result["news_score"] == 50.0
    assert result["news_signal"] == "NEUTRAL"
    assert "No recent news" in result["reason"]
