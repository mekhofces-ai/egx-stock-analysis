from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChannelPerformance, ExtractedSignal, FinalAnalysis, TelegramSource


def update_channel_performance(db: Session) -> list[ChannelPerformance]:
    performances: list[ChannelPerformance] = []
    sources = db.scalars(select(TelegramSource)).all()
    for source in sources:
        signals = db.scalars(select(ExtractedSignal).where(ExtractedSignal.source_id == source.id)).all()
        analyses = db.scalars(select(FinalAnalysis).where(FinalAnalysis.source_id == source.id)).all()
        total = len(signals)
        avg_confidence = None
        if analyses:
            avg_confidence = round(sum(item.confidence_score for item in analyses) / len(analyses), 2)
        fake_signal_count = sum(1 for item in analyses if item.final_decision in {"AVOID", "HIGH_RISK"})
        stop_loss_missing_count = sum(1 for item in signals if item.direction in {"BUY", "SELL"} and item.stop_loss is None)
        pump_words_count = sum(1 for item in signals if item.hype_words)
        symbol_counts = Counter(item.stock_symbol for item in signals if item.stock_symbol)
        risky_counts = Counter(item.symbol for item in analyses if item.final_decision in {"AVOID", "HIGH_RISK"})

        performance = source.performance or ChannelPerformance(source_id=source.id)
        performance.total_signals = total
        performance.win_rate = None
        performance.fake_signal_count = fake_signal_count
        performance.avg_confidence = avg_confidence
        performance.best_symbols = [symbol for symbol, _ in symbol_counts.most_common(5)]
        performance.worst_symbols = [symbol for symbol, _ in risky_counts.most_common(5)]
        performance.stop_loss_missing_count = stop_loss_missing_count
        performance.pump_words_count = pump_words_count
        db.add(performance)
        performances.append(performance)
    db.commit()
    return performances

