from __future__ import annotations

from dataclasses import dataclass

from app.database import SessionLocal
from app.services.analysis_runner import analyze_pending_signals
from app.services.alerts import (
    send_buy_alerts_for_analyses,
    send_buy_alerts_for_analyses_async,
    send_buy_recommendation_alerts,
    send_buy_recommendation_alerts_async,
)
from app.services.telegram_listener import TelegramListener, fetch_active_channels_once
from app.services.message_understanding import process_unclassified_messages


@dataclass
class IngestionResult:
    inserted_messages: int
    new_analyses: int
    signal_alerts: dict
    recommendation_alerts: dict

    def to_dict(self) -> dict:
        return {
            "inserted_messages": self.inserted_messages,
            "new_analyses": self.new_analyses,
            "signal_alerts": self.signal_alerts,
            "recommendation_alerts": self.recommendation_alerts,
        }


def _skipped_alerts() -> dict:
    return {"configured": True, "eligible": 0, "sent": 0, "skipped": True, "reason": "alerts disabled for this ingestion run"}


def run_ingestion_cycle(limit: int = 500, *, send_alerts: bool = True) -> IngestionResult:
    inserted = fetch_active_channels_once()
    with SessionLocal() as db:
        process_unclassified_messages(db, limit=limit)
        analyses = analyze_pending_signals(db, limit=limit)
        signal_alerts = send_buy_alerts_for_analyses(db, analyses) if send_alerts else _skipped_alerts()
        recommendation_alerts = send_buy_recommendation_alerts(db) if send_alerts else _skipped_alerts()
    return IngestionResult(
        inserted_messages=inserted,
        new_analyses=len(analyses),
        signal_alerts=signal_alerts,
        recommendation_alerts=recommendation_alerts,
    )


async def run_ingestion_cycle_async(limit: int = 500, *, send_alerts: bool = True) -> IngestionResult:
    inserted = await TelegramListener().fetch_once()
    with SessionLocal() as db:
        process_unclassified_messages(db, limit=limit)
        analyses = analyze_pending_signals(db, limit=limit)
        signal_alerts = await send_buy_alerts_for_analyses_async(db, analyses) if send_alerts else _skipped_alerts()
        recommendation_alerts = await send_buy_recommendation_alerts_async(db) if send_alerts else _skipped_alerts()
    return IngestionResult(
        inserted_messages=inserted,
        new_analyses=len(analyses),
        signal_alerts=signal_alerts,
        recommendation_alerts=recommendation_alerts,
    )
