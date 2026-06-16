from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DynamicWeightsBySymbol, SignalAccuracyTracking


SOURCE_COLUMNS = {
    "technical": "technical_correct",
    "financial": "financial_correct",
    "news": "news_correct",
    "telegram": "telegram_correct",
    "strategy": "strategy_correct",
}


def _accuracy(rows: list[SignalAccuracyTracking], column: str) -> float:
    values = [getattr(row, column) for row in rows if getattr(row, column) is not None]
    if not values:
        return 0.5
    return sum(1 for value in values if value) / len(values)


def update_dynamic_weights(db: Session, *, min_samples: int = 3) -> dict[str, int]:
    symbols = db.scalars(select(SignalAccuracyTracking.symbol).distinct()).all()
    updated = 0
    skipped = 0
    for symbol in symbols:
        rows = db.scalars(select(SignalAccuracyTracking).where(SignalAccuracyTracking.symbol == symbol).limit(100)).all()
        if len(rows) < min_samples:
            skipped += 1
            continue
        accuracies = {source: _accuracy(rows, column) for source, column in SOURCE_COLUMNS.items()}
        raw = {source: 10 + accuracy * 50 for source, accuracy in accuracies.items()}
        total = sum(raw.values()) or 1
        normalized = {source: round(value / total * 100, 2) for source, value in raw.items()}
        row = db.scalar(select(DynamicWeightsBySymbol).where(DynamicWeightsBySymbol.symbol == symbol))
        if not row:
            row = DynamicWeightsBySymbol(symbol=symbol)
        row.technical_weight = normalized["technical"]
        row.financial_weight = normalized["financial"]
        row.news_weight = normalized["news"]
        row.telegram_weight = normalized["telegram"]
        row.strategy_weight = normalized["strategy"]
        db.add(row)
        updated += 1
    return {"updated": updated, "skipped": skipped}

