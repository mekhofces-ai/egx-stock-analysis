from __future__ import annotations

from dashboard.pages.end_of_day_common import header, show_table
from dashboard.ui_components import warning_box


def render() -> None:
    _day, payload = header("Strategy Learning Center", "strategy_learning_center_date")
    warning_box("Suggestions are advisory only. No strategy weights or rules are changed automatically.")
    show_table(payload, "strategy_improvement_suggestions", "Strategy Improvement Suggestions", height=360)
    show_table(payload, "score_breakdown", "Score Breakdown And Failed Filters")
