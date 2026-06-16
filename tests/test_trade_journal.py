from __future__ import annotations

from app.services.trading_safety import journal_trade_event


class DummyDB:
    def __init__(self) -> None:
        self.rows = []

    def add(self, row) -> None:  # noqa: ANN001
        self.rows.append(row)


def test_trade_journal_saves_losing_trade_reason() -> None:
    db = DummyDB()
    row = journal_trade_event(
        db,
        {
            "symbol": "COMI",
            "signal": "CONDITIONAL BUY",
            "entry_zone": "10 - 10.5",
            "actual_entry": 10.5,
            "stop_loss": 9.5,
            "targets": {"target_1": 11.5},
            "exit_price": 9.4,
            "result": "LOSS",
            "pnl": -100,
            "pnl_pct": -2,
            "mistake_type": "BAD_ENTRY",
            "lesson_learned": "Wait for pullback.",
        },
    )
    assert db.rows == [row]
    assert row.symbol == "COMI"
    assert row.mistake_type == "BAD_ENTRY"
