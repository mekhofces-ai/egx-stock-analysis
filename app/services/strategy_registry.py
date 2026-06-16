from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import RISK_NOTE, Settings, get_settings
from app.database import SessionLocal, sqlite_write_lock
from app.models import Stock, StrategyDefinition, StrategyResult
from app.services.strategy import run_strategy_for_symbol
from app.services.strategies.cli_v6_egx import (
    STRATEGY_NAME as CLI_V6_NAME,
    normalize_symbol,
    run_cli_v6_for_symbol,
)


logger = logging.getLogger(__name__)

LEGACY_CODE = "strategy_legacy"
CLI_V6_CODE = "cli_v6_egx"


def ensure_strategy_registry(db: Session) -> None:
    rows = [
        StrategyDefinition(
            strategy_code=LEGACY_CODE,
            strategy_name="Legacy Multi-Timeframe Strategy",
            description="Existing EMA/RSI/MACD multi-timeframe strategy.",
            is_enabled=True,
            default_timeframe="15m,1h,4h,1D",
            config_json={"source": "app.services.strategy", "risk_note": RISK_NOTE},
        ),
        StrategyDefinition(
            strategy_code=CLI_V6_CODE,
            strategy_name=CLI_V6_NAME,
            description="Composite Leading Indicator v6 EGX optimized strategy.",
            is_enabled=True,
            default_timeframe="15m,30m,1h,4h,1d",
            config_json={"source": "app.services.strategies.cli_v6_egx", "risk_note": RISK_NOTE},
        ),
    ]
    for row in rows:
        existing = db.scalar(select(StrategyDefinition).where(StrategyDefinition.strategy_code == row.strategy_code))
        if existing:
            existing.strategy_name = row.strategy_name
            existing.description = row.description
            existing.default_timeframe = row.default_timeframe
            existing.config_json = row.config_json
        else:
            db.add(row)
    db.commit()


def list_strategies(db: Session | None = None) -> list[dict[str, Any]]:
    def _run(active_db: Session) -> list[dict[str, Any]]:
        ensure_strategy_registry(active_db)
        rows = active_db.scalars(select(StrategyDefinition).order_by(StrategyDefinition.id.asc())).all()
        return [
            {
                "strategy_code": row.strategy_code,
                "strategy_name": row.strategy_name,
                "description": row.description,
                "is_enabled": row.is_enabled,
                "default_timeframe": row.default_timeframe,
                "config_json": row.config_json,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]

    if db is not None:
        return _run(db)
    with SessionLocal() as active_db:
        return _run(active_db)


def get_strategy(db: Session, strategy_code: str) -> StrategyDefinition | None:
    ensure_strategy_registry(db)
    return db.scalar(select(StrategyDefinition).where(StrategyDefinition.strategy_code == strategy_code))


def set_strategy_enabled(db: Session, strategy_code: str, enabled: bool) -> bool:
    row = get_strategy(db, strategy_code)
    if not row:
        return False
    row.is_enabled = bool(enabled)
    row.updated_at = datetime.utcnow()
    db.commit()
    return True


def _persist_result(db: Session, payload: dict[str, Any], run_id: str) -> None:
    row = StrategyResult(
        strategy_code=payload["strategy_code"],
        strategy_name=payload["strategy_name"],
        symbol=payload["symbol"],
        timeframe=payload.get("timeframe") or "summary",
        signal=payload.get("signal"),
        recommendation=payload.get("recommendation"),
        score=payload.get("score"),
        confidence=payload.get("confidence"),
        trend=payload.get("trend"),
        reason=payload.get("reason"),
        details_json=payload.get("details_json"),
        run_id=run_id,
        created_at=datetime.utcnow(),
    )
    db.add(row)


def _run_legacy(db: Session, symbol: str, settings: Settings, run_id: str) -> dict[str, Any]:
    data = run_strategy_for_symbol(db, symbol, settings=settings)
    action = data.get("strategy_action")
    score = float(data.get("strategy_score") or 0)
    payload = {
        "strategy_code": LEGACY_CODE,
        "strategy_name": "Legacy Multi-Timeframe Strategy",
        "symbol": normalize_symbol(symbol),
        "timeframe": "summary",
        "signal": action,
        "recommendation": action,
        "score": score,
        "confidence": score,
        "trend": data.get("data_quality"),
        "reason": "; ".join(
            note
            for row in data.get("timeframes", [])
            for note in (row.get("notes") or [])[:1]
        )[:1000]
        or f"Legacy strategy action {action}.",
        "details_json": data,
    }
    _persist_result(db, payload, run_id)
    for frame in data.get("timeframes", []):
        _persist_result(
            db,
            {
                "strategy_code": LEGACY_CODE,
                "strategy_name": "Legacy Multi-Timeframe Strategy",
                "symbol": normalize_symbol(symbol),
                "timeframe": frame.get("timeframe"),
                "signal": frame.get("action"),
                "recommendation": frame.get("action"),
                "score": frame.get("score"),
                "confidence": frame.get("score"),
                "trend": frame.get("trend"),
                "reason": "; ".join(frame.get("notes") or []) or frame.get("error"),
                "details_json": frame,
            },
            run_id,
        )
    return payload


def _run_cli_v6(db: Session, symbol: str, settings: Settings, run_id: str) -> dict[str, Any]:
    data = run_cli_v6_for_symbol(db, symbol, settings=settings, run_id=run_id, persist=True)
    return {
        "strategy_code": CLI_V6_CODE,
        "strategy_name": CLI_V6_NAME,
        "symbol": normalize_symbol(symbol),
        "timeframe": "summary",
        "signal": data.get("recommendation"),
        "recommendation": data.get("recommendation"),
        "score": data.get("current_score"),
        "confidence": data.get("confidence"),
        "trend": data.get("recommendation"),
        "reason": data.get("reason"),
        "details_json": data,
    }


RUNNERS: dict[str, Callable[[Session, str, Settings, str], dict[str, Any]]] = {
    LEGACY_CODE: _run_legacy,
    CLI_V6_CODE: _run_cli_v6,
}


def _candidate_symbols(db: Session, limit: int | None = None) -> list[str]:
    stmt = select(Stock.symbol).where(Stock.is_active.is_(True)).order_by(Stock.symbol.asc())
    if limit:
        stmt = stmt.limit(limit)
    return [normalize_symbol(symbol) for symbol in db.scalars(stmt).all()]


def run_strategy(
    strategy_code: str,
    symbol: str | None = None,
    db: Session | None = None,
    settings: Settings | None = None,
    limit: int | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    run_id = run_id or f"registry_{strategy_code}_{datetime.utcnow():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"

    def _run(active_db: Session) -> dict[str, Any]:
        ensure_strategy_registry(active_db)
        strategy = active_db.scalar(select(StrategyDefinition).where(StrategyDefinition.strategy_code == strategy_code))
        if not strategy:
            raise ValueError(f"Unknown strategy: {strategy_code}")
        runner = RUNNERS.get(strategy_code)
        if runner is None:
            raise ValueError(f"No runner is registered for {strategy_code}")
        symbols = [normalize_symbol(symbol)] if symbol else _candidate_symbols(active_db, limit=limit)
        rows = []
        errors = []
        for item in symbols:
            try:
                rows.append(runner(active_db, item, settings, run_id))
                active_db.commit()
            except Exception as exc:
                active_db.rollback()
                logger.warning("Strategy %s failed for %s: %s", strategy_code, item, exc)
                errors.append(f"{item}: {exc}")
        return {"run_id": run_id, "strategy_code": strategy_code, "rows": rows, "errors": errors, "symbols_count": len(rows)}

    if db is not None:
        return _run(db)
    with SessionLocal() as active_db:
        return _run(active_db)


def run_all_enabled_strategies(
    symbol: str | None = None,
    db: Session | None = None,
    settings: Settings | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    run_id = f"registry_all_{datetime.utcnow():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"

    def _run(active_db: Session) -> dict[str, Any]:
        ensure_strategy_registry(active_db)
        strategies = active_db.scalars(select(StrategyDefinition).where(StrategyDefinition.is_enabled.is_(True))).all()
        result = {"run_id": run_id, "strategies": [], "rows": [], "errors": []}
        for strategy in strategies:
            try:
                partial = run_strategy(strategy.strategy_code, symbol=symbol, db=active_db, settings=settings, limit=limit, run_id=run_id)
                result["strategies"].append(strategy.strategy_code)
                result["rows"].extend(partial.get("rows") or [])
                result["errors"].extend(partial.get("errors") or [])
            except Exception as exc:
                result["errors"].append(f"{strategy.strategy_code}: {exc}")
        return result

    if db is not None:
        return _run(db)
    with SessionLocal() as active_db:
        return _run(active_db)


def latest_strategy_results(db: Session, symbol: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    stmt = select(StrategyResult).where(StrategyResult.timeframe == "summary")
    if symbol:
        stmt = stmt.where(StrategyResult.symbol == normalize_symbol(symbol))
    rows = db.scalars(stmt.order_by(StrategyResult.created_at.desc(), StrategyResult.id.desc()).limit(limit)).all()
    return [
        {
            "strategy_code": row.strategy_code,
            "strategy_name": row.strategy_name,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "signal": row.signal,
            "recommendation": row.recommendation,
            "score": row.score,
            "confidence": row.confidence,
            "trend": row.trend,
            "reason": row.reason,
            "run_id": row.run_id,
            "created_at": row.created_at,
            "details_json": row.details_json,
        }
        for row in rows
    ]
