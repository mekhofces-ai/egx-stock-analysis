from __future__ import annotations

from app.services import system_smoke_test


def test_smoke_test_formats_rows(monkeypatch):
    monkeypatch.setattr(
        system_smoke_test,
        "run_health_check",
        lambda save_log=True: [{"component": "Config", "status": "OK", "message": "ok", "details": {}}],
    )
    monkeypatch.setattr(system_smoke_test, "init_db", lambda seed=True: None)
    monkeypatch.setattr(system_smoke_test, "generate_daily_report", lambda **kwargs: {"items_count": 5})
    monkeypatch.setattr(system_smoke_test, "generate_daily_file_report", lambda **kwargs: {"items_count": 5})
    monkeypatch.setattr(
        system_smoke_test,
        "build_last7_audit",
        lambda **kwargs: {"summary": {"total_recommendations": 5, "evaluated": 5}},
    )

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, *args):  # noqa: ANN002
            return False

        def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
            class Result:
                def scalar(self):
                    return 0

            return Result()

    monkeypatch.setattr(system_smoke_test, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(system_smoke_test, "safety_snapshot", lambda db: {"execution_blocked": True})

    result = system_smoke_test.run_smoke_test(save_log=False)
    assert result["status"] == "OK"
    text = system_smoke_test.format_smoke_result(result)
    assert "System smoke test" in text
