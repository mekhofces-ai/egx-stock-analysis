from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TelegramMessage, TelegramMessageSymbol, TelegramSignal
from app.news.sentiment_engine import score_sentiment
from app.services.message_understanding import understand_telegram_message


TARGET_RE = re.compile(r"(?:target|tp|هدف|مستهدف)\s*[:=]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
STOP_RE = re.compile(r"(?:stop|sl|وقف|ايقاف)\s*[:=]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def classify_message_type(text: str, has_image: bool = False) -> str:
    lower = (text or "").lower()
    if has_image and any(word in lower for word in ["chart", "شارت", "تحليل"]):
        return "technical chart"
    if any(word in lower for word in ["rumor", "اشاعة", "اشاعه"]):
        return "rumor"
    if any(word in lower for word in ["news", "خبر", "اعلان", "disclosure"]):
        return "news"
    if any(word in lower for word in ["buy", "sell", "شراء", "بيع", "target", "هدف"]):
        return "recommendation"
    if any(word in lower for word in ["warning", "تحذير", "خطر"]):
        return "warning"
    return "discussion"


def enrich_message(db: Session, message: TelegramMessage) -> dict[str, Any]:
    text = message.message_text or message.text or ""
    understanding = understand_telegram_message(db, message)
    sentiment = score_sentiment(text)
    symbols = understanding.get("symbols") or []
    primary = symbols[0]["symbol"] if symbols else None
    intent = (understanding.get("intent") or {}).get("primary_intent")
    target_match = TARGET_RE.search(text)
    stop_match = STOP_RE.search(text)
    message.symbol = primary
    message.sentiment = str(sentiment["sentiment"])
    message.recommendation_type = intent
    message.target_price = float(target_match.group(1)) if target_match else message.target_price
    message.stop_loss = float(stop_match.group(1)) if stop_match else message.stop_loss
    message.has_image = bool(message.media_path or message.image_path)
    message.message_type = classify_message_type(text, bool(message.has_image))
    message.parsed = True
    return {
        "symbols": symbols,
        "intent": intent,
        "sentiment": sentiment,
        "message_type": message.message_type,
        "target_price": message.target_price,
        "stop_loss": message.stop_loss,
    }


def process_recent_messages(db: Session, *, limit: int = 250) -> dict[str, Any]:
    rows = db.scalars(select(TelegramMessage).order_by(TelegramMessage.created_at.desc()).limit(limit)).all()
    processed = 0
    symbols = 0
    errors: list[str] = []
    for message in rows:
        try:
            result = enrich_message(db, message)
            processed += 1
            symbols += len(result.get("symbols") or [])
        except Exception as exc:
            errors.append(f"{message.id}: {exc}")
    return {"processed": processed, "symbols": symbols, "errors": errors}


def analyze_telegram_for_symbol(db: Session, symbol: str, *, days: int = 30, persist: bool = True) -> dict[str, Any]:
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.scalars(
        select(TelegramMessageSymbol)
        .where(TelegramMessageSymbol.symbol == symbol, TelegramMessageSymbol.created_at >= since)
        .order_by(TelegramMessageSymbol.created_at.desc())
        .limit(100)
    ).all()
    if not rows:
        result = {
            "symbol": symbol,
            "telegram_signal": "NEUTRAL",
            "telegram_score": 50.0,
            "top_channels": [],
            "reason": "No recent Telegram mentions found.",
        }
        if persist:
            db.add(TelegramSignal(symbol=symbol, telegram_signal="NEUTRAL", telegram_score=50.0, top_channels=[], reason=result["reason"]))
        return result

    score = 50.0
    buy_count = sum(1 for row in rows if (row.intent or "").lower() in {"buy", "watch", "target", "support", "image_ocr"})
    sell_count = sum(1 for row in rows if (row.intent or "").lower() in {"sell", "avoid", "stop_loss", "resistance"})
    score += min(20, len(rows) * 1.5)
    score += buy_count * 3
    score -= sell_count * 4
    score = round(max(0, min(100, score)), 2)
    signal = "BULLISH" if score >= 65 else "BEARISH" if score < 40 else "NEUTRAL"
    source_counts: dict[str, int] = {}
    for row in rows:
        source_counts[row.source or "telegram"] = source_counts.get(row.source or "telegram", 0) + 1
    top_channels = [key for key, _ in sorted(source_counts.items(), key=lambda item: item[1], reverse=True)[:5]]
    reason = f"{len(rows)} recent mention(s): {buy_count} bullish/watch, {sell_count} bearish/risk."
    if persist:
        db.add(TelegramSignal(symbol=symbol, telegram_signal=signal, telegram_score=score, top_channels=top_channels, reason=reason))
    return {"symbol": symbol, "telegram_signal": signal, "telegram_score": score, "top_channels": top_channels, "reason": reason}

