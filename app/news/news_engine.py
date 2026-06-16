from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NewsSignal, StockNews


def _bound(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def analyze_news(db: Session, symbol: str, *, days: int = 30, persist: bool = True) -> dict[str, Any]:
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.scalars(
        select(StockNews)
        .where(StockNews.symbol == symbol, (StockNews.published_at.is_(None)) | (StockNews.published_at >= since))
        .order_by(StockNews.published_at.desc().nullslast(), StockNews.created_at.desc())
        .limit(20)
    ).all()
    if not rows:
        result = {
            "symbol": symbol,
            "news_signal": "NEUTRAL",
            "news_score": 50.0,
            "main_news_drivers": [],
            "reason": "No recent stored news found for this symbol.",
        }
        if persist:
            db.add(NewsSignal(symbol=symbol, news_signal="NEUTRAL", news_score=50.0, main_news_drivers=[], reason=result["reason"]))
        return result
    weighted = []
    drivers = []
    for row in rows:
        sentiment = float(row.sentiment_score or 0)
        impact = float(row.impact_score or 25)
        weighted.append(sentiment * (impact / 100.0))
        drivers.append(row.title or row.body[:120] if row.body else row.source or "news")
    avg = sum(weighted) / max(1, len(weighted))
    score = _bound(50 + avg)
    signal = "BULLISH" if score >= 65 else "BEARISH" if score < 40 else "NEUTRAL"
    reason = f"{len(rows)} recent news item(s), weighted sentiment score {score:.0f}."
    if persist:
        db.add(NewsSignal(symbol=symbol, news_signal=signal, news_score=score, main_news_drivers=drivers[:5], reason=reason))
    return {"symbol": symbol, "news_signal": signal, "news_score": score, "main_news_drivers": drivers[:5], "reason": reason}

