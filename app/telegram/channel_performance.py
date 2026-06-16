from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TelegramChannelPerformance, TelegramMessage, TelegramMessageSymbol


def update_telegram_channel_performance(db: Session) -> list[TelegramChannelPerformance]:
    rows = db.scalars(select(TelegramMessageSymbol).order_by(TelegramMessageSymbol.created_at.desc()).limit(5000)).all()
    by_channel_symbol: dict[tuple[str, str], list[TelegramMessageSymbol]] = {}
    for row in rows:
        channel = row.source or "telegram"
        if row.telegram_message_id:
            msg = db.get(TelegramMessage, row.telegram_message_id)
            channel = (msg.channel_name if msg else None) or channel
        by_channel_symbol.setdefault((channel, row.symbol), []).append(row)

    output: list[TelegramChannelPerformance] = []
    symbol_counter: Counter[str] = Counter()
    for (channel, symbol), calls in by_channel_symbol.items():
        symbol_counter.update([symbol] * len(calls))
        perf = db.scalar(
            select(TelegramChannelPerformance).where(
                TelegramChannelPerformance.channel_name == channel,
                TelegramChannelPerformance.symbol == symbol,
            )
        )
        if not perf:
            perf = TelegramChannelPerformance(channel_name=channel, symbol=symbol)
        perf.total_calls = len(calls)
        perf.correct_calls = 0
        perf.wrong_calls = 0
        perf.win_rate = None
        perf.average_return = None
        perf.best_symbol = symbol_counter.most_common(1)[0][0] if symbol_counter else symbol
        perf.worst_symbol = None
        db.add(perf)
        output.append(perf)
    return output

