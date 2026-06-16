from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from app.services import daily_file_report


class FakeSession:
    def __init__(self) -> None:
        self.row = None

    def add(self, row) -> None:  # noqa: ANN001
        self.row = row

    def flush(self) -> None:
        if getattr(self, "row", None) is not None:
            self.row.id = 1

    def refresh(self, row) -> None:  # noqa: ANN001
        if getattr(self, "row", None) is not None:
            row.id = self.row.id

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def delete(self, row) -> None:  # noqa: ANN001
        return None


def sample_report_data() -> dict:
    return {
        "summary": pd.DataFrame([{"Metric": "Report Date", "Value": "2026-06-07"}]),
        "comparison_summary": pd.DataFrame(
            [
                {"Metric": "Recommendations Compared", "Value": 1},
                {"Metric": "Evaluated Rows", "Value": 1},
                {"Metric": "Win Rate %", "Value": "Accuracy is not reliable yet because evaluated sample size is too small."},
                {"Metric": "Target Hit Count", "Value": 1},
            ]
        ),
        "recommendation_vs_actual": pd.DataFrame(
            [
                {
                    "symbol": "COMI",
                    "recommendation_datetime": "2026-06-07 09:00:00",
                    "recommended_signal": "CONDITIONAL BUY",
                    "strategy_source": "daily_report",
                    "entry_zone_low": 78,
                    "entry_zone_high": 80,
                    "stop_loss": 76,
                    "target_1": 84,
                    "target_2": 88,
                    "signal_price": 79,
                    "next_available_open": 79.5,
                    "highest_price_after_signal": 85,
                    "lowest_price_after_signal": 78.5,
                    "latest_close": 84.5,
                    "actual_return_pct": 2.4,
                    "max_favorable_move_pct": 7.59,
                    "max_adverse_move_pct": -0.63,
                    "target_1_hit": True,
                    "stop_loss_hit": False,
                    "days_evaluated": 2,
                    "evaluation_status": "TARGET_HIT",
                    "final_quality": "Excellent",
                    "result": "GOOD_CALL",
                    "evaluation_quality": "MEDIUM_DAILY",
                    "root_cause": "Target reached before invalidation.",
                }
            ]
        ),
        "accuracy_by_stage": pd.DataFrame(
            [{"stage": "CONDITIONAL BUY", "total": 1, "evaluated": 1, "target_hit": 1, "stop_hit": 0, "win_rate_pct": None, "accuracy_note": "Not reliable yet; evaluated sample size is below 5.", "avg_return_pct": 2.4}]
        ),
        "accuracy_by_strategy": pd.DataFrame(
            [{"strategy_source": "daily_report", "total": 1, "evaluated": 1, "target_hit": 1, "stop_hit": 0, "win_rate_pct": None, "accuracy_note": "Not reliable yet; evaluated sample size is below 5.", "avg_return_pct": 2.4}]
        ),
        "missed_not_evaluated": pd.DataFrame(),
        "best_worst_trades": pd.DataFrame([{"symbol": "COMI", "actual_return_pct": 2.4, "evaluation_status": "TARGET_HIT"}]),
        "data_quality_check": pd.DataFrame([{"evaluation_status": "TARGET_HIT", "evaluation_quality": "MEDIUM_DAILY", "rows": 1, "reason": ""}]),
        "top_recommendations": pd.DataFrame(
            [
                {
                    "Symbol": "COMI",
                    "Company Name": "Commercial International Bank",
                    "Final Score": 82,
                    "Signal": "CONDITIONAL BUY",
                    "Grade": "A",
                    "Entry Zone": "78 - 80",
                    "Stop Loss": 76,
                    "Target 1": 84,
                    "Target 2": 88,
                    "Target 3": 92,
                    "Risk/Reward": 2.1,
                    "Reason": "Strong setup with validation.",
                }
            ]
        ),
        "audit": pd.DataFrame([{"Symbol": "COMI", "Result": "GOOD_CALL", "Root Cause": "-"}]),
        "errors": pd.DataFrame([{"Time": datetime(2026, 6, 7), "Module": "system", "Error Type": "none"}]),
        "backtest": pd.DataFrame([{"Symbol": "COMI", "Strategy": "CLI v6", "Win Rate": 62, "Backtest Score": 70}]),
        "telegram": pd.DataFrame([{"Symbol": "COMI", "Mentions": 3, "Telegram Score": 65}]),
        "_opportunities": [
            {"symbol": "COMI", "signal": "BUY", "final_score": 82, "entry_zone_low": 78,
             "entry_zone_high": 80, "stop_loss": 76, "target_1": 84, "target_2": 88,
             "target_3": 92, "risk_reward": 2.1, "explanation": "Strong setup"},
        ],
        "_morning_review": {},
    }


def test_daily_file_report_dry_run_does_not_write(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(daily_file_report, "_existing_file_report", lambda db, day: None)
    monkeypatch.setattr(daily_file_report, "collect_report_data", lambda db, day: sample_report_data())
    result = daily_file_report.generate_daily_file_report(
        report_date=date(2026, 6, 7),
        dry_run=True,
        reports_dir=tmp_path,
        db=FakeSession(),
    )
    assert result["status"] == "dry_run"
    assert not list(tmp_path.glob("*.xlsx"))


def test_daily_file_report_duplicate_is_skipped(monkeypatch, tmp_path: Path) -> None:
    existing = daily_file_report.DailyFileReport(
        id=5,
        report_date=datetime(2026, 6, 7),
        report_time=datetime(2026, 6, 7, 15),
        excel_path=str(tmp_path / "existing.xlsx"),
        pdf_path=None,
        excel_created=True,
        pdf_created=False,
        sent_to_telegram=False,
        status="created",
    )
    monkeypatch.setattr(daily_file_report, "_existing_file_report", lambda db, day: existing)
    result = daily_file_report.generate_daily_file_report(
        report_date=date(2026, 6, 7),
        reports_dir=tmp_path,
        db=FakeSession(),
    )
    assert result["status"] == "duplicate_skipped"
    assert result["report_id"] == 5


def test_existing_unsent_report_can_be_sent(monkeypatch, tmp_path: Path) -> None:
    existing = daily_file_report.DailyFileReport(
        id=6,
        report_date=datetime(2026, 6, 7),
        report_time=datetime(2026, 6, 7, 15),
        excel_path=str(tmp_path / "existing.xlsx"),
        pdf_path=None,
        excel_created=True,
        pdf_created=False,
        sent_to_telegram=False,
        status="created",
    )
    monkeypatch.setattr(daily_file_report, "_existing_file_report", lambda db, day: existing)
    monkeypatch.setattr(
        daily_file_report,
        "send_report_to_telegram",
        lambda row: {"sent_messages": 1, "sent_documents": 1},
    )
    result = daily_file_report.generate_daily_file_report(
        report_date=date(2026, 6, 7),
        send_telegram=True,
        reports_dir=tmp_path,
        db=FakeSession(),
    )
    assert result["status"] == "sent"
    assert result["sent_to_telegram"] is True


def test_comparison_summary_excludes_not_evaluated_and_missing_data() -> None:
    audit = {
        "audit_date": "2026-06-07",
        "summary": {"target_hit": 1, "stop_loss_hit": 0, "win_rate_pct": 100, "good_calls": 1, "bad_calls": 0},
        "items": [
            {"symbol": "COMI", "evaluation_status": "TARGET_HIT", "actual_return": 2.4, "max_favorable_move_pct": 4.0, "max_adverse_move_pct": -0.5},
            {"symbol": "AALR", "evaluation_status": "NOT_EVALUATED", "actual_return": 99.0},
            {"symbol": "HRHO", "evaluation_status": "DATA_MISSING", "actual_return": -99.0},
        ],
        "diagnosis": "sample",
    }
    rows, warnings = daily_file_report._comparison_summary(audit)
    summary = {row["Metric"]: row["Value"] for row in rows}
    assert summary["Evaluated Rows"] == 1
    assert summary["Not Evaluated"] == 1
    assert summary["Missing Data"] == 1
    assert summary["Average Actual Return %"] == 2.4
    assert "Accuracy is not reliable yet because evaluated sample size is too small." in warnings
