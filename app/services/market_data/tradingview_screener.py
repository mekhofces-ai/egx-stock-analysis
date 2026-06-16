from __future__ import annotations

import logging
import ssl
from typing import Any

import httpx
import pandas as pd

from app.services.market_data.base import BaseMarketDataProvider, MarketQuote, ProviderUnavailable

logger = logging.getLogger(__name__)


class TradingViewScreenerProvider(BaseMarketDataProvider):
    """Unofficial TradingView screener integration.

    It is intentionally optional and wrapped by ProviderChain because this endpoint can change,
    block, or rate-limit requests without notice.
    """

    provider_name = "tradingview_screener"
    is_mock = False
    scan_url = "https://scanner.tradingview.com/egypt/scan"
    columns = [
        "name",
        "description",
        "close",
        "change",
        "volume",
        "RSI",
        "Recommend.All",
        "Recommend.MA",
        "Recommend.Other",
    ]

    def _is_ssl_error(self, exc: Exception) -> bool:
        if isinstance(exc, ssl.SSLError):
            return True
        text = str(exc).lower()
        return "ssl" in text or "certificate" in text or "self-signed certificate" in text

    def _post_scan(self, payload: dict[str, Any], headers: dict[str, str], verify: bool) -> dict[str, Any]:
        with httpx.Client(timeout=12.0, headers=headers, verify=verify) as client:
            response = client.post(self.scan_url, json=payload)
        response.raise_for_status()
        return response.json()

    def _scan(self, symbols: list[str] | None = None, limit: int = 100) -> pd.DataFrame:
        payload: dict[str, Any] = {
            "columns": self.columns,
            "range": [0, limit],
            "sort": {"sortBy": "volume", "sortOrder": "desc"},
        }
        if symbols:
            payload["symbols"] = {"tickers": [f"EGX:{symbol.upper().replace('EGX:', '')}" for symbol in symbols], "query": {"types": []}}
        headers = {
            "User-Agent": "egx-telegram-signal-mvp/1.0",
            "Content-Type": "application/json",
        }
        verify = not self.settings.allow_insecure_market_data_tls
        try:
            data = self._post_scan(payload, headers, verify=verify)
        except Exception as exc:
            if verify and self._is_ssl_error(exc):
                logger.warning("TradingView screener SSL verification failed; retrying once with verify=False.")
                try:
                    data = self._post_scan(payload, headers, verify=False)
                except Exception as retry_exc:
                    raise ProviderUnavailable(f"TradingView screener request failed after SSL retry: {retry_exc}") from retry_exc
            else:
                raise ProviderUnavailable(f"TradingView screener request failed: {exc}") from exc

        rows: list[dict[str, Any]] = []
        for item in data.get("data", []) or []:
            values = item.get("d", [])
            raw_symbol = str(item.get("s", ""))
            symbol = raw_symbol.split(":")[-1].upper()
            row = dict(zip(self.columns, values, strict=False))
            rows.append(
                {
                    "symbol": symbol,
                    "close": row.get("close"),
                    "change_percent": row.get("change"),
                    "volume": row.get("volume"),
                    "RSI": row.get("RSI"),
                    "Recommend.All": row.get("Recommend.All"),
                    "Recommend.MA": row.get("Recommend.MA"),
                    "Recommend.Other": row.get("Recommend.Other"),
                    "description": row.get("description"),
                    "provider": self.provider_name,
                    "is_mock": False,
                    "raw": row,
                }
            )
        if not rows:
            raise ProviderUnavailable("TradingView screener returned no rows")
        return pd.DataFrame(rows)

    def get_symbols(self) -> list[str]:
        df = self._scan(limit=500)
        return sorted(df["symbol"].dropna().astype(str).unique().tolist())

    def get_last_price(self, symbol: str) -> MarketQuote:
        df = self._scan(symbols=[symbol], limit=1)
        row = df.iloc[0]
        return MarketQuote(
            symbol=symbol.upper(),
            close=float(row["close"]) if pd.notna(row["close"]) else None,
            change_percent=float(row["change_percent"]) if pd.notna(row["change_percent"]) else None,
            volume=float(row["volume"]) if pd.notna(row["volume"]) else None,
            provider=self.provider_name,
            is_mock=False,
            raw=row.to_dict(),
        )

    def get_daily_ohlcv(self, symbol: str, start_date=None, end_date=None) -> pd.DataFrame:
        raise ProviderUnavailable("TradingView screener exposes snapshot fields, not historical OHLCV")

    def get_intraday_ohlcv(self, symbol: str, interval: str = "1h") -> pd.DataFrame:
        raise ProviderUnavailable("TradingView screener exposes snapshot fields, not intraday OHLCV")

    def screen_stocks(self, filters: dict[str, Any] | None = None) -> pd.DataFrame:
        limit = int((filters or {}).get("limit", 100))
        return self._scan(limit=limit)
