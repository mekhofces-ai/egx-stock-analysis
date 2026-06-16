"""Tests for the enhanced afternoon report with pre-trade stages."""

from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.services.morning_review import best_worst_signal_sources


def test_best_worst_signal_sources_no_data():
    """Returns N/A when no decisions exist."""
    mock_db = MagicMock(spec=Session)
    mock_db.scalars.return_value.all.return_value = []
    result = best_worst_signal_sources(mock_db)
    assert "N/A" in result["best"]
    assert "N/A" in result["worst"]


def test_best_worst_signal_sources_with_data():
    """Returns identified best/worst when data exists."""
    from app.models import FinalStockDecision
    mock_db = MagicMock(spec=Session)

    d1 = MagicMock(spec=FinalStockDecision)
    d1.best_analysis_today = "technical"
    d1.best_strategy_today = "cli_v6"
    d1.final_score = 85.0

    d2 = MagicMock(spec=FinalStockDecision)
    d2.best_analysis_today = "technical"
    d2.best_strategy_today = "cli_v6"
    d2.final_score = 75.0

    d3 = MagicMock(spec=FinalStockDecision)
    d3.best_analysis_today = "news"
    d3.best_strategy_today = "legacy"
    d3.final_score = 60.0

    mock_db.scalars.return_value.all.return_value = [d1, d2, d3]
    result = best_worst_signal_sources(mock_db)
    assert "technical" in result["best"] or "cli_v6" in result["best"]
    assert "news" in result["worst"] or "legacy" in result["worst"]
