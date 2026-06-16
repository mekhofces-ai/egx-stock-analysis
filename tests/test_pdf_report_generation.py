from __future__ import annotations

from datetime import date
from pathlib import Path

from app.services import daily_file_report
from tests.test_daily_file_report import FakeSession, sample_report_data


def test_pdf_failure_does_not_block_excel(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(daily_file_report, "_existing_file_report", lambda db, day: None)
    monkeypatch.setattr(daily_file_report, "collect_report_data", lambda db, day: sample_report_data())
    monkeypatch.setattr(daily_file_report, "generate_pdf_report", lambda db, rows, filepath, morning_review=None: (_ for _ in ()).throw(RuntimeError("pdf unavailable")))
    result = daily_file_report.generate_daily_file_report(
        report_date=date(2026, 6, 7),
        reports_dir=tmp_path,
        db=FakeSession(),
    )
    assert result["excel_created"] is True
    assert result["pdf_created"] is False
    assert "PDF failed" in result["error_message"]
