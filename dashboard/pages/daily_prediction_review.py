from __future__ import annotations

from dashboard.pages.end_of_day_common import header, show_summary, show_table


def render() -> None:
    _day, payload = header("Daily Prediction Review", "daily_prediction_review_date")
    show_summary(payload)
    show_table(payload, "recommendation_results", "Recommendation Results")
    show_table(payload, "top_movers_analysis", "Top Movers Analysis", height=420)
