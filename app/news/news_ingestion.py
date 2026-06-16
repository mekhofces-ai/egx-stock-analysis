from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.data.data_cleaner import normalize_symbol
from app.models import StockNews
from app.news.sentiment_engine import score_sentiment


def import_news_csv(db: Session, path: str | Path, *, source: str = "manual_csv") -> int:
    df = pd.read_csv(path)
    count = 0
    for _, row in df.iterrows():
        symbol = normalize_symbol(row.get("symbol") or row.get("ticker"))
        text = " ".join(str(row.get(col) or "") for col in ["title", "body", "text", "news"])
        sentiment = score_sentiment(text)
        published = pd.to_datetime(row.get("published_at") or row.get("date"), errors="coerce")
        db.add(
            StockNews(
                symbol=symbol or None,
                title=str(row.get("title") or "")[:500],
                body=str(row.get("body") or row.get("text") or ""),
                source=str(row.get("source") or source),
                published_at=published.to_pydatetime() if not pd.isna(published) else None,
                sentiment=str(sentiment["sentiment"]),
                sentiment_score=float(sentiment["sentiment_score"]),
                impact_score=min(100.0, abs(float(sentiment["sentiment_score"])) + 20),
                expected_impact_duration=str(row.get("expected_impact_duration") or "short"),
                raw_json={str(k): None if pd.isna(v) else v for k, v in row.items()},
            )
        )
        count += 1
    return count

