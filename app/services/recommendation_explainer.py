"""Recommendation Explanation & Enhanced Risk Management Module

Provides human-readable explanations for every recommendation.
Validates risk/reward, liquidity, market regime, and data freshness.
"""
from __future__ import annotations

from typing import Any

from app.config import DISCLAIMER


def generate_recommendation_explanation(
    symbol: str,
    signal: str,
    final_score: float,
    components: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a detailed explanation for a recommendation."""
    components = components or {}
    scores = components.get("components") or {}
    screener = components.get("screener") or {}
    cli_strategy = components.get("cli_v6_strategy") or {}
    strategy = components.get("strategy") or {}
    backtests = components.get("backtests") or []
    risks = components.get("risks") or []
    combined_decision = components.get("combined_decision", "")

    technical_score = scores.get("tradingview") or scores.get("system_recommendation", 50)
    telegram_score = scores.get("telegram", 50)
    strategy_score = scores.get("cli_v6_strategy", 50)
    backtest_score = scores.get("backtest", 50)
    risk_score_value = components.get("risk_score", 50)
    freshness_score = components.get("freshness_score", 50)

    explanations = {
        "why_buy": [],
        "why_avoid": [],
        "why_wait": [],
        "main_risks": [],
        "confidence_factors": [],
        "data_quality_notes": [],
    }

    if signal in ("BUY", "STRONG BUY"):
        if technical_score and float(technical_score) >= 70:
            explanations["why_buy"].append(f"Technical analysis score is strong ({technical_score:.0f}%)")
        if telegram_score and float(telegram_score) >= 60:
            explanations["why_buy"].append(f"Telegram consensus is positive ({telegram_score:.0f}%)")
        if strategy_score and float(strategy_score) >= 65:
            explanations["why_buy"].append(f"CLI v6 strategy confirms ({strategy_score:.0f}%)")
        if backtest_score and float(backtest_score) >= 60:
            explanations["why_buy"].append(f"Backtest performance supports ({backtest_score:.0f}%)")
        if not explanations["why_buy"]:
            explanations["why_buy"].append("Overall score reached BUY threshold")

    elif signal in ("AVOID", "SELL", "HIGH_RISK"):
        if technical_score and float(technical_score) < 45:
            explanations["why_avoid"].append(f"Technical analysis is weak ({technical_score:.0f}%)")
        if telegram_score and float(telegram_score) < 40:
            explanations["why_avoid"].append(f"Telegram sentiment is negative ({telegram_score:.0f}%)")
        if strategy_score and float(strategy_score) < 40:
            explanations["why_avoid"].append(f"Strategy recommends against ({strategy_score:.0f}%)")
        if combined_decision in ("SELL", "AVOID", "HIGH_RISK"):
            explanations["why_avoid"].append(f"Combined decision is {combined_decision}")
        if not explanations["why_avoid"]:
            explanations["why_avoid"].append("Score below minimum threshold")

    elif signal in ("WATCH", "NEUTRAL", "HOLD"):
        if final_score >= 70 and signal != "BUY":
            explanations["why_wait"].append("Score is high but missing confirmation from key sources")
        elif final_score >= 60:
            explanations["why_wait"].append("Moderate score - waiting for stronger signals")
        else:
            explanations["why_wait"].append("Score is below entry threshold")

        if technical_score and float(technical_score) >= 70:
            explanations["why_wait"].append("Technical setup is promising but needs Telegram confirmation")
        if telegram_score and float(telegram_score) >= 60:
            explanations["why_wait"].append("Telegram interest exists but technicals not aligned")

    # Risk analysis
    if isinstance(risk_score_value, (int, float)) and risk_score_value < 60:
        explanations["main_risks"].append(f"Risk score is low ({risk_score_value:.0f}%)")
    if screener.get("warnings"):
        for w in screener["warnings"]:
            if "overbought" in str(w).lower():
                explanations["main_risks"].append("RSI overbought - chase risk")
            elif "hype" in str(w).lower():
                explanations["main_risks"].append("Telegram hype detected - potential fake signal")
            elif "missing stop" in str(w).lower():
                explanations["main_risks"].append("Missing stop loss in Telegram signals")
    for r in risks:
        explanations["main_risks"].append(f"Risk: {r}")
    if strategy.get("data_quality") == "unavailable":
        explanations["data_quality_notes"].append("Strategy candles unavailable")
    if screener.get("last_price") is None:
        explanations["data_quality_notes"].append("No live price data available")

    # Confidence factors
    if final_score >= 80:
        explanations["confidence_factors"].append("Very high overall score")
    elif final_score >= 70:
        explanations["confidence_factors"].append("Good overall score")
    if isinstance(technical_score, (int, float)) and technical_score >= 75:
        explanations["confidence_factors"].append("Strong technical alignment")
    if isinstance(telegram_score, (int, float)) and telegram_score >= 65:
        explanations["confidence_factors"].append("Positive Telegram community sentiment")
    if backtest_score and float(backtest_score) >= 65:
        explanations["confidence_factors"].append("Historical backtest supports the signal")

    return explanations


def format_recommendation_explanation(
    symbol: str,
    signal: str,
    final_score: float,
    entry_price: float | None,
    stop_loss: float | None,
    target_price: float | None,
    explanation: dict[str, Any] | None = None,
    components: dict[str, Any] | None = None,
) -> str:
    """Format the recommendation with explanation for display."""
    if explanation is None:
        explanation = generate_recommendation_explanation(symbol, signal, final_score, components)

    lines = [f"EGX Recommendation: {symbol}", f"Signal: {signal} | Score: {final_score:.0f}%", ""]

    if entry_price:
        lines.append(f"Entry Zone: {entry_price:.2f}")
    if stop_loss:
        lines.append(f"Stop Loss: {stop_loss:.2f}")
    if target_price:
        lines.append(f"Target: {target_price:.2f}")
        risk = abs(entry_price - stop_loss) if entry_price and stop_loss else 0
        reward = abs(target_price - entry_price) if entry_price else 0
        if risk > 0 and reward > 0:
            rr = reward / risk
            lines.append(f"Risk/Reward: 1:{rr:.1f}")
    lines.append("")

    if explanation["why_buy"]:
        lines.append("Why BUY:")
        for item in explanation["why_buy"]:
            lines.append(f"  + {item}")
        lines.append("")
    if explanation["why_avoid"]:
        lines.append("Why AVOID:")
        for item in explanation["why_avoid"]:
            lines.append(f"  - {item}")
        lines.append("")
    if explanation["why_wait"]:
        lines.append("Why WAIT:")
        for item in explanation["why_wait"]:
            lines.append(f"  ~ {item}")
        lines.append("")
    if explanation["main_risks"]:
        lines.append("Main Risks:")
        for item in explanation["main_risks"]:
            lines.append(f"  ! {item}")
        lines.append("")
    if explanation["confidence_factors"]:
        lines.append("Confidence Factors:")
        for item in explanation["confidence_factors"]:
            lines.append(f"  * {item}")
        lines.append("")
    if explanation["data_quality_notes"]:
        lines.append("Data Quality Notes:")
        for item in explanation["data_quality_notes"]:
            lines.append(f"  ? {item}")
        lines.append("")

    lines.append(f"Disclaimer: {DISCLAIMER}")
    return "\n".join(lines)


def enhanced_validate_recommendation(
    signal: str,
    final_score: float,
    entry_price: float | None,
    stop_loss: float | None,
    target_price: float | None,
    current_price: float | None = None,
    telegram_score: float = 50.0,
    technical_score: float = 50.0,
    strategy_score: float = 50.0,
    backtest_score: float = 50.0,
    risk_liquidity_score: float = 50.0,
    news_score: float = 50.0,
    market_regime: str | None = None,
) -> dict[str, Any]:
    """Enhanced validation with better risk management."""
    issues = []
    warnings_list = []

    # Score-based validation
    if signal in ("BUY", "STRONG BUY"):
        if final_score < 70:
            issues.append(f"Final score {final_score:.0f}% is below 70% BUY threshold")
        if technical_score < 60:
            warnings_list.append(f"Technical score {technical_score:.0f}% is weak for BUY")
        if strategy_score < 55:
            warnings_list.append(f"Strategy score {strategy_score:.0f}% is weak for BUY")
        if risk_liquidity_score < 50:
            issues.append(f"Risk/liquidity score {risk_liquidity_score:.0f}% is too low")
        if telegram_score < 40 and news_score < 40:
            warnings_list.append("Both Telegram and news sentiment are negative")

    # Price-based validation
    if entry_price and current_price:
        distance_pct = abs(current_price - entry_price) / entry_price * 100
        if distance_pct > 5:
            issues.append(f"Current price ({current_price:.2f}) is {distance_pct:.1f}% away from entry ({entry_price:.2f})")
        if current_price > entry_price and signal == "BUY":
            warnings_list.append(f"Price is above entry zone - consider waiting for pullback")

    # Stop loss validation
    if entry_price and stop_loss:
        sl_distance_pct = abs(entry_price - stop_loss) / entry_price * 100
        if sl_distance_pct < 1:
            issues.append(f"Stop loss ({stop_loss:.2f}) is too tight ({sl_distance_pct:.1f}%)")
        elif sl_distance_pct > 15:
            issues.append(f"Stop loss ({stop_loss:.2f}) is too wide ({sl_distance_pct:.1f}%)")

    # Risk/Reward validation
    if entry_price and stop_loss and target_price and entry_price != stop_loss:
        risk_amount = abs(entry_price - stop_loss)
        reward_amount = abs(target_price - entry_price)
        if risk_amount > 0:
            rr = reward_amount / risk_amount
            if rr < 1.5:
                issues.append(f"Risk/reward ratio 1:{rr:.1f} is below 1:1.5 minimum")

    # Market regime validation
    if market_regime == "bearish" and signal in ("BUY", "STRONG BUY"):
        issues.append("Market regime is bearish - avoid new BUY positions")
    elif market_regime == "high_volatility" and signal in ("BUY", "STRONG BUY"):
        warnings_list.append("High market volatility - reduce position size")

    # Liquidity validation
    if risk_liquidity_score < 40:
        issues.append("Liquidity is too low - risk of slippage")

    validated_signal = signal
    if issues:
        if signal in ("BUY", "STRONG BUY"):
            validated_signal = "WATCH"
    if len(issues) >= 3:
        validated_signal = "AVOID"

    return {
        "signal": validated_signal,
        "final_score": final_score,
        "issues": issues,
        "warnings": warnings_list,
        "is_valid": len(issues) == 0,
        "has_warnings": len(warnings_list) > 0,
    }
