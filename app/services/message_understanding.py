from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Stock, TelegramMessage, TelegramMessageSymbol
from app.services.backtest_queue import enqueue_backtest
from app.services.parser import (
    extract_direction,
    extract_symbol,
    normalize_text,
    parse_message,
)
from app.services.stock_aliases import COMMON_ARABIC_STOCK_ALIASES
from app.services.strategies.cli_v6_egx import normalize_symbol


INTENT_KEYWORDS: dict[str, list[str]] = {
    "buy": ["buy", "entry", "accumulate", "breakout", "شراء", "دخول", "تجميع", "اختراق"],
    "sell": ["sell", "exit", "take profit", "بيع", "خروج", "تصريف"],
    "hold": ["hold", "keep", "احتفاظ", "امسك"],
    "target": ["target", "tp", "هدف", "اهداف", "مستهدف"],
    "stop_loss": ["stop", "sl", "stop loss", "وقف خسارة", "ايقاف خسارة"],
    "support": ["support", "دعم"],
    "resistance": ["resistance", "مقاومة"],
    "rumor_news": ["news", "rumor", "خبر", "اشاعة", "اشاعه"],
}


def _clean_symbol(value: str) -> str:
    return normalize_symbol(str(value).replace(".CA", ""))


def stock_aliases(db: Session) -> tuple[list[str], dict[str, list[str]]]:
    stocks = db.scalars(select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.symbol.asc())).all()
    symbols = [_clean_symbol(stock.symbol) for stock in stocks]
    aliases: dict[str, list[str]] = {symbol: list(COMMON_ARABIC_STOCK_ALIASES.get(symbol, [])) for symbol in symbols}
    for stock in stocks:
        symbol = _clean_symbol(stock.symbol)
        values = aliases.setdefault(symbol, [])
        for value in [stock.name_ar, stock.name_en, stock.tradingview_symbol]:
            if value and str(value).strip():
                values.append(str(value).replace("EGX:", "").replace(".CA", ""))
        if symbol == "COMI":
            values.extend(["CIB", "Commercial International Bank"])
    return symbols, aliases


def classify_intent(text: str) -> dict[str, Any]:
    normalized = normalize_text(text or "")
    lower = normalized.lower()
    hits: list[str] = []
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(keyword.lower() in lower for keyword in keywords):
            hits.append(intent)
    direction = extract_direction(normalized)
    if direction:
        mapped = {"BUY": "buy", "SELL": "sell", "WATCH": "watch", "HOLD": "hold", "AVOID": "avoid"}.get(direction)
        if mapped and mapped not in hits:
            hits.insert(0, mapped)
    if not hits:
        hits.append("mention")
    return {"primary_intent": hits[0], "intents": hits, "direction": direction}


def extract_symbols(text: str, db: Session) -> list[dict[str, Any]]:
    text = text or ""
    symbols, aliases = stock_aliases(db)
    found: dict[str, dict[str, Any]] = {}

    direct = extract_symbol(text, known_symbols=symbols, known_aliases=aliases)
    if direct:
        found[_clean_symbol(direct)] = {"symbol": _clean_symbol(direct), "confidence": 0.9, "source": "parser", "reason": "matched symbol or alias"}

    upper = normalize_text(text).upper()
    for symbol in symbols:
        if re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?:\.CA)?(?![A-Z0-9])", upper):
            found[symbol] = {"symbol": symbol, "confidence": 0.95, "source": "direct_symbol", "reason": "direct ticker match"}

    lower = normalize_text(text).lower()
    for symbol, values in aliases.items():
        for alias in values:
            cleaned = normalize_text(str(alias)).lower().strip()
            if len(cleaned) >= 3 and cleaned in lower:
                score = 0.85 if len(cleaned) >= 5 else 0.7
                current = found.get(symbol)
                if current is None or score > current["confidence"]:
                    found[symbol] = {"symbol": symbol, "confidence": score, "source": "alias", "reason": f"matched alias: {alias}"}

    return sorted(found.values(), key=lambda item: item["confidence"], reverse=True)


def understand_text(text: str, db: Session) -> dict[str, Any]:
    parsed = parse_message(text or "", known_symbols=stock_aliases(db)[0], known_aliases=stock_aliases(db)[1])
    return {
        "symbols": extract_symbols(text or "", db),
        "intent": classify_intent(text or ""),
        "parsed_signal": parsed.to_dict(),
    }


def store_message_symbols(
    db: Session,
    telegram_message_id: int | None,
    symbols: list[dict[str, Any]],
    intent: str,
    source: str,
    queue_reason: str,
) -> int:
    inserted = 0
    existing_symbols: set[str] = set()
    if telegram_message_id:
        existing_symbols = {
            row.symbol
            for row in db.scalars(
                select(TelegramMessageSymbol).where(
                    TelegramMessageSymbol.telegram_message_id == telegram_message_id,
                    TelegramMessageSymbol.source == source,
                )
            ).all()
        }
    for item in symbols:
        symbol = _clean_symbol(item["symbol"])
        if telegram_message_id and symbol in existing_symbols:
            continue
        db.add(
            TelegramMessageSymbol(
                telegram_message_id=telegram_message_id,
                symbol=symbol,
                confidence=float(item.get("confidence") or 0),
                source=source,
                reason=item.get("reason"),
                intent=intent,
            )
        )
        enqueue_backtest(db, symbol, reason=queue_reason, priority=4, requested_by=source)
        inserted += 1
    return inserted


def understand_telegram_message(db: Session, message: TelegramMessage) -> dict[str, Any]:
    text = message.message_text or message.text or ""
    result = understand_text(text, db)
    primary_intent = result["intent"]["primary_intent"]
    inserted = store_message_symbols(
        db,
        telegram_message_id=message.id,
        symbols=result["symbols"],
        intent=primary_intent,
        source="telegram_text",
        queue_reason=f"Telegram {primary_intent} mention",
    )
    message.message_text = text
    message.channel_id = message.channel_id or str(message.source_id)
    message.channel_name = message.channel_name or (message.source.title if message.source else None)
    return {**result, "stored_symbols": inserted}


def process_unclassified_messages(db: Session, limit: int = 250) -> dict[str, Any]:
    rows = db.scalars(
        select(TelegramMessage)
        .where(TelegramMessage.created_at >= datetime.utcnow() - timedelta(days=30))
        .order_by(TelegramMessage.created_at.desc())
        .limit(limit)
    ).all()
    processed = 0
    symbols = 0
    errors: list[str] = []
    for message in rows:
        try:
            result = understand_telegram_message(db, message)
            processed += 1
            symbols += int(result.get("stored_symbols") or 0)
        except Exception as exc:
            errors.append(f"{message.id}: {exc}")
    db.commit()
    return {"processed": processed, "symbols": symbols, "errors": errors}
