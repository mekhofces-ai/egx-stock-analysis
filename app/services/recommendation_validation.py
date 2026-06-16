from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import (
    MAX_DISTANCE_FROM_ENTRY_PCT,
    MAX_POSITION_RISK_PCT,
    MIN_BACKTEST_SCORE_TO_BUY,
    MIN_RISK_LIQUIDITY_SCORE_TO_BUY,
    MIN_RISK_REWARD_TO_BUY,
    MIN_STRATEGY_SCORE_TO_BUY,
    MIN_TECHNICAL_SCORE_TO_BUY,
)


DIRECT_BUY_SIGNALS = {"BUY", "STRONG BUY"}
CONDITIONAL_BUY = "CONDITIONAL BUY"
WATCH_ONLY = "WATCH ONLY"
WAIT_FOR_PULLBACK = "WAIT FOR PULLBACK"
AVOID = "AVOID"


@dataclass(frozen=True)
class RecommendationValidation:
    original_signal: str
    signal: str
    grade: str
    passed: bool
    entry_zone_valid: bool
    stop_loss_valid: bool
    risk_reward_valid: bool
    position_size: int | None
    risk_amount: float | None
    risk_per_share: float | None
    no_trade_reasons: list[str]
    conditions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_signal": self.original_signal,
            "signal": self.signal,
            "grade": self.grade,
            "passed": self.passed,
            "entry_zone_valid": self.entry_zone_valid,
            "stop_loss_valid": self.stop_loss_valid,
            "risk_reward_valid": self.risk_reward_valid,
            "position_size": self.position_size,
            "risk_amount": self.risk_amount,
            "risk_per_share": self.risk_per_share,
            "no_trade_reasons": self.no_trade_reasons,
            "conditions": self.conditions,
        }


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def entry_zone_valid(entry_low: Any, entry_high: Any) -> bool:
    low = safe_float(entry_low)
    high = safe_float(entry_high)
    return low is not None and high is not None and low > 0 and high > 0 and low <= high


def stop_loss_valid(entry_low: Any, stop_loss: Any) -> bool:
    low = safe_float(entry_low)
    stop = safe_float(stop_loss)
    return low is not None and stop is not None and stop > 0 and stop < low


def calculate_position_size(
    *,
    portfolio_value: float | None,
    entry_price: float | None,
    stop_loss: float | None,
    max_risk_pct: float = MAX_POSITION_RISK_PCT,
) -> dict[str, Any]:
    if portfolio_value is None or entry_price is None or stop_loss is None:
        return {"quantity": None, "risk_amount": None, "risk_per_share": None, "valid": False}
    risk_per_share = float(entry_price) - float(stop_loss)
    if portfolio_value <= 0 or entry_price <= 0 or risk_per_share <= 0:
        return {"quantity": 0, "risk_amount": 0.0, "risk_per_share": risk_per_share, "valid": False}
    risk_amount = float(portfolio_value) * float(max_risk_pct)
    quantity = int(max(0, risk_amount // risk_per_share))
    return {
        "quantity": quantity,
        "risk_amount": round(risk_amount, 2),
        "risk_per_share": round(risk_per_share, 4),
        "valid": quantity > 0,
    }


def _grade(final_score: float, hard_gates_passed: bool, risk_reward: float | None) -> str:
    if hard_gates_passed and final_score >= 85 and (risk_reward or 0) >= 2.5:
        return "A+"
    if hard_gates_passed and final_score >= 70:
        return "A"
    if final_score >= 60:
        return "B"
    if final_score >= 45:
        return "C"
    return "D"


def validate_recommendation(
    row: dict[str, Any],
    *,
    current_price: float | None = None,
    portfolio_value: float | None = None,
    min_technical_score: float = MIN_TECHNICAL_SCORE_TO_BUY,
    min_strategy_score: float = MIN_STRATEGY_SCORE_TO_BUY,
    min_backtest_score: float = MIN_BACKTEST_SCORE_TO_BUY,
    min_risk_liquidity_score: float = MIN_RISK_LIQUIDITY_SCORE_TO_BUY,
    min_risk_reward: float = MIN_RISK_REWARD_TO_BUY,
    max_distance_from_entry_pct: float = MAX_DISTANCE_FROM_ENTRY_PCT,
) -> RecommendationValidation:
    final_score = safe_float(row.get("final_score"), 0.0) or 0.0
    telegram_score = safe_float(row.get("telegram_score"), 50.0) or 50.0
    technical_score = safe_float(row.get("technical_score"), 50.0) or 50.0
    strategy_score = safe_float(row.get("strategy_score"), 50.0) or 50.0
    news_score = safe_float(row.get("news_score"), 50.0) or 50.0
    backtest_score = safe_float(row.get("backtest_score"), 50.0) or 50.0
    risk_liquidity_score = safe_float(row.get("risk_liquidity_score"), 50.0) or 50.0
    risk_reward = safe_float(row.get("risk_reward"))
    entry_low = safe_float(row.get("entry_zone_low"))
    entry_high = safe_float(row.get("entry_zone_high"))
    stop_loss = safe_float(row.get("stop_loss"))
    original_signal = str(row.get("signal") or "").upper().strip() or "WATCH ONLY"

    valid_entry = entry_zone_valid(entry_low, entry_high)
    valid_stop = stop_loss_valid(entry_low, stop_loss)
    valid_rr = risk_reward is not None and risk_reward >= min_risk_reward
    no_trade_reasons: list[str] = []

    if technical_score < min_technical_score:
        no_trade_reasons.append(f"technical score {technical_score:.0f} is below {min_technical_score:.0f}")
    if strategy_score < min_strategy_score:
        no_trade_reasons.append(f"strategy score {strategy_score:.0f} is below {min_strategy_score:.0f}")
    if backtest_score < min_backtest_score:
        no_trade_reasons.append(f"backtest score {backtest_score:.0f} is below {min_backtest_score:.0f}")
    if risk_liquidity_score < min_risk_liquidity_score:
        no_trade_reasons.append(f"liquidity/risk score {risk_liquidity_score:.0f} is below {min_risk_liquidity_score:.0f}")
    if not valid_entry:
        no_trade_reasons.append("entry zone is invalid or missing")
    if not valid_stop:
        no_trade_reasons.append("stop loss is invalid or missing")
    if not valid_rr:
        no_trade_reasons.append(f"risk/reward is below {min_risk_reward:.1f}")
    if news_score < 40:
        no_trade_reasons.append("news score is negative")
    if telegram_score >= 75 and technical_score < min_technical_score:
        no_trade_reasons.append("Telegram attention is high but technical confirmation is weak")

    if current_price is not None and entry_high:
        distance = (float(current_price) - entry_high) / entry_high
        if distance > max_distance_from_entry_pct:
            no_trade_reasons.append(
                f"late signal: current price is {distance * 100:.1f}% above entry zone"
            )

    hard_gates_passed = (
        technical_score >= min_technical_score
        and strategy_score >= min_strategy_score
        and backtest_score >= min_backtest_score
        and risk_liquidity_score >= min_risk_liquidity_score
        and valid_entry
        and valid_stop
        and valid_rr
    )
    grade = _grade(final_score, hard_gates_passed, risk_reward)

    position = calculate_position_size(
        portfolio_value=portfolio_value,
        entry_price=entry_high or entry_low,
        stop_loss=stop_loss,
    )

    if risk_liquidity_score < min_risk_liquidity_score:
        signal = AVOID
    elif current_price is not None and entry_high and (float(current_price) - entry_high) / entry_high > max_distance_from_entry_pct:
        signal = WAIT_FOR_PULLBACK
    elif news_score < 40 and final_score < 80:
        signal = WATCH_ONLY if final_score >= 55 else AVOID
    elif telegram_score >= 75 and technical_score < min_technical_score:
        signal = WATCH_ONLY
    elif hard_gates_passed and final_score >= 70 and grade in {"A", "A+"}:
        signal = CONDITIONAL_BUY
    elif final_score >= 55:
        signal = WATCH_ONLY
    else:
        signal = AVOID

    if signal == CONDITIONAL_BUY and no_trade_reasons:
        signal = WATCH_ONLY

    conditions = [
        "Price reaches the entry zone before entry.",
        "Volume confirms the move.",
        "Market condition is not bearish/high-volatility.",
        "Stop loss is accepted before entry.",
        "Risk per trade remains at or below 1%.",
    ]
    if signal != CONDITIONAL_BUY and not no_trade_reasons:
        no_trade_reasons.append("setup does not meet conditional BUY grade")

    return RecommendationValidation(
        original_signal=original_signal,
        signal=signal,
        grade=grade,
        passed=signal == CONDITIONAL_BUY,
        entry_zone_valid=valid_entry,
        stop_loss_valid=valid_stop,
        risk_reward_valid=valid_rr,
        position_size=position["quantity"],
        risk_amount=position["risk_amount"],
        risk_per_share=position["risk_per_share"],
        no_trade_reasons=no_trade_reasons,
        conditions=conditions,
    )


def apply_validation_to_row(
    row: dict[str, Any],
    *,
    current_price: float | None = None,
    portfolio_value: float | None = None,
) -> dict[str, Any]:
    validation = validate_recommendation(row, current_price=current_price, portfolio_value=portfolio_value)
    updated = dict(row)
    details = dict(updated.get("details") or {})
    details["validation"] = validation.to_dict()
    updated["signal"] = validation.signal
    updated["signal_grade"] = validation.grade
    updated["validation"] = validation.to_dict()
    updated["details"] = details
    if validation.no_trade_reasons:
        reason = "; ".join(validation.no_trade_reasons)
        explanation = str(updated.get("explanation") or "").rstrip()
        updated["explanation"] = f"{explanation}\nValidation: {reason}" if explanation else f"Validation: {reason}"
    return updated
