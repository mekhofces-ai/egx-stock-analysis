from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from app.database import SAMPLE_STOCKS
from app.services.market_data.base import BaseMarketDataProvider, MarketQuote


class MockProvider(BaseMarketDataProvider):
    provider_name = "mock"
    is_mock = True

    def get_symbols(self) -> list[str]:
        return [row["symbol"] for row in SAMPLE_STOCKS]

    def _rng(self, symbol: str) -> np.random.Generator:
        seed = sum((idx + 1) * ord(char) for idx, char in enumerate(symbol.upper()))
        return np.random.default_rng(seed)

    def _make_frame(self, symbol: str, periods: int = 260, freq: str = "B") -> pd.DataFrame:
        rng = self._rng(symbol)
        if freq == "B":
            dates = pd.bdate_range(end=pd.Timestamp.utcnow().normalize(), periods=periods)
        else:
            dates = pd.date_range(end=pd.Timestamp.utcnow().floor("min"), periods=periods, freq=freq)
        base = 10 + (sum(ord(char) for char in symbol.upper()) % 120)
        drift = rng.normal(0.0009, 0.001, periods)
        shocks = rng.normal(0, 0.018, periods)
        close = base * np.cumprod(1 + drift + shocks)
        open_ = close * (1 + rng.normal(0, 0.006, periods))
        high = np.maximum(open_, close) * (1 + rng.uniform(0.002, 0.025, periods))
        low = np.minimum(open_, close) * (1 - rng.uniform(0.002, 0.025, periods))
        volume = rng.integers(250_000, 8_000_000, periods)
        spike_index = periods - 3
        volume[spike_index:] = volume[spike_index:] * rng.integers(2, 4)
        frame = pd.DataFrame(
            {
                "symbol": symbol.upper(),
                "date": dates,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume.astype(float),
            }
        )
        frame.attrs["provider"] = self.provider_name
        frame.attrs["is_mock"] = True
        return frame

    def get_last_price(self, symbol: str) -> MarketQuote:
        df = self._make_frame(symbol)
        last = df.iloc[-1]
        prev = df.iloc[-2]
        change_percent = (float(last["close"]) - float(prev["close"])) / float(prev["close"]) * 100
        return MarketQuote(
            symbol=symbol.upper(),
            close=round(float(last["close"]), 2),
            change_percent=round(change_percent, 2),
            volume=float(last["volume"]),
            provider=self.provider_name,
            is_mock=True,
            raw={"warning": "Mock market data for development/testing only."},
        )

    def get_daily_ohlcv(self, symbol: str, start_date: datetime | None = None, end_date: datetime | None = None) -> pd.DataFrame:
        df = self._make_frame(symbol)
        if start_date is not None:
            df = df[df["date"] >= pd.to_datetime(start_date)]
        if end_date is not None:
            df = df[df["date"] <= pd.to_datetime(end_date)]
        df = df.reset_index(drop=True)
        df.attrs["provider"] = self.provider_name
        df.attrs["is_mock"] = True
        return df

    def get_intraday_ohlcv(self, symbol: str, interval: str = "1h") -> pd.DataFrame:
        freq_map = {"15m": "15min", "15min": "15min", "1h": "1h", "60m": "1h", "4h": "4h", "240m": "4h"}
        freq = freq_map.get(interval.lower(), "1h")
        periods = 320 if freq == "15min" else 220 if freq == "1h" else 180
        df = self._make_frame(symbol, periods=periods, freq=freq)
        df.attrs["provider"] = self.provider_name
        df.attrs["is_mock"] = True
        return df

    def screen_stocks(self, filters: dict[str, Any] | None = None) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for symbol in self.get_symbols():
            quote = self.get_last_price(symbol)
            rows.append(
                {
                    "symbol": quote.symbol,
                    "close": quote.close,
                    "change_percent": quote.change_percent,
                    "volume": quote.volume,
                    "RSI": None,
                    "Recommend.All": None,
                    "Recommend.MA": None,
                    "Recommend.Other": None,
                    "provider": self.provider_name,
                    "is_mock": True,
                }
            )
        return pd.DataFrame(rows).sort_values("volume", ascending=False).reset_index(drop=True)
