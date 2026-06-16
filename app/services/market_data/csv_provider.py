from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.services.market_data.base import BaseMarketDataProvider, MarketQuote, ProviderUnavailable


class CSVProvider(BaseMarketDataProvider):
    provider_name = "csv"
    is_mock = False

    def _normalize_interval(self, interval: str) -> str:
        value = interval.strip().lower()
        aliases = {
            "15": "15m",
            "15min": "15m",
            "15m": "15m",
            "30": "30m",
            "30min": "30m",
            "30m": "30m",
            "60": "1h",
            "60m": "1h",
            "1h": "1h",
            "240": "4h",
            "240m": "4h",
            "4h": "4h",
            "d": "1d",
            "1d": "1d",
        }
        return aliases.get(value, value)

    def _candidate_paths(self, symbol: str) -> list[Path]:
        return [
            Path(self.settings.csv_data_dir) / f"{symbol.upper()}.csv",
            Path(self.settings.csv_ohlcv_sample_path),
        ]

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        df.columns = [str(col).strip().lower() for col in df.columns]
        rename_map = {"datetime": "date", "time": "date", "adj close": "close"}
        df = df.rename(columns=rename_map)
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ProviderUnavailable(f"CSV missing columns: {sorted(missing)}")
        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)

    def _resample_intraday(self, df: pd.DataFrame, interval: str) -> pd.DataFrame:
        interval = self._normalize_interval(interval)
        freq_map = {"15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h"}
        if interval not in freq_map:
            raise ProviderUnavailable(f"Unsupported CSV intraday interval: {interval}")
        frame = df.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        if not (frame["date"].dt.hour.ne(0).any() or frame["date"].dt.minute.ne(0).any()):
            raise ProviderUnavailable("CSV data is daily only; no intraday timestamps found")
        symbol = frame["symbol"].iloc[0] if "symbol" in frame.columns and not frame.empty else None
        frame = frame.set_index("date").sort_index()
        resampled = frame.resample(freq_map[interval]).agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        resampled = resampled.dropna(subset=["open", "high", "low", "close"]).reset_index()
        if symbol is not None:
            resampled["symbol"] = str(symbol).upper()
        if resampled.empty:
            raise ProviderUnavailable(f"CSV resample returned no rows for {interval}")
        resampled.attrs["provider"] = self.provider_name
        resampled.attrs["is_mock"] = False
        return resampled

    def _read_all(self) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        data_dir = Path(self.settings.csv_data_dir)
        if data_dir.exists():
            for path in data_dir.glob("*.csv"):
                df = pd.read_csv(path)
                if "symbol" not in [str(col).lower() for col in df.columns]:
                    df["symbol"] = path.stem.upper()
                frames.append(df)
        sample_path = Path(self.settings.csv_ohlcv_sample_path)
        if sample_path.exists():
            frames.append(pd.read_csv(sample_path))
        if not frames:
            raise ProviderUnavailable("No CSV data files found")
        return pd.concat(frames, ignore_index=True)

    def _read_symbol(self, symbol: str) -> pd.DataFrame:
        symbol = symbol.upper()
        for path in self._candidate_paths(symbol):
            if not path.exists():
                continue
            df = pd.read_csv(path)
            lower_columns = {str(col).lower(): col for col in df.columns}
            if "symbol" in lower_columns:
                df = df[df[lower_columns["symbol"]].astype(str).str.upper() == symbol]
            elif path.stem.upper() != symbol and path.name != Path(self.settings.csv_ohlcv_sample_path).name:
                continue
            df = self._normalize(df)
            if not df.empty:
                df["symbol"] = symbol
                df.attrs["provider"] = self.provider_name
                df.attrs["is_mock"] = False
                return df
        raise ProviderUnavailable(f"No CSV OHLCV data for {symbol}")

    def get_symbols(self) -> list[str]:
        df = self._read_all()
        lower_columns = {str(col).lower(): col for col in df.columns}
        if "symbol" in lower_columns:
            return sorted(df[lower_columns["symbol"]].dropna().astype(str).str.upper().unique().tolist())
        data_dir = Path(self.settings.csv_data_dir)
        return sorted(path.stem.upper() for path in data_dir.glob("*.csv"))

    def get_last_price(self, symbol: str) -> MarketQuote:
        df = self._read_symbol(symbol)
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        change_percent = None
        if float(prev["close"]):
            change_percent = (float(last["close"]) - float(prev["close"])) / float(prev["close"]) * 100
        return MarketQuote(
            symbol=symbol.upper(),
            close=float(last["close"]),
            change_percent=change_percent,
            volume=float(last["volume"]) if pd.notna(last["volume"]) else None,
            provider=self.provider_name,
            is_mock=False,
        )

    def get_daily_ohlcv(self, symbol: str, start_date: datetime | None = None, end_date: datetime | None = None) -> pd.DataFrame:
        df = self._read_symbol(symbol)
        if start_date is not None:
            df = df[df["date"] >= pd.to_datetime(start_date)]
        if end_date is not None:
            df = df[df["date"] <= pd.to_datetime(end_date)]
        return df

    def get_intraday_ohlcv(self, symbol: str, interval: str = "1h") -> pd.DataFrame:
        interval = self._normalize_interval(interval)
        if interval == "1d":
            return self.get_daily_ohlcv(symbol)
        df = self._read_symbol(symbol)
        if "timeframe" in df.columns:
            filtered = df[df["timeframe"].astype(str).str.lower().map(self._normalize_interval) == interval].copy()
            if filtered.empty:
                raise ProviderUnavailable(f"No CSV rows for {symbol} timeframe {interval}")
            filtered.attrs["provider"] = self.provider_name
            filtered.attrs["is_mock"] = False
            return filtered.reset_index(drop=True)
        return self._resample_intraday(df, interval)

    def screen_stocks(self, filters: dict[str, Any] | None = None) -> pd.DataFrame:
        df = self._normalize(self._read_all())
        if "symbol" not in df.columns:
            raise ProviderUnavailable("CSV screener requires a symbol column")
        rows: list[dict[str, Any]] = []
        for symbol, group in df.groupby(df["symbol"].astype(str).str.upper()):
            group = group.sort_values("date")
            if group.empty:
                continue
            last = group.iloc[-1]
            prev = group.iloc[-2] if len(group) > 1 else last
            prev_close = float(prev["close"]) if float(prev["close"]) else None
            change_percent = ((float(last["close"]) - prev_close) / prev_close * 100) if prev_close else 0.0
            rows.append(
                {
                    "symbol": symbol,
                    "close": float(last["close"]),
                    "change_percent": change_percent,
                    "volume": float(last["volume"]),
                    "provider": self.provider_name,
                    "is_mock": False,
                }
            )
        result = pd.DataFrame(rows)
        if result.empty:
            raise ProviderUnavailable("CSV screener returned no rows")
        return result.sort_values("volume", ascending=False).reset_index(drop=True)
