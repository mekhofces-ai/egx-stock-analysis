from __future__ import annotations

from app.services.daily_stock_report import calculate_final_score, sort_top_recommendations


def test_final_score_uses_configured_weights() -> None:
    score = calculate_final_score(
        {
            "telegram_score": 80,
            "technical_score": 90,
            "strategy_score": 70,
            "news_score": 50,
            "backtest_score": 60,
            "risk_liquidity_score": 100,
        }
    )
    assert round(score, 2) == 74.0


def test_top_5_sorting_uses_final_score_descending() -> None:
    rows = [{"symbol": f"S{i}", "final_score": score, "risk_liquidity_score": 50} for i, score in enumerate([40, 95, 70, 88, 66, 91])]
    top = sort_top_recommendations(rows, top_n=5)
    assert [row["symbol"] for row in top] == ["S1", "S5", "S3", "S2", "S4"]
