from datetime import UTC, datetime
from types import SimpleNamespace

from app.services import reports


def test_night_opportunity_report_ranks_confirmed_buy(monkeypatch) -> None:
    settings = SimpleNamespace(
        timezone="Africa/Cairo",
        night_opportunity_top_n=2,
        strategy_symbol_limit=5,
    )

    rec_run = SimpleNamespace(
        provider_status="available",
        provider_warning=None,
        generated_at=datetime.now(UTC),
        rows=[
            {
                "symbol": "COMI",
                "name": "Commercial International Bank",
                "sector": "Banks",
                "final_score": 84,
                "final_recommendation": "BUY",
                "smart_action_now": "BUY NOW",
                "smart_buy_zone": "100.00 - 102.00",
                "smart_suggested_entry": 101.0,
                "smart_suggested_stop": 97.0,
                "smart_target_scalp": 103.5,
                "smart_target_swing": 107.0,
                "smart_target_long": 113.0,
                "telegram_vote": "POSITIVE",
                "telegram_signals": 3,
                "telegram_buy": 3,
                "telegram_sell": 0,
                "warnings": [],
            },
            {
                "symbol": "HRHO",
                "final_score": 61,
                "final_recommendation": "WATCH",
                "smart_action_now": "WATCH",
                "telegram_vote": "NONE",
                "telegram_signals": 0,
                "telegram_buy": 0,
                "telegram_sell": 0,
                "warnings": ["Some Telegram signals were missing stop loss."],
            },
        ],
    )

    strategy = {
        "rows": [
            {
                "symbol": "COMI",
                "strategy_action": "BUY",
                "strategy_score": 78,
                "buy_timeframes": 2,
                "watch_timeframes": 1,
                "uses_mock_data": False,
                "timeframes": [{"timeframe": "4h", "action": "BUY"}],
            },
            {
                "symbol": "HRHO",
                "strategy_action": "WATCH",
                "strategy_score": 58,
                "buy_timeframes": 0,
                "watch_timeframes": 2,
                "uses_mock_data": True,
                "timeframes": [{"timeframe": "1D", "action": "WATCH"}],
            },
        ]
    }

    monkeypatch.setattr(reports, "build_final_recommendations", lambda *args, **kwargs: rec_run)
    monkeypatch.setattr(reports, "run_strategy_universe", lambda *args, **kwargs: strategy)

    message = reports.build_night_opportunity_report(db=object(), settings=settings)

    assert "Night EGX Opportunity Report" in message
    assert "1. COMI - BUY CANDIDATE" in message
    assert "Telegram: POSITIVE" in message
    assert "Disclaimer: Not financial advice." in message
