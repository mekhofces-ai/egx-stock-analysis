from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AutomationSetting, DynamicWeightsBySymbol


DEFAULT_WEIGHTS = {
    "technical": 35.0,
    "financial": 25.0,
    "news": 20.0,
    "telegram": 10.0,
    "strategy": 10.0,
}


def _setting_float(db: Session, key: str, default: float) -> float:
    row = db.get(AutomationSetting, key)
    try:
        return float(row.value) if row and row.value is not None else default
    except ValueError:
        return default


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0, value) for value in weights.values())
    if total <= 0:
        return DEFAULT_WEIGHTS.copy()
    return {key: round(max(0, value) / total, 4) for key, value in weights.items()}


def weights_for_symbol(db: Session, symbol: str) -> dict[str, float]:
    dynamic = db.scalar(select(DynamicWeightsBySymbol).where(DynamicWeightsBySymbol.symbol == symbol))
    if dynamic:
        return normalize_weights(
            {
                "technical": dynamic.technical_weight,
                "financial": dynamic.financial_weight,
                "news": dynamic.news_weight,
                "telegram": dynamic.telegram_weight,
                "strategy": dynamic.strategy_weight,
            }
        )
    return normalize_weights(
        {
            "technical": _setting_float(db, "final_weight_technical", DEFAULT_WEIGHTS["technical"]),
            "financial": _setting_float(db, "final_weight_financial", DEFAULT_WEIGHTS["financial"]),
            "news": _setting_float(db, "final_weight_news", DEFAULT_WEIGHTS["news"]),
            "telegram": _setting_float(db, "final_weight_telegram", DEFAULT_WEIGHTS["telegram"]),
            "strategy": _setting_float(db, "final_weight_strategy", DEFAULT_WEIGHTS["strategy"]),
        }
    )


def weighted_score(scores: dict[str, float | None], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    available = {key: value for key, value in scores.items() if value is not None}
    if not available:
        return 50.0, {}
    active_weights = {key: weights.get(key, 0.0) for key in available}
    normalized = normalize_weights(active_weights)
    score = sum(float(available[key]) * normalized.get(key, 0.0) for key in available)
    return round(score, 2), normalized

