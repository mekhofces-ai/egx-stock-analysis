from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.data_cleaner import normalize_symbol
from app.database import SessionLocal
from app.models import Stock


def list_active_symbols(db: Session | None = None, limit: int | None = None) -> list[str]:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        query = select(Stock.symbol).where(Stock.is_active.is_(True)).order_by(Stock.symbol.asc())
        if limit:
            query = query.limit(limit)
        return [normalize_symbol(symbol) for symbol in session.scalars(query).all() if normalize_symbol(symbol)]
    finally:
        if owns_session:
            session.close()


def symbol_name_map(db: Session | None = None) -> dict[str, str]:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        rows = session.scalars(select(Stock).where(Stock.is_active.is_(True))).all()
        return {
            normalize_symbol(row.symbol): (row.name or row.name_en or row.name_ar or row.symbol)
            for row in rows
            if normalize_symbol(row.symbol)
        }
    finally:
        if owns_session:
            session.close()

