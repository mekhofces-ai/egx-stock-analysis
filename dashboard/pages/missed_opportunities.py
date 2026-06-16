from __future__ import annotations

from dashboard.pages.end_of_day_common import header, show_table


def render() -> None:
    _day, payload = header("Missed Opportunities", "missed_opportunities_date")
    show_table(payload, "missed_opportunities", "Top Movers Not Recommended")
    show_table(payload, "tomorrow_watchlist", "Tomorrow Watchlist", height=420)
