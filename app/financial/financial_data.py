from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.data.data_cleaner import normalize_symbol
from app.models import FinancialData


FIELD_ALIASES = {
    "symbol": ["symbol", "ticker"],
    "period": ["period", "date", "year", "quarter"],
    "revenue": ["revenue", "sales"],
    "gross_profit": ["gross_profit", "gross profit"],
    "net_profit": ["net_profit", "net profit", "profit"],
    "ebitda": ["ebitda"],
    "eps": ["eps"],
    "assets": ["assets", "total_assets"],
    "liabilities": ["liabilities", "total_liabilities"],
    "equity": ["equity", "book_value"],
    "debt": ["debt", "total_debt"],
    "cash_flow": ["cash_flow", "operating_cash_flow"],
    "market_price": ["market_price", "price"],
    "shares_outstanding": ["shares_outstanding", "shares"],
}


def _pick(row: pd.Series, aliases: list[str]) -> object | None:
    normalized = {str(col).strip().lower(): col for col in row.index}
    for alias in aliases:
        col = normalized.get(alias)
        if col is not None:
            return row[col]
    return None


def import_financial_csv(db: Session, path: str | Path) -> int:
    df = pd.read_csv(path)
    count = 0
    for _, row in df.iterrows():
        symbol = normalize_symbol(_pick(row, FIELD_ALIASES["symbol"]))
        if not symbol:
            continue
        payload = {"symbol": symbol, "raw_json": {str(k): None if pd.isna(v) else v for k, v in row.items()}}
        for field, aliases in FIELD_ALIASES.items():
            if field == "symbol":
                continue
            value = _pick(row, aliases)
            if pd.isna(value):
                value = None
            if field == "period":
                payload[field] = str(value) if value is not None else None
            else:
                try:
                    payload[field] = float(value) if value is not None else None
                except (TypeError, ValueError):
                    payload[field] = None
        db.add(FinancialData(**payload))
        count += 1
    return count

