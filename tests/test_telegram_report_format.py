from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import RISK_NOTE
from app.services.daily_stock_report import REPORT_DISCLAIMER, format_telegram_report


def test_telegram_report_contains_required_sections() -> None:
    items = [
        {
            "symbol": "COMI",
            "company_name": "Commercial International Bank",
            "final_score": 87,
            "telegram_score": 80,
            "technical_score": 90,
            "strategy_score": 82,
            "news_score": 60,
            "backtest_score": 70,
            "risk_liquidity_score": 88,
            "signal": "BUY",
            "entry_zone_low": 12.4,
            "entry_zone_high": 12.8,
            "stop_loss": 11.9,
            "target_1": 13.5,
            "target_2": 14.2,
            "target_3": 15.0,
            "risk_reward": 2.4,
            "explanation": "Telegram sentiment: Positive\nTechnical: Bullish breakout\nStrategy: 3 strategies confirmed BUY",
            "details": {"backtest": {"metrics": {"win_rate": 62, "max_drawdown": 8, "profit_factor": 1.6}}},
        }
    ]
    message = format_telegram_report("morning", datetime(2026, 6, 7, 9, tzinfo=ZoneInfo("Africa/Cairo")), items)
    assert "EGX Daily Stock Report" in message
    assert "1) COMI" in message
    assert REPORT_DISCLAIMER in message
    assert RISK_NOTE in message
    assert len(message) < 3900


def test_telegram_report_contains_recommendation_vs_actual_comparison() -> None:
    items = [
        {
            "symbol": "COMI",
            "company_name": "Commercial International Bank",
            "final_score": 87,
            "signal": "WATCH ONLY",
            "entry_zone_low": 12.4,
            "entry_zone_high": 12.8,
            "stop_loss": 11.9,
            "target_1": 13.5,
            "target_2": 14.2,
            "target_3": 15.0,
            "risk_reward": 2.4,
            "explanation": "Technical: Bullish breakout",
            "details": {"backtest": {"metrics": {"win_rate": 62, "max_drawdown": 8, "profit_factor": 1.6}}},
        }
    ]
    comparison = {
        "date": "2026-06-07",
        "summary": {"total_recommendations": 1, "good_calls": 1, "bad_calls": 0, "no_entry": 0, "target_hit": 1, "stop_loss_hit": 0},
        "diagnosis": "Recommendation matched the later movement.",
        "items": [
            {
                "symbol": "COMI",
                "recommended_signal": "CONDITIONAL BUY",
                "evaluation_status": "TARGET_HIT",
                "final_quality": "Excellent",
                "result": "GOOD_CALL",
                "actual_return": 2.4,
                "evaluation_quality": "MEDIUM_DAILY",
            }
        ],
        "status_counts": {"TARGET_HIT": 1},
        "evaluated": 1,
        "avg_return": 2.4,
    }
    message = format_telegram_report(
        "evening",
        datetime(2026, 6, 7, 21, tzinfo=ZoneInfo("Africa/Cairo")),
        items,
        comparison=comparison,
    )
    assert "Recommendation vs What Happened" in message
    assert "Compared date: 2026-06-07" in message
    assert "COMI: CONDITIONAL BUY -> TARGET_HIT" in message
    assert "Accuracy is not reliable yet because evaluated sample size is too small." in message
    assert "Win rate:" not in message
