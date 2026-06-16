from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FinalStockDecision, MarketPrice, OHLCVData, SignalAccuracyTracking


WINDOWS = [1, 3, 5, 10, 20]


def _price_on_or_after(db: Session, symbol: str, dt: datetime) -> float | None:
    price = db.scalar(
        select(OHLCVData.close)
        .where(OHLCVData.symbol == symbol, OHLCVData.datetime >= dt, OHLCVData.close.is_not(None))
        .order_by(OHLCVData.datetime.asc())
    )
    if price is not None:
        return float(price)
    price = db.scalar(
        select(MarketPrice.close)
        .where(MarketPrice.symbol == symbol, MarketPrice.timestamp >= dt, MarketPrice.close.is_not(None))
        .order_by(MarketPrice.timestamp.asc())
    )
    return float(price) if price is not None else None


def _is_bullish(signal: str | None) -> bool:
    return str(signal or "").upper() in {"STRONG BUY", "BUY", "BULLISH"}


def _is_bearish(signal: str | None) -> bool:
    return str(signal or "").upper() in {"SELL", "AVOID", "AVOID / SELL", "BEARISH", "STRONG SELL"}


def _correct(signal: str | None, move_pct: float | None, threshold: float = 1.0) -> bool | None:
    if move_pct is None:
        return None
    if _is_bullish(signal):
        return move_pct >= threshold
    if _is_bearish(signal):
        return move_pct <= -threshold
    return abs(move_pct) < threshold


def update_signal_accuracy(db: Session, *, limit: int = 250) -> dict[str, Any]:
    decisions = db.scalars(select(FinalStockDecision).order_by(FinalStockDecision.decision_date.desc()).limit(limit)).all()
    updated = 0
    missing_prices = 0
    for decision in decisions:
        entry_price = decision.entry_price or _price_on_or_after(db, decision.symbol, decision.decision_date)
        if not entry_price:
            missing_prices += 1
            continue
        row = db.scalar(
            select(SignalAccuracyTracking).where(
                SignalAccuracyTracking.symbol == decision.symbol,
                SignalAccuracyTracking.decision_date == decision.decision_date,
            )
        )
        if not row:
            row = SignalAccuracyTracking(symbol=decision.symbol, decision_date=decision.decision_date)
        moves: dict[int, float | None] = {}
        prices: dict[int, float | None] = {}
        for days in WINDOWS:
            price = _price_on_or_after(db, decision.symbol, decision.decision_date + timedelta(days=days))
            prices[days] = price
            moves[days] = round(((price - entry_price) / entry_price) * 100, 4) if price is not None else None
            setattr(row, f"price_after_{days}d", price)
            setattr(row, f"move_{days}d_pct", moves[days])
        main_move = moves.get(5) if moves.get(5) is not None else next((value for value in moves.values() if value is not None), None)
        source_signals = {
            "technical": "BULLISH" if (decision.technical_score or 50) >= 60 else "BEARISH" if (decision.technical_score or 50) < 40 else "NEUTRAL",
            "financial": "BULLISH" if (decision.financial_score or 50) >= 60 else "BEARISH" if (decision.financial_score or 50) < 40 else "NEUTRAL",
            "news": "BULLISH" if (decision.news_score or 50) >= 60 else "BEARISH" if (decision.news_score or 50) < 40 else "NEUTRAL",
            "telegram": "BULLISH" if (decision.telegram_score or 50) >= 60 else "BEARISH" if (decision.telegram_score or 50) < 40 else "NEUTRAL",
            "strategy": "BULLISH" if (decision.strategy_score or 50) >= 60 else "BEARISH" if (decision.strategy_score or 50) < 40 else "NEUTRAL",
        }
        row.technical_correct = _correct(source_signals["technical"], main_move)
        row.financial_correct = _correct(source_signals["financial"], main_move)
        row.news_correct = _correct(source_signals["news"], main_move)
        row.telegram_correct = _correct(source_signals["telegram"], main_move)
        row.strategy_correct = _correct(source_signals["strategy"], main_move)
        row.final_decision_correct = _correct(decision.final_signal, main_move)
        candidates = {
            "technical": decision.technical_score,
            "financial": decision.financial_score,
            "news": decision.news_score,
            "telegram": decision.telegram_score,
            "strategy": decision.strategy_score,
        }
        row.actual_best_driver = max(candidates.items(), key=lambda item: abs((item[1] or 50) - 50))[0]
        row.check_date = datetime.utcnow()
        row.details_json = {"entry_price": entry_price, "source_signals": source_signals, "prices": prices, "moves": moves}
        db.add(row)
        updated += 1
    return {"updated": updated, "missing_prices": missing_prices}

