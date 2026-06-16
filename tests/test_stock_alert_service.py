"""Tests for the central stock_alert_service."""
import sys
sys.path.insert(0, r"C:\Users\omar.mokhtar\Documents\New project")

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.services.stock_alert_service import (
    StockAlertResult,
    _trading_date,
    _bucketed,
    compute_alert_hash,
    is_muted_symbol,
    is_trading_session,
    send_stock_alert,
)
from app.config import get_settings, Settings


class TestBucketed:
    def test_none_returns_empty(self):
        assert _bucketed(None) == ""

    def test_empty_string_returns_empty(self):
        assert _bucketed("") == ""

    def test_zero_returns_empty(self):
        assert _bucketed(0) == ""

    def test_buckets_correctly(self):
        assert _bucketed(211.65, 5.0) == "210"

    def test_buckets_rounds_up(self):
        assert _bucketed(213.0, 5.0) == "215"

    def test_string_input(self):
        assert _bucketed("211.65", 5.0) == "210"

    def test_small_values(self):
        assert _bucketed(2.5, 5.0) == "0"

    def test_bucket_size_one(self):
        assert _bucketed(30.4, 1.0) == "30"


class TestComputeHash:
    def test_deterministic(self):
        h1 = compute_alert_hash("AALR", "BUY", "STRONG BUY", "30", "35", "28")
        h2 = compute_alert_hash("AALR", "BUY", "STRONG BUY", "30", "35", "28")
        assert h1 == h2

    def test_case_insensitive(self):
        h1 = compute_alert_hash("AALR", "BUY", "STRONG BUY", "30", "35", "28")
        h2 = compute_alert_hash("aalr", "buy", "strong buy", "30", "35", "28")
        assert h1 == h2

    def test_bucketed_same(self):
        """Different prices in same bucket produce same hash."""
        h1 = compute_alert_hash("AALR", "BUY", "STRONG BUY", "211", "224", "204")
        h2 = compute_alert_hash("AALR", "BUY", "STRONG BUY", "209", "225", "205")
        assert h1 == h2, f"211->{_bucketed(211,5.0)} and 209->{_bucketed(209,5.0)} should be same bucket"

    def test_different_bucket_differs(self):
        h1 = compute_alert_hash("AALR", "BUY", "STRONG BUY", "200", "224", "204")
        h2 = compute_alert_hash("AALR", "BUY", "STRONG BUY", "250", "224", "204")
        assert h1 != h2

    def test_different_stage_differs(self):
        h1 = compute_alert_hash("AALR", "WATCH", "WEAK BUY")
        h2 = compute_alert_hash("AALR", "BUY", "STRONG BUY")
        assert h1 != h2

    def test_different_date_differs(self):
        h1 = compute_alert_hash("AALR", "BUY", "STRONG BUY", trading_date="2026-06-08")
        h2 = compute_alert_hash("AALR", "BUY", "STRONG BUY", trading_date="2026-06-09")
        assert h1 != h2


class TestIsMutedSymbol:
    def setup_method(self):
        self.settings = get_settings()
        self.settings.muted_symbols = "AALR,ABUK,EMPTY"

    def test_muted(self):
        assert is_muted_symbol("AALR", self.settings)

    def test_not_muted(self):
        assert not is_muted_symbol("COMI", self.settings)

    def test_case_insensitive(self):
        assert is_muted_symbol("aalr", self.settings)

    def test_empty_muted_list(self):
        self.settings.muted_symbols = ""
        assert not is_muted_symbol("AALR", self.settings)


class TestSendStockAlert:
    def test_disabled_blocks(self):
        settings = get_settings()
        settings.enable_stock_alerts = False
        result = send_stock_alert(
            MagicMock(spec=Session), "TEST", "BUY", settings=settings,
        )
        assert result.blocked
        assert "disabled" in result.reason

    def test_muted_blocks(self):
        settings = get_settings()
        settings.enable_stock_alerts = True
        settings.muted_symbols = "TEST"
        result = send_stock_alert(
            MagicMock(spec=Session), "TEST", "BUY", settings=settings,
        )
        assert result.blocked
        assert "muted" in result.reason.lower()

    def test_bucketed_hash_stability_regression(self):
        """Verify that real AALR-like prices produce stable hashes."""
        h1 = compute_alert_hash("AALR", "BUY", "BUY", "210", "225", "205")
        h2 = compute_alert_hash("AALR", "BUY", "BUY", "211.65", "224.35", "204.24")
        assert h1 == h2, "Real AALR prices must produce same hash after bucketing"
