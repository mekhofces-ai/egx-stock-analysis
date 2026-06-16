from __future__ import annotations

import json
import logging
import random
import string
from datetime import datetime
from typing import Any

import pandas as pd
from websockets.sync.client import connect

from app.services.market_data.base import BaseMarketDataProvider, MarketQuote, ProviderUnavailable

logger = logging.getLogger(__name__)


class TradingViewWebSocketProvider(BaseMarketDataProvider):
    """TradingView chart-session OHLCV adapter.

    TradingView does not provide this as a stable public API. The app treats it as a
    best-effort candle source and the strategy layer validates freshness and price
    alignment before using the result.
    """

    provider_name = "tradingview_websocket"
    is_mock = False

    def _session_id(self, prefix: str) -> str:
        suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(12))
        return f"{prefix}_{suffix}"

    def _frame_message(self, payload: dict[str, Any]) -> str:
        text = json.dumps(payload, separators=(",", ":"))
        return f"~m~{len(text)}~m~{text}"

    def _send(self, ws, method: str, params: list[Any]) -> None:
        ws.send(self._frame_message({"m": method, "p": params}))

    def _iter_payloads(self, raw: str) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        pos = 0
        while pos < len(raw):
            if not raw.startswith("~m~", pos):
                break
            pos += 3
            marker = raw.find("~m~", pos)
            if marker == -1:
                break
            try:
                length = int(raw[pos:marker])
            except ValueError:
                break
            start = marker + 3
            end = start + length
            text = raw[start:end]
            pos = end
            if not text or text.startswith("m~~h~"):
                continue
            try:
                payloads.append(json.loads(text))
            except json.JSONDecodeError:
                logger.debug("Could not parse TradingView payload: %s", text[:120])
        return payloads

    def _resolution(self, interval: str) -> str:
        value = interval.strip().lower()
        aliases = {
            "15": "15",
            "15m": "15",
            "15min": "15",
            "30": "30",
            "30m": "30",
            "30min": "30",
            "60": "60",
            "60m": "60",
            "1h": "60",
            "240": "240",
            "240m": "240",
            "4h": "240",
            "d": "D",
            "1d": "D",
            "1D": "D",
        }
        return aliases.get(value, interval)

    def _symbol_payload(self, symbol: str) -> str:
        tv_symbol = symbol.upper()
        if not tv_symbol.startswith("EGX:"):
            tv_symbol = f"EGX:{tv_symbol}"
        return tv_symbol

    def _extract_series(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        method = payload.get("m")
        if method not in {"timescale_update", "series_completed"}:
            return []
        params = payload.get("p") or []
        if len(params) < 2 or not isinstance(params[1], dict):
            return []
        series_block = params[1].get("s1") or params[1].get("sds_1")
        if not isinstance(series_block, dict):
            return []
        rows = series_block.get("s") or []
        return rows if isinstance(rows, list) else []

    def _rows_to_frame(self, symbol: str, rows: list[dict[str, Any]]) -> pd.DataFrame:
        parsed: list[dict[str, Any]] = []
        for row in rows:
            values = row.get("v") if isinstance(row, dict) else None
            if not isinstance(values, list) or len(values) < 5:
                continue
            volume = values[5] if len(values) > 5 else 0
            parsed.append(
                {
                    "symbol": symbol.upper(),
                    "date": pd.to_datetime(values[0], unit="s", utc=True),
                    "open": values[1],
                    "high": values[2],
                    "low": values[3],
                    "close": values[4],
                    "volume": volume,
                }
            )
        frame = pd.DataFrame(parsed)
        if frame.empty:
            raise ProviderUnavailable(f"TradingView returned no parseable bars for {symbol}")
        for col in ["open", "high", "low", "close", "volume"]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)
        if frame.empty:
            raise ProviderUnavailable(f"TradingView bars for {symbol} were empty after cleaning")
        frame.attrs["provider"] = self.provider_name
        frame.attrs["is_mock"] = False
        return frame

    def _history(self, symbol: str, resolution: str, bars: int) -> pd.DataFrame:
        chart_session = self._session_id("cs")
        quote_session = self._session_id("qs")
        rows: list[dict[str, Any]] = []
        try:
            with connect(
                self.settings.tradingview_ws_url,
                origin="https://www.tradingview.com",
                additional_headers={
                    "User-Agent": "Mozilla/5.0 EGX-Telegram-Analyst/1.0",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
                open_timeout=10,
                close_timeout=5,
            ) as ws:
                self._send(ws, "set_auth_token", [self.settings.tradingview_auth_token or "unauthorized_user_token"])
                self._send(ws, "chart_create_session", [chart_session, ""])
                self._send(ws, "quote_create_session", [quote_session])
                self._send(ws, "quote_set_fields", [quote_session, "lp", "ch", "chp", "volume"])
                self._send(ws, "quote_add_symbols", [quote_session, f"EGX:{symbol.upper()}"])
                self._send(ws, "resolve_symbol", [chart_session, "symbol_1", self._symbol_payload(symbol)])
                self._send(ws, "create_series", [chart_session, "s1", "s1", "symbol_1", resolution, int(bars)])

                for _ in range(35):
                    raw = ws.recv(timeout=3)
                    if raw.startswith("~h~"):
                        ws.send(raw)
                        continue
                    for payload in self._iter_payloads(raw):
                        method = payload.get("m")
                        if method == "protocol_error":
                            raise ProviderUnavailable(f"TradingView protocol error: {payload}")
                        extracted = self._extract_series(payload)
                        if extracted:
                            rows = extracted
                        if method == "series_completed" and rows:
                            return self._rows_to_frame(symbol, rows)
                    if rows and len(rows) >= min(50, bars):
                        return self._rows_to_frame(symbol, rows)
        except ProviderUnavailable:
            raise
        except Exception as exc:
            raise ProviderUnavailable(f"TradingView chart websocket failed for {symbol}: {exc}") from exc
        if rows:
            return self._rows_to_frame(symbol, rows)
        raise ProviderUnavailable(f"TradingView returned no history for {symbol}")

    def get_symbols(self) -> list[str]:
        raise ProviderUnavailable("TradingView chart websocket does not provide a universe list")

    def get_last_price(self, symbol: str) -> MarketQuote:
        df = self._history(symbol, resolution="D", bars=5)
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        previous_close = float(prev["close"]) if float(prev["close"]) else None
        change_percent = ((float(last["close"]) - previous_close) / previous_close * 100) if previous_close else None
        return MarketQuote(
            symbol=symbol.upper(),
            close=float(last["close"]),
            change_percent=change_percent,
            volume=float(last["volume"]) if pd.notna(last["volume"]) else None,
            provider=self.provider_name,
            is_mock=False,
            raw={"source": "tradingview_chart_session"},
        )

    def get_daily_ohlcv(self, symbol: str, start_date: datetime | None = None, end_date: datetime | None = None) -> pd.DataFrame:
        frame = self._history(symbol, resolution="D", bars=self.settings.strategy_backtest_bars)
        if start_date is not None:
            frame = frame[frame["date"] >= pd.to_datetime(start_date, utc=True)]
        if end_date is not None:
            frame = frame[frame["date"] <= pd.to_datetime(end_date, utc=True)]
        if frame.empty:
            raise ProviderUnavailable(f"TradingView daily history filtered to empty for {symbol}")
        frame.attrs["provider"] = self.provider_name
        frame.attrs["is_mock"] = False
        return frame.reset_index(drop=True)

    def get_intraday_ohlcv(self, symbol: str, interval: str = "1h") -> pd.DataFrame:
        resolution = self._resolution(interval)
        if resolution == "D":
            return self.get_daily_ohlcv(symbol)
        bars = max(self.settings.strategy_backtest_bars, 120 if resolution == "15" else 90)
        frame = self._history(symbol, resolution=resolution, bars=bars)
        frame.attrs["provider"] = self.provider_name
        frame.attrs["is_mock"] = False
        return frame.reset_index(drop=True)

    def screen_stocks(self, filters: dict[str, Any] | None = None) -> pd.DataFrame:
        raise ProviderUnavailable("TradingView chart websocket is an OHLCV provider, not a universe screener")
