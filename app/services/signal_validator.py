from __future__ import annotations

from typing import Any

from app.config import DISCLAIMER, Settings, get_settings


def _bound(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _get(signal: Any, key: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(key, default)
    return getattr(signal, key, default)


def _format_zone(price: float | None, atr: float | None) -> str | None:
    if price is None:
        return None
    if atr:
        return f"{price - atr * 0.25:.2f} - {price + atr * 0.25:.2f}"
    return f"{price:.2f}"


def validate_signal(
    signal: Any,
    technical: dict[str, Any],
    source: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    direction = (_get(signal, "direction") or "WATCH").upper()
    symbol = (_get(signal, "stock_symbol") or technical.get("symbol") or "").upper()
    indicators = technical.get("indicators") or {}
    last_price = indicators.get("last_price")
    atr = indicators.get("atr_14")
    trend = technical.get("trend_direction") or "UNKNOWN"
    technical_score = float(technical.get("technical_score") or 0)
    risk_score = float(technical.get("risk_score") or 65)
    source_trust = float(getattr(source, "trust_score", 50.0) if source is not None else 50.0)
    support = technical.get("support")
    resistance = technical.get("resistance")
    volume_spike = bool(indicators.get("volume_spike"))
    entry_price = _get(signal, "entry_price") or last_price
    stop_loss = _get(signal, "stop_loss")
    targets = _get(signal, "targets") or []
    hype_words = _get(signal, "hype_words") or []
    risk_flags = _get(signal, "risk_flags") or []

    confidence = 45.0 + (technical_score - 50) * 0.45 + (source_trust - 50) * 0.25
    reasons: list[str] = []
    warnings: list[str] = []

    if direction == "BUY" and trend == "UPTREND" and volume_spike:
        confidence += 18
        reasons.append("Buy signal aligns with uptrend and volume spike.")
    elif direction == "BUY" and trend == "UPTREND":
        confidence += 10
        reasons.append("Buy signal aligns with the current uptrend.")
    elif direction == "BUY" and trend == "DOWNTREND":
        confidence -= 18
        warnings.append("Buy signal conflicts with a downtrend.")

    if direction in {"SELL", "AVOID"}:
        confidence += 6 if trend == "DOWNTREND" else -8
        reasons.append(f"Telegram direction is {direction}.")

    if technical.get("breakout"):
        confidence += 8
        reasons.append("Price is breaking above recent resistance with confirmation.")

    if resistance and last_price and direction == "BUY" and abs(last_price - resistance) / last_price <= 0.025:
        confidence -= 12
        warnings.append("Buy signal is close to resistance, which raises rejection risk.")

    if stop_loss is None and direction in {"BUY", "SELL"}:
        confidence -= 10
        warnings.append("Telegram signal has no stop loss.")

    if hype_words:
        confidence -= min(18, 6 * len(hype_words))
        warnings.append(f"Hype/pump language detected: {', '.join(hype_words)}.")

    if source_trust < 40:
        confidence -= 10
        warnings.append("Source trust score is low.")
    elif source_trust >= 70:
        confidence += 6
        reasons.append("Source trust score is above average.")

    if technical.get("is_mock"):
        confidence -= 12
        warnings.append("Market data is mock data; do not use it for live trading decisions.")

    if not last_price:
        confidence -= 25
        warnings.append("Missing market-data price lowered confidence.")

    if atr and entry_price and targets:
        far_targets = [target for target in targets if abs(float(target) - float(entry_price)) > atr * 3]
        if far_targets:
            confidence -= 8
            warnings.append("One or more targets are more than 3 ATR away from entry.")

    if support and direction == "BUY" and stop_loss is None:
        stop_loss = round(float(support) * 0.985, 2)
        reasons.append("Stop loss was inferred below recent support.")

    if not targets and last_price and atr and direction in {"BUY", "WATCH"}:
        targets = [round(float(last_price) + atr, 2), round(float(last_price) + atr * 2, 2)]
        reasons.append("Targets were estimated from ATR because Telegram targets were missing.")

    if not reasons:
        reasons.append("Signal was evaluated against available technical and source-quality data.")
    warnings.extend(flag for flag in risk_flags if flag not in warnings)

    confidence = _bound(confidence)
    if direction == "SELL":
        final_decision = "SELL" if confidence >= 50 else "NEUTRAL"
    elif direction == "AVOID":
        final_decision = "AVOID"
    elif risk_score >= 78 or (hype_words and stop_loss is None):
        final_decision = "HIGH_RISK"
    elif direction == "BUY" and confidence >= 70:
        final_decision = "BUY"
    elif confidence >= 55:
        final_decision = "WATCH"
    else:
        final_decision = "NEUTRAL"

    invalidation_point = None
    if stop_loss:
        invalidation_point = f"Close below {float(stop_loss):.2f}"
    elif support:
        invalidation_point = f"Close below support near {float(support):.2f}"

    position_size = "Use reduced size until stop loss and liquidity are confirmed."
    if entry_price and stop_loss and float(entry_price) != 0:
        risk_per_share = abs(float(entry_price) - float(stop_loss))
        risk_percent = risk_per_share / float(entry_price) * 100
        if risk_percent > 0:
            capital_pct = min(100.0, settings.default_risk_per_trade_percent / risk_percent * 100)
            position_size = f"Risk {settings.default_risk_per_trade_percent:.1f}% of capital; estimated position value up to {capital_pct:.1f}% of capital."

    return {
        "symbol": symbol,
        "final_decision": final_decision,
        "confidence_score": confidence,
        "entry_zone": _format_zone(float(entry_price), float(atr) if atr else None) if entry_price else None,
        "stop_loss": float(stop_loss) if stop_loss is not None else None,
        "targets": [float(target) for target in targets],
        "reasons": reasons,
        "warnings": warnings,
        "invalidation_point": invalidation_point,
        "position_size_suggestion": position_size,
        "last_price": float(last_price) if last_price is not None else None,
        "trend": trend,
        "risk_score": risk_score,
        "disclaimer": DISCLAIMER,
    }

