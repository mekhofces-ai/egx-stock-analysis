from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from app.services import learning_system as ls
from app.services.daily_loss_audit import EVAL_ENTRY_NOT_REACHED, EVAL_NOT_EVALUATED, EVAL_STOP_HIT, EVAL_TARGET_HIT


class FakeDB:
    def __init__(self) -> None:
        self.added = []

    def scalar(self, *args, **kwargs):  # noqa: ANN001, ANN201
        return None

    def add(self, value):  # noqa: ANN001
        self.added.append(value)


def _item(**overrides):
    base = {
        "id": 1,
        "report_id": 10,
        "report": None,
        "symbol": "TST",
        "details_json": {"validation": {"no_trade_reasons": []}, "weights": {"technical": 1}},
        "technical_score": 75,
        "telegram_score": 60,
        "strategy_score": 70,
        "news_score": 55,
        "risk_liquidity_score": 80,
        "final_score": 78,
        "risk_reward": 2.0,
        "signal": "CONDITIONAL BUY",
        "explanation": "Selected from available data.",
        "entry_zone_low": 10,
        "entry_zone_high": 10.5,
        "stop_loss": 9.5,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_decision_snapshot_uses_no_future_data(monkeypatch) -> None:
    frame = pd.DataFrame(
        [
            {"datetime": "2026-01-01", "open": 9, "high": 11, "low": 8, "close": 10, "volume": 1000},
            {"datetime": "2026-01-03", "open": 90, "high": 110, "low": 80, "close": 99, "volume": 9999},
        ]
    )
    monkeypatch.setattr(ls, "get_ohlcv", lambda db, symbol, timeframe=None, limit=700: frame)
    monkeypatch.setattr(ls, "_telegram_before", lambda db, symbol, snapshot_time: {"mentions": 0})
    monkeypatch.setattr(ls, "_news_before", lambda db, symbol, snapshot_time: {"items": 0})
    monkeypatch.setattr(ls, "_latest_financial_before", lambda db, symbol, snapshot_time: 50)
    monkeypatch.setattr(ls, "evaluate_daily_market", lambda db, target_date=None, persist=False: {"market_score": 60, "market_regime": "BULLISH"})

    snapshot = ls.create_decision_snapshot(FakeDB(), _item(), recommendation_time=datetime(2026, 1, 2, 9, 0, 0))

    assert snapshot.close == 10
    assert snapshot.stock_price == 10
    assert snapshot.raw_json["ohlcv_quality"] == "OK"


def test_walk_forward_uses_train_before_test(monkeypatch) -> None:
    start = datetime(2026, 1, 1)
    rows = []
    for idx in range(10):
        rows.append(
            {
                "recommendation_datetime": start + timedelta(days=idx),
                "status": EVAL_TARGET_HIT if idx % 2 == 0 else EVAL_STOP_HIT,
                "actual_return_pct": 2 if idx % 2 == 0 else -1,
            }
        )
    monkeypatch.setattr(ls, "_evaluation_rows", lambda db: rows)

    result = ls.run_walk_forward_validation(FakeDB(), train_days=3, test_days=2, min_trades=1)
    periods = result["periods"]

    assert not periods.empty
    assert pd.to_datetime(periods["Train End"]).max() < pd.to_datetime(periods["Test Start"]).max()
    assert all(pd.to_datetime(periods["Train End"]) < pd.to_datetime(periods["Test Start"]))


def test_pump_risk_stock_is_downgraded(monkeypatch) -> None:
    monkeypatch.setattr(
        ls,
        "calculate_pump_risk_for_row",
        lambda *args, **kwargs: {"pump_risk_score": 82, "risk_level": "HIGH", "reason": "Telegram-only hype"},
    )
    row = {"symbol": "AALR", "signal": "STRONG BUY", "details": {}, "explanation": "Old reason"}
    updated = ls.apply_pump_risk_guard(FakeDB(), row)

    assert updated["signal"] == "WATCH ONLY"
    assert "Pump-risk downgrade" in updated["explanation"]


def test_weak_market_regime_downgrades_buy() -> None:
    row = {"symbol": "COMI", "signal": "CONDITIONAL BUY", "details": {}, "explanation": "Setup valid"}
    market = {"market_regime": "BEARISH", "trade_permission": "BUY_BLOCKED", "market_score": 35, "explanation": "Weak breadth"}
    updated = ls.apply_market_regime_guard(row, market)

    assert updated["signal"] == "WATCH ONLY"
    assert updated["live_trade_allowed"] is False
    assert "Market-regime downgrade" in updated["explanation"]


def test_source_accuracy_excludes_not_evaluated_and_entry_not_reached(monkeypatch) -> None:
    rows = [
        {"status": EVAL_TARGET_HIT, "actual_return_pct": 4, "technical_score": 80, "symbol": "A"},
        {"status": EVAL_NOT_EVALUATED, "actual_return_pct": None, "technical_score": 95, "symbol": "B"},
        {"status": EVAL_ENTRY_NOT_REACHED, "actual_return_pct": 3, "technical_score": 90, "symbol": "C"},
    ]
    monkeypatch.setattr(ls, "_evaluation_rows", lambda db: rows)

    df = ls.compute_source_accuracy(FakeDB())
    technical = df[df["Source"].eq("technical")].iloc[0]

    assert technical["Evaluated"] == 1
    assert technical["Win Rate %"] == 100.0


def test_risk_expectancy_calculations_exclude_entry_not_reached(monkeypatch) -> None:
    rows = [
        {"status": EVAL_TARGET_HIT, "actual_return_pct": 5, "days_evaluated": 3, "risk_reward": 2.0},
        {"status": EVAL_STOP_HIT, "actual_return_pct": -2, "days_evaluated": 1, "risk_reward": 2.0},
        {"status": EVAL_ENTRY_NOT_REACHED, "actual_return_pct": 4, "days_evaluated": 4, "risk_reward": 2.0},
        {"status": EVAL_NOT_EVALUATED, "actual_return_pct": None, "days_evaluated": 0, "risk_reward": 2.0},
    ]
    monkeypatch.setattr(ls, "_evaluation_rows", lambda db: rows)

    result = ls.compute_risk_expectancy(FakeDB())

    assert result["Evaluated Count"] == 2
    assert result["Expected Value %"] == 1.5
    assert result["Profit Factor"] == 2.5
    assert result["Entry Reached Rate %"] == 66.67


def test_missed_opportunity_diagnosis_feeds_learning_suggestion(monkeypatch) -> None:
    monkeypatch.setattr(ls, "compute_source_accuracy", lambda db, persist=False: pd.DataFrame())
    monkeypatch.setattr(ls, "compute_risk_expectancy", lambda db, persist=False: {"Evaluated Count": 0})
    monkeypatch.setattr(ls, "_evaluation_rows", lambda db: [])
    missed = pd.DataFrame([{"Stock Symbol": "XXXX", "Why Not Selected Code": "LATE_BREAKOUT"}])

    payload = ls.build_strategy_learning_report(FakeDB(), target_date=datetime(2026, 1, 1).date(), missed_df=missed)

    blocked = payload["filters_blocked_good"]
    assert blocked.iloc[0]["Filter"] == "LATE_BREAKOUT"
    assert all(row["Auto Applied"] == "No" for row in payload["suggested_rules"].to_dict("records"))


def test_telegram_report_includes_learning_section() -> None:
    payload = {
        "source_accuracy": pd.DataFrame(
            [
                {"Source": "technical", "Reliability Score": 72, "Evaluated": 8},
                {"Source": "telegram", "Reliability Score": 35, "Evaluated": 8},
            ]
        ),
        "risk_expectancy": pd.DataFrame([{"Expected Value %": 1.2, "Profit Factor": 1.8, "Max Drawdown %": -3.5, "Entry Reached Rate %": 70, "Sample Warning": ""}]),
        "missed_opportunity_diagnosis": pd.DataFrame(
            [{"Stock Symbol": "XXXX", "Today Return %": 8.4, "Why Not Selected Code": "LATE_BREAKOUT", "Suggested Fix": "Add intraday scans."}]
        ),
        "strategy_learning": {
            "suggested_rules": pd.DataFrame([{"Rule": "Pump risk", "Suggestion": "Downgrade Telegram-only hype.", "Auto Applied": "No"}])
        },
    }

    text = ls.format_learning_telegram_block(payload)

    assert "Best source today: technical" in text
    assert "Worst source today: telegram" in text
    assert "Tomorrow improvement suggestions" in text
    assert "Auto Applied: No" in text
