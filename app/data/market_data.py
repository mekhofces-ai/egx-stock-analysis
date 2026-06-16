from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.data_cleaner import clean_ohlcv_frame, normalize_symbol
from app.database import SessionLocal
from app.models import DailyEGXReportRow, MarketPrice, OHLCVData, TradingViewScreeningResult, TradingViewScreeningRun


def _rows_to_frame(rows: list[Any], datetime_field: str) -> pd.DataFrame:
    return clean_ohlcv_frame(
        pd.DataFrame(
            [
                {
                    "datetime": getattr(row, datetime_field),
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                }
                for row in rows
            ]
        )
    )


def get_ohlcv(
    db: Session,
    symbol: str,
    *,
    timeframe: str | None = None,
    limit: int = 300,
) -> pd.DataFrame:
    clean_symbol = normalize_symbol(symbol)
    requested_timeframe = str(timeframe or "").strip().lower()
    daily_aliases = {"", "1d", "1D", "d", "D", "daily"}
    if not timeframe:
        rows = db.scalars(
            select(OHLCVData)
            .where(OHLCVData.symbol == clean_symbol)
            .order_by(OHLCVData.datetime.desc())
            .limit(limit)
        ).all()
        if rows:
            return _rows_to_frame(list(reversed(rows)), "datetime")
    query = select(MarketPrice).where(MarketPrice.symbol == clean_symbol)
    if timeframe:
        query = query.where(MarketPrice.timeframe.in_([timeframe, timeframe.upper(), timeframe.lower()]))
    rows = db.scalars(query.order_by(MarketPrice.timestamp.desc()).limit(limit)).all()
    if rows:
        return _rows_to_frame(list(reversed(rows)), "timestamp")
    if requested_timeframe in daily_aliases:
        rows = db.scalars(
            select(OHLCVData)
            .where(OHLCVData.symbol == clean_symbol)
            .order_by(OHLCVData.datetime.desc())
            .limit(limit)
        ).all()
        if rows:
            return _rows_to_frame(list(reversed(rows)), "datetime")
    return clean_ohlcv_frame(pd.DataFrame())


def latest_price(db: Session, symbol: str) -> float | None:
    clean_symbol = normalize_symbol(symbol)
    price = db.scalar(
        select(MarketPrice.close)
        .where(MarketPrice.symbol == clean_symbol, MarketPrice.close.is_not(None))
        .order_by(MarketPrice.timestamp.desc())
    )
    if price is not None:
        return float(price)
    ohlcv_price = db.scalar(
        select(OHLCVData.close)
        .where(OHLCVData.symbol == clean_symbol, OHLCVData.close.is_not(None))
        .order_by(OHLCVData.datetime.desc())
    )
    if ohlcv_price is not None:
        return float(ohlcv_price)
    run = db.scalar(select(TradingViewScreeningRun).order_by(TradingViewScreeningRun.created_at.desc()))
    if run:
        tv_price = db.scalar(
            select(TradingViewScreeningResult.close).where(
                TradingViewScreeningResult.run_id == run.id,
                TradingViewScreeningResult.symbol == clean_symbol,
                TradingViewScreeningResult.close.is_not(None),
            )
        )
        if tv_price is not None:
            return float(tv_price)
    report_price = db.scalar(
        select(DailyEGXReportRow.buy_price)
        .where(DailyEGXReportRow.symbol == clean_symbol, DailyEGXReportRow.buy_price.is_not(None))
        .order_by(DailyEGXReportRow.created_at.desc())
    )
    return float(report_price) if report_price is not None else None


def latest_ohlcv_date(db: Session, symbol: str) -> datetime | None:
    clean_symbol = normalize_symbol(symbol)
    return db.scalar(
        select(OHLCVData.datetime)
        .where(OHLCVData.symbol == clean_symbol)
        .order_by(OHLCVData.datetime.desc())
    ) or db.scalar(
        select(MarketPrice.timestamp)
        .where(MarketPrice.symbol == clean_symbol)
        .order_by(MarketPrice.timestamp.desc())
    )


def load_ohlcv(symbol: str, *, timeframe: str | None = None, limit: int = 300) -> pd.DataFrame:
    with SessionLocal() as db:
        return get_ohlcv(db, symbol, timeframe=timeframe, limit=limit)
