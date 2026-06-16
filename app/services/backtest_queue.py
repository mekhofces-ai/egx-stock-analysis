from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, sqlite_write_lock
from app.models import BacktestQueue
from app.services.backtest_cli_v6 import BACKTEST_TIMEFRAMES, run_cli_v6_backtest_symbol
from app.services.strategies.cli_v6_egx import normalize_symbol, normalize_timeframe


logger = logging.getLogger(__name__)


def enqueue_backtest(
    db: Session,
    symbol: str,
    reason: str,
    priority: int = 5,
    requested_by: str | None = None,
) -> BacktestQueue:
    symbol = normalize_symbol(symbol)
    existing = db.scalar(
        select(BacktestQueue)
        .where(BacktestQueue.symbol == symbol, BacktestQueue.status == "pending")
        .order_by(BacktestQueue.priority.asc(), BacktestQueue.created_at.desc())
    )
    if existing:
        existing.priority = min(int(priority), int(existing.priority or priority))
        existing.reason = reason or existing.reason
        existing.requested_by = requested_by or existing.requested_by
        return existing
    item = BacktestQueue(
        symbol=symbol,
        reason=reason,
        priority=max(1, min(10, int(priority or 5))),
        requested_by=requested_by,
        status="pending",
        created_at=datetime.utcnow(),
    )
    db.add(item)
    return item


def enqueue_backtest_sync(symbol: str, reason: str, priority: int = 5, requested_by: str | None = None) -> dict[str, Any]:
    with SessionLocal() as db:
        item = enqueue_backtest(db, symbol, reason=reason, priority=priority, requested_by=requested_by)
        with sqlite_write_lock():
            db.commit()
        return {"id": item.id, "symbol": item.symbol, "status": item.status}


def pending_queue(db: Session, limit: int = 50) -> list[BacktestQueue]:
    return db.scalars(
        select(BacktestQueue)
        .where(BacktestQueue.status == "pending")
        .order_by(BacktestQueue.priority.asc(), BacktestQueue.created_at.asc())
        .limit(limit)
    ).all()


def process_backtest_queue(
    db: Session,
    limit: int = 10,
    timeframes: list[str] | None = None,
    requested_by: str | None = "automation",
) -> dict[str, Any]:
    frames = [normalize_timeframe(frame) for frame in (timeframes or ["1d"])]
    items = pending_queue(db, limit=limit)
    result = {"processed": 0, "failed": 0, "rows": [], "errors": []}
    for item in items:
        item.status = "processing"
        item.processed_at = None
        item.error_message = None
        db.commit()
        symbol = item.symbol
        try:
            rows = []
            for timeframe in frames:
                rows.append(run_cli_v6_backtest_symbol(db, symbol=symbol, timeframe=timeframe))
            item.status = "done"
            item.processed_at = datetime.utcnow()
            item.error_message = None
            db.commit()
            result["processed"] += 1
            result["rows"].append({"symbol": symbol, "timeframes": frames, "results": rows})
        except Exception as exc:
            logger.warning("Queued backtest failed for %s: %s", symbol, exc)
            db.rollback()
            item = db.get(BacktestQueue, item.id)
            if item:
                item.status = "failed"
                item.processed_at = datetime.utcnow()
                item.error_message = str(exc)[:1000]
                db.commit()
            result["failed"] += 1
            result["errors"].append(f"{symbol}: {exc}")
    return result


def add_opportunities_to_queue(db: Session, rows: list[dict[str, Any]], threshold: float = 70.0) -> int:
    count = 0
    for row in rows:
        score = float(row.get("final_score") or 0)
        symbol = row.get("symbol")
        if not symbol or score < threshold:
            continue
        enqueue_backtest(db, symbol, reason=f"High opportunity score {score:.0f}", priority=3, requested_by="opportunity_engine")
        count += 1
    db.commit()
    return count


def queue_status_rows(db: Session, limit: int = 200) -> list[dict[str, Any]]:
    rows = db.scalars(select(BacktestQueue).order_by(BacktestQueue.created_at.desc()).limit(limit)).all()
    return [
        {
            "id": row.id,
            "symbol": row.symbol,
            "reason": row.reason,
            "priority": row.priority,
            "status": row.status,
            "requested_by": row.requested_by,
            "created_at": row.created_at,
            "processed_at": row.processed_at,
            "error_message": row.error_message,
        }
        for row in rows
    ]
