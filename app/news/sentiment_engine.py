from __future__ import annotations

POSITIVE_WORDS = {
    "profit",
    "profits",
    "growth",
    "contract",
    "award",
    "dividend",
    "upgrade",
    "increase",
    "strong",
    "positive",
    "شراء",
    "ارباح",
    "توزيع",
    "نمو",
    "ايجابي",
}

NEGATIVE_WORDS = {
    "loss",
    "losses",
    "decline",
    "downgrade",
    "fine",
    "debt",
    "lawsuit",
    "warning",
    "negative",
    "بيع",
    "خسائر",
    "تراجع",
    "سلبي",
    "غرامة",
}


def score_sentiment(text: str | None) -> dict[str, object]:
    value = (text or "").lower()
    positive = sum(1 for word in POSITIVE_WORDS if word.lower() in value)
    negative = sum(1 for word in NEGATIVE_WORDS if word.lower() in value)
    raw = positive - negative
    if raw > 0:
        sentiment = "positive"
    elif raw < 0:
        sentiment = "negative"
    else:
        sentiment = "neutral"
    score = max(-100, min(100, raw * 20))
    return {"sentiment": sentiment, "sentiment_score": float(score), "positive_hits": positive, "negative_hits": negative}

