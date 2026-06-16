from __future__ import annotations

from datetime import date

from app.services.last7_audit import build_last7_audit, format_last7_summary


class DummyDb:
    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
        class Result:
            def mappings(self):
                return self

            def all(self):
                return []

        return Result()

    def scalars(self, *args, **kwargs):  # noqa: ANN002, ANN003
        class Result:
            def all(self):
                return []

        return Result()


def test_last7_audit_aggregates_daily_results():
    def fake_daily_builder(**kwargs):
        target_date = kwargs["target_date"]
        return {
            "audit_date": target_date.isoformat(),
            "summary": {
                "total_recommendations": 1,
                "good_calls": 1 if target_date.day % 2 else 0,
                "bad_calls": 0 if target_date.day % 2 else 1,
                "no_entry": 0,
                "stop_loss_hit": 0,
                "target_hit": 1 if target_date.day % 2 else 0,
                "mistake_counts": {"BAD_ENTRY": 1} if not target_date.day % 2 else {},
            },
            "items": [
                {
                    "symbol": "COMI",
                    "recommended_signal": "CONDITIONAL BUY",
                    "final_score": 80,
                    "actual_return": 2.1 if target_date.day % 2 else -1.2,
                    "result": "GOOD_CALL" if target_date.day % 2 else "BAD_CALL",
                    "mistake_type": None if target_date.day % 2 else "BAD_ENTRY",
                }
            ],
        }

    result = build_last7_audit(days=3, end_date=date(2026, 6, 11), db=DummyDb(), daily_builder=fake_daily_builder)
    assert result["summary"]["total_recommendations"] == 3
    assert result["summary"]["evaluated"] == 3
    assert result["summary"]["bad_calls"] >= 1
    assert "BAD_ENTRY" in result["summary"]["mistake_counts"]
    assert "EGX Last 7 Days System Audit" in format_last7_summary(result)
