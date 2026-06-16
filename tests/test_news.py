from __future__ import annotations

from app.news.sentiment_engine import score_sentiment


def test_sentiment_positive_negative() -> None:
    assert score_sentiment("profit growth dividend")["sentiment"] == "positive"
    assert score_sentiment("loss decline warning")["sentiment"] == "negative"
    assert score_sentiment("regular disclosure")["sentiment"] == "neutral"

