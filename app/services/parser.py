from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


NUMBER_RE = re.compile(r"(?<!\w)(\d+(?:[\.,]\d+)?)(?!\w)")
SYMBOL_RE = re.compile(r"(?:EGX[:\s-]?)?([A-Z]{2,6})(?:\.CA)?\b")
STOP_WORD_SYMBOLS = {
    "BUY",
    "SELL",
    "WATCH",
    "HOLD",
    "AVOID",
    "SL",
    "TP",
    "RSI",
    "MACD",
    "EGX",
}


DIRECTION_KEYWORDS: dict[str, list[str]] = {
    "BUY": ["buy", "شراء", "تجميع", "دخول", "اختراق", "accumulation", "breakout"],
    "SELL": ["sell", "بيع", "تصريف"],
    "WATCH": ["watch", "مراقبة", "تابع", "متابعة"],
    "HOLD": ["hold", "احتفاظ", "امسك"],
    "AVOID": ["avoid", "ابتعد", "تجنب", "خطر"],
}

HYPE_WORDS = [
    "pump",
    "urgent",
    "rocket",
    "guaranteed",
    "sure",
    "moon",
    "صاروخ",
    "مضمون",
    "الحق",
    "فرصة العمر",
    "طيران",
]

ENTRY_KEYWORDS = ["entry", "enter", "buy", "price", "دخول", "شراء", "منطقة شراء", "حول", "عند"]
TARGET_KEYWORDS = ["target", "targets", "tp", "هدف", "اهداف", "مستهدف"]
STOP_KEYWORDS = ["stop loss", "stop", "sl", "وقف خسارة", "ايقاف خسارة"]
SUPPORT_KEYWORDS = ["support", "دعم"]
RESISTANCE_KEYWORDS = ["resistance", "مقاومة"]


@dataclass
class ParsedSignal:
    stock_symbol: str | None = None
    stock_name: str | None = None
    direction: str | None = None
    entry_price: float | None = None
    targets: list[float] = field(default_factory=list)
    stop_loss: float | None = None
    support: float | None = None
    resistance: float | None = None
    timeframe: str | None = None
    hype_words: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_symbol": self.stock_symbol,
            "stock_name": self.stock_name,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "targets": self.targets,
            "stop_loss": self.stop_loss,
            "support": self.support,
            "resistance": self.resistance,
            "timeframe": self.timeframe,
            "hype_words": self.hype_words,
            "risk_flags": self.risk_flags,
            "sentiment_score": self.sentiment_score,
            "raw": self.raw,
        }


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[\u064b-\u065f\u0670]", "", text)
    return text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي")


def _lower(text: str) -> str:
    return normalize_text(text).lower()


def _parse_number(value: str) -> float | None:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _numbers(text: str) -> list[float]:
    values: list[float] = []
    for match in NUMBER_RE.finditer(text):
        number = _parse_number(match.group(1))
        if number is not None:
            values.append(number)
    return values


def _numbers_after_keywords(text: str, keywords: list[str], window: int = 90) -> list[float]:
    lower_text = _lower(text)
    found: list[float] = []
    for keyword in keywords:
        key = _lower(keyword)
        start = lower_text.find(key)
        while start != -1:
            segment = text[start : start + len(keyword) + window]
            found.extend(_numbers(segment))
            start = lower_text.find(key, start + len(key))
    return found


def extract_targets(text: str) -> list[float]:
    lower_text = _lower(text)
    found: list[float] = []
    cutoff_keywords = STOP_KEYWORDS + SUPPORT_KEYWORDS + RESISTANCE_KEYWORDS
    for keyword in TARGET_KEYWORDS:
        key = _lower(keyword)
        start = lower_text.find(key)
        while start != -1:
            segment_end = start + len(keyword) + 110
            for cutoff in cutoff_keywords:
                cutoff_index = lower_text.find(_lower(cutoff), start + len(key))
                if cutoff_index != -1:
                    segment_end = min(segment_end, cutoff_index)
            found.extend(_numbers(text[start:segment_end]))
            start = lower_text.find(key, start + len(key))
    return found


def _first_number_after(text: str, keywords: list[str]) -> float | None:
    numbers = _numbers_after_keywords(text, keywords, window=50)
    return numbers[0] if numbers else None


def extract_symbol(
    text: str,
    known_symbols: list[str] | None = None,
    known_aliases: dict[str, list[str]] | None = None,
) -> str | None:
    normalized = normalize_text(text)
    upper = normalized.upper()
    if known_symbols:
        for symbol in sorted({s.upper() for s in known_symbols}, key=len, reverse=True):
            if re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", upper):
                return symbol
    if known_aliases:
        lower_text = _lower(normalized)
        for symbol, aliases in known_aliases.items():
            for alias in aliases:
                cleaned_alias = _lower(alias)
                if len(cleaned_alias) >= 4 and cleaned_alias in lower_text:
                    return symbol.upper()
    if known_symbols or known_aliases:
        return None
    for match in SYMBOL_RE.finditer(upper):
        candidate = match.group(1).upper()
        if candidate not in STOP_WORD_SYMBOLS:
            return candidate
    return None


def extract_direction(text: str) -> str | None:
    lower_text = _lower(text)
    scores: dict[str, int] = {}
    for direction, keywords in DIRECTION_KEYWORDS.items():
        scores[direction] = sum(1 for keyword in keywords if _lower(keyword) in lower_text)
    best_direction, best_score = max(scores.items(), key=lambda item: item[1])
    return best_direction if best_score > 0 else None


def extract_timeframe(text: str) -> str | None:
    lower_text = _lower(text)
    patterns = {
        "intraday": ["intraday", "1h", "hour", "ساعة", "لحظي"],
        "daily": ["daily", "day", "يومي", "جلسة"],
        "weekly": ["weekly", "week", "اسبوعي", "اسبوع"],
        "monthly": ["monthly", "month", "شهري", "شهر"],
        "swing": ["swing", "سوينج"],
    }
    for timeframe, keywords in patterns.items():
        if any(keyword in lower_text for keyword in keywords):
            return timeframe
    return None


def extract_hype_words(text: str) -> list[str]:
    lower_text = _lower(text)
    return [word for word in HYPE_WORDS if _lower(word) in lower_text]


def sentiment_for(direction: str | None, hype_words: list[str], risk_flags: list[str]) -> float:
    score = 0.0
    if direction == "BUY":
        score += 0.45
    elif direction == "WATCH":
        score += 0.15
    elif direction == "HOLD":
        score += 0.05
    elif direction == "SELL":
        score -= 0.45
    elif direction == "AVOID":
        score -= 0.65
    if hype_words:
        score += 0.1
    score -= min(0.4, 0.08 * len(risk_flags))
    return round(max(-1.0, min(1.0, score)), 3)


def parse_message(
    text: str,
    known_symbols: list[str] | None = None,
    known_aliases: dict[str, list[str]] | None = None,
) -> ParsedSignal:
    text = text or ""
    normalized = normalize_text(text)
    direction = extract_direction(normalized)
    targets = extract_targets(normalized)
    hype_words = extract_hype_words(normalized)
    stock_symbol = extract_symbol(normalized, known_symbols=known_symbols, known_aliases=known_aliases)

    entry_price = _first_number_after(normalized, ENTRY_KEYWORDS)
    stop_loss = _first_number_after(normalized, STOP_KEYWORDS)
    support = _first_number_after(normalized, SUPPORT_KEYWORDS)
    resistance = _first_number_after(normalized, RESISTANCE_KEYWORDS)

    risk_flags: list[str] = []
    if not stock_symbol:
        risk_flags.append("missing_stock_symbol")
    if not direction:
        risk_flags.append("missing_direction")
    if direction in {"BUY", "SELL"} and stop_loss is None:
        risk_flags.append("missing_stop_loss")
    if hype_words:
        risk_flags.append("hype_or_pump_language")
    if direction == "BUY" and not targets:
        risk_flags.append("missing_targets")

    # If the message contains a single obvious number and no explicit entry, treat it as a watch price only.
    all_numbers = _numbers(normalized)
    if entry_price is None and direction in {"BUY", "WATCH"} and len(all_numbers) == 1:
        entry_price = all_numbers[0]

    signal = ParsedSignal(
        stock_symbol=stock_symbol,
        direction=direction,
        entry_price=entry_price,
        targets=targets[:5],
        stop_loss=stop_loss,
        support=support,
        resistance=resistance,
        timeframe=extract_timeframe(normalized),
        hype_words=hype_words,
        risk_flags=risk_flags,
        raw={"text": text, "normalized": normalized},
    )
    signal.sentiment_score = sentiment_for(signal.direction, signal.hype_words, signal.risk_flags)
    return signal
