from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from app.config import Settings

logger = logging.getLogger(__name__)


class ProviderUnavailable(RuntimeError):
    """Raised when a provider cannot answer and the chain should try the next one."""


@dataclass
class MarketQuote:
    symbol: str
    close: float | None
    change_percent: float | None = None
    volume: float | None = None
    provider: str = "unknown"
    is_mock: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


class BaseMarketDataProvider(ABC):
    provider_name = "base"
    is_mock = False

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def get_symbols(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def get_last_price(self, symbol: str) -> MarketQuote:
        raise NotImplementedError

    @abstractmethod
    def get_daily_ohlcv(self, symbol: str, start_date: datetime | None = None, end_date: datetime | None = None) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def get_intraday_ohlcv(self, symbol: str, interval: str = "1h") -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def screen_stocks(self, filters: dict[str, Any] | None = None) -> pd.DataFrame:
        raise NotImplementedError


class ProviderChain:
    def __init__(self, providers: list[BaseMarketDataProvider]):
        self.providers = providers

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                result = getattr(provider, method_name)(*args, **kwargs)
                if isinstance(result, pd.DataFrame) and result.empty:
                    raise ProviderUnavailable(f"{provider.provider_name} returned no rows")
                return result
            except ProviderUnavailable as exc:
                logger.info("%s unavailable for %s: %s", provider.provider_name, method_name, exc)
                last_error = exc
            except Exception as exc:  # provider failures should not crash the app
                logger.warning("%s failed for %s: %s", provider.provider_name, method_name, exc)
                last_error = exc
        raise ProviderUnavailable(f"No market-data provider could satisfy {method_name}: {last_error}")

    def get_symbols(self) -> list[str]:
        return self._call("get_symbols")

    def get_last_price(self, symbol: str) -> MarketQuote:
        return self._call("get_last_price", symbol.upper())

    def get_daily_ohlcv(self, symbol: str, start_date: datetime | None = None, end_date: datetime | None = None) -> pd.DataFrame:
        return self._call("get_daily_ohlcv", symbol.upper(), start_date, end_date)

    def get_intraday_ohlcv(self, symbol: str, interval: str = "1h") -> pd.DataFrame:
        return self._call("get_intraday_ohlcv", symbol.upper(), interval)

    def screen_stocks(self, filters: dict[str, Any] | None = None) -> pd.DataFrame:
        return self._call("screen_stocks", filters or {})


def build_provider_chain(settings: Settings) -> ProviderChain:
    from app.services.market_data.csv_provider import CSVProvider
    from app.services.market_data.mock_provider import MockProvider
    from app.services.market_data.tradingview_screener import TradingViewScreenerProvider
    from app.services.market_data.tradingview_websocket import TradingViewWebSocketProvider

    registry: dict[str, type[BaseMarketDataProvider]] = {
        "csv": CSVProvider,
        "mock": MockProvider,
        "tradingview_screener": TradingViewScreenerProvider,
        "tradingview_websocket": TradingViewWebSocketProvider,
    }

    providers: list[BaseMarketDataProvider] = []
    for key in settings.provider_priority:
        normalized = key.strip().lower()
        if normalized == "mock" and not settings.market_data_allow_mock:
            logger.info("Skipping mock market data provider because MARKET_DATA_ALLOW_MOCK=false.")
            continue
        provider_cls = registry.get(normalized)
        if provider_cls:
            providers.append(provider_cls(settings))
        else:
            logger.warning("Unknown market data provider in priority list: %s", key)
    if not providers:
        if settings.market_data_allow_mock:
            providers.append(MockProvider(settings))
        else:
            providers.append(CSVProvider(settings))
    return ProviderChain(providers)
