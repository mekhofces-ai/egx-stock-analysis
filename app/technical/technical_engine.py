from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.data.market_data import get_ohlcv
from app.models import TechnicalSignal
from app.technical.candlestick_patterns import detect_patterns
from app.technical.indicators import add_indicators
from app.technical.support_resistance import breakout_state, support_resistance


def _bound(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _risk_level(score: float, reward_risk: float | None) -> str:
    if reward_risk is not None and reward_risk >= 2 and score >= 70:
        return "LOW"
    if score >= 55:
        return "MEDIUM"
    return "HIGH"


def _neutral_missing(symbol: str, reason: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "signal": "HOLD",
        "technical_score": 50.0,
        "entry_price": None,
        "stop_loss": None,
        "take_profit_1": None,
        "take_profit_2": None,
        "confidence": 0.0,
        "reason": reason,
        "risk_level": "HIGH",
        "details": {"status": "missing_data"},
    }


def analyze_technical(
    db: Session,
    symbol: str,
    *,
    timeframe: str = "1D",
    persist: bool = True,
) -> dict[str, Any]:
    df = get_ohlcv(db, symbol, timeframe=timeframe, limit=260)
    if df.empty or len(df) < 30:
        result = _neutral_missing(symbol, "No sufficient OHLCV candles. Run TradingView/data import first.")
        if persist:
            db.add(
                TechnicalSignal(
                    symbol=symbol,
                    signal=result["signal"],
                    technical_score=result["technical_score"],
                    confidence=result["confidence"],
                    reason=result["reason"],
                    risk_level=result["risk_level"],
                    details_json=result["details"],
                )
            )
        return result

    enriched = add_indicators(df)
    last = enriched.iloc[-1]
    prev = enriched.iloc[-2]
    sr = support_resistance(enriched)
    breakout = breakout_state(enriched)
    patterns = detect_patterns(enriched)

    trend_score = 0.0
    if last["close"] > last["sma20"]:
        trend_score += 8
    if last["close"] > last["sma50"]:
        trend_score += 8
    if pd.notna(last.get("sma200")) and last["close"] > last["sma200"]:
        trend_score += 5
    if last["ema20"] > last["ema50"]:
        trend_score += 4
    trend_score = min(25, trend_score)

    momentum_score = 0.0
    if last["rsi14"] >= 55:
        momentum_score += 8
    elif last["rsi14"] <= 35:
        momentum_score -= 5
    if last["macd_hist"] > 0:
        momentum_score += 6
    if last["macd_hist"] > prev["macd_hist"]:
        momentum_score += 4
    if last["adx"] >= 20 and last["plus_di"] >= last["minus_di"]:
        momentum_score += 2
    momentum_score = _bound(momentum_score, 0, 20)

    volume_score = 0.0
    volume_ma = float(last.get("volume_ma20") or 0)
    if volume_ma and last["volume"] >= volume_ma:
        volume_score += 8
    if volume_ma and last["volume"] >= volume_ma * 1.5 and last["close"] > last["open"]:
        volume_score += 10
    if last["close"] > prev["close"]:
        volume_score += 2
    volume_score = min(20, volume_score)

    sr_score = 10.0
    if breakout == "BULLISH_BREAKOUT":
        sr_score = 20.0
    elif breakout == "BEARISH_BREAKDOWN":
        sr_score = 0.0
    elif sr["support"] and last["close"] >= sr["support"] * 1.03:
        sr_score += 4
    if "bullish_engulfing" in patterns or "hammer" in patterns:
        sr_score += 3
    if "bearish_engulfing" in patterns:
        sr_score -= 5
    sr_score = _bound(sr_score, 0, 20)

    entry = float(last["close"])
    atr = float(last.get("atr14") or 0)
    stop_loss = sr["support"] if sr["support"] and sr["support"] < entry else (entry - max(atr * 1.5, entry * 0.03))
    risk = max(entry - float(stop_loss), 0.0001)
    take_profit_1 = entry + risk * 1.5
    take_profit_2 = entry + risk * 2.5
    reward_risk = (take_profit_1 - entry) / risk if risk else None
    risk_reward_score = 15.0 if reward_risk and reward_risk >= 1.5 else 8.0

    total = _bound(trend_score + momentum_score + volume_score + sr_score + risk_reward_score)
    signal = "BUY" if total >= 70 else "SELL" if total < 40 else "HOLD"
    confidence = _bound(total if signal != "HOLD" else abs(total - 50) * 2)
    reason = (
        f"Trend {trend_score:.0f}/25, momentum {momentum_score:.0f}/20, volume {volume_score:.0f}/20, "
        f"support/resistance {sr_score:.0f}/20, risk/reward {risk_reward_score:.0f}/15. "
        f"Breakout: {breakout}. Patterns: {', '.join(patterns) if patterns else 'none'}."
    )
    result = {
        "symbol": symbol,
        "signal": signal,
        "technical_score": total,
        "entry_price": round(entry, 4),
        "stop_loss": round(float(stop_loss), 4),
        "take_profit_1": round(float(take_profit_1), 4),
        "take_profit_2": round(float(take_profit_2), 4),
        "confidence": confidence,
        "reason": reason,
        "risk_level": _risk_level(total, reward_risk),
        "details": {
            "timeframe": timeframe,
            "trend_score": trend_score,
            "momentum_score": momentum_score,
            "volume_score": volume_score,
            "support_resistance_score": sr_score,
            "risk_reward_score": risk_reward_score,
            "support": sr["support"],
            "resistance": sr["resistance"],
            "patterns": patterns,
            "as_of": datetime.utcnow().isoformat(),
        },
    }
    if persist:
        db.add(
            TechnicalSignal(
                symbol=symbol,
                signal=signal,
                technical_score=total,
                entry_price=result["entry_price"],
                stop_loss=result["stop_loss"],
                take_profit_1=result["take_profit_1"],
                take_profit_2=result["take_profit_2"],
                confidence=confidence,
                reason=reason,
                risk_level=result["risk_level"],
                details_json=result["details"],
            )
        )
    return result

