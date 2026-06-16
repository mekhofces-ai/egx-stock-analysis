from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.strategy import run_strategy_for_symbol, run_strategy_universe


router = APIRouter(prefix="/strategy", tags=["strategy"])


@router.get("/backtest/{symbol}")
def strategy_backtest_symbol(
    symbol: str,
    timeframes: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    frames = [item.strip() for item in timeframes.split(",") if item.strip()] if timeframes else None
    return run_strategy_for_symbol(db, symbol=symbol, timeframes=frames)


@router.get("/backtest")
def strategy_backtest_universe(
    limit: int = Query(default=30, ge=1, le=100),
    symbols: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    selected = [item.strip().upper() for item in symbols.split(",") if item.strip()] if symbols else None
    return run_strategy_universe(db, settings=get_settings(), limit=limit, symbols=selected)
