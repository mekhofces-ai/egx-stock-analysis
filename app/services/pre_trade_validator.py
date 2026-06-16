from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.data.market_data import get_ohlcv, latest_price
from app.intelligence.risk_quality import analyze_market_regime as _analyze_regime
from app.models import (
    FinalStockDecision,
    LiquiditySnapshot,
    MarketRegimeSnapshot,
    NotificationLog,
    Opportunity,
    Stock,
    TradeJournal,
)
from app.services.recommendation_explainer import generate_recommendation_explanation as explain_recommendation

logger = logging.getLogger(__name__)
CAIRO_TZ = ZoneInfo("Africa/Cairo")


class RecommendationStage(str, Enum):
    WATCH = "WATCH"
    NEAR_ENTRY = "NEAR ENTRY"
    ENTRY_CONFIRMED = "ENTRY CONFIRMED"
    BUY = "BUY"
    STRONG_BUY = "STRONG BUY"
    AVOID = "AVOID"


ACTION_STAGES = {RecommendationStage.ENTRY_CONFIRMED, RecommendationStage.BUY, RecommendationStage.STRONG_BUY}


@dataclass
class PreTradeResult:
    stage: RecommendationStage
    passed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    market_regime: str = "unknown"
    market_regime_impact: str = ""
    confidence_score: float = 0.0
    entry_validation: dict[str, bool] = field(default_factory=dict)
    explanation: str = ""
    final_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "passed": self.passed,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "risk_factors": self.risk_factors,
            "market_regime": self.market_regime,
            "market_regime_impact": self.market_regime_impact,
            "confidence_score": self.confidence_score,
            "entry_validation": self.entry_validation,
            "explanation": self.explanation,
            "final_action": self.final_action,
        }


# ---------------------------------------------------------------------------
# 1. Market Condition Engine
# ---------------------------------------------------------------------------

def analyze_market_condition(db: Session) -> dict[str, Any]:
    regime_data = _analyze_regime(db, persist=True)
    regime = regime_data.get("regime", "unknown")
    trend_score = regime_data.get("trend_score", 50.0)
    volatility_score_val = regime_data.get("volatility_score")
    market_score = regime_data.get("market_score", 50.0)
    reason = regime_data.get("reason", "")
    liquidity_ok = _check_market_liquidity(db)

    sideway_threshold = 15
    if trend_score is not None:
        sideways = abs(trend_score - 50) <= sideway_threshold
    else:
        sideways = False
    if sideways and "high_volatility" not in regime and "bullish" not in regime and "bearish" not in regime:
        regime = "sideways"

    return {
        "regime": regime,
        "trend_score": trend_score,
        "volatility_score": volatility_score_val,
        "market_score": market_score,
        "reason": reason,
        "liquidity_ok": liquidity_ok,
        "sideways": sideways,
    }


def _check_market_liquidity(db: Session) -> bool:
    count = db.query(func.count(LiquiditySnapshot.id)).filter(
        LiquiditySnapshot.status.in_(["low", "critical"])
    ).scalar() or 0
    return count < 10


def market_regime_downgrade(regime: str, original_stage: RecommendationStage,
                            final_score: float) -> tuple[RecommendationStage, str]:
    if regime == "bearish":
        if original_stage in (RecommendationStage.BUY, RecommendationStage.ENTRY_CONFIRMED):
            if final_score >= 80:
                return RecommendationStage.BUY, "Bearish market: maintained BUY due to very strong score (>=80)"
            elif final_score >= 70:
                return RecommendationStage.NEAR_ENTRY, "Bearish market: downgraded BUY to NEAR ENTRY (score 70-80)"
            else:
                return RecommendationStage.WATCH, "Bearish market: downgraded to WATCH (score <70)"
        elif original_stage == RecommendationStage.STRONG_BUY:
            if final_score >= 90:
                return RecommendationStage.STRONG_BUY, "Bearish market: maintained STRONG BUY (score >=90)"
            else:
                return RecommendationStage.BUY, "Bearish market: downgraded STRONG BUY to BUY (score <90)"
    elif regime == "high_volatility":
        if original_stage in (RecommendationStage.BUY, RecommendationStage.STRONG_BUY, RecommendationStage.ENTRY_CONFIRMED):
            if final_score >= 85:
                return RecommendationStage.ENTRY_CONFIRMED, "High volatility: downgraded to ENTRY CONFIRMED (score >=85)"
            else:
                return RecommendationStage.WATCH, "High volatility: downgraded to WATCH (score <85)"
    elif regime == "sideways":
        if original_stage == RecommendationStage.STRONG_BUY:
            return RecommendationStage.BUY, "Sideways market: STRONG BUY downgraded to BUY"
    return original_stage, ""


# ---------------------------------------------------------------------------
# 2. No Trade Filter
# ---------------------------------------------------------------------------

@dataclass
class NoTradeFilterResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    entry_validation: dict[str, bool] = field(default_factory=dict)


def check_no_trade_filters(db: Session, symbol: str, row: dict[str, Any],
                           current_price: float | None = None) -> NoTradeFilterResult:
    result = NoTradeFilterResult(passed=True)
    entry = _float(row, "entry_price") or _float(row, "entry_zone_low") or 0
    target = _float(row, "target_price") or _float(row, "target_1") or 0
    stop = _float(row, "stop_loss") or 0
    score = _float(row, "final_score") or _float(row, "confidence") or 0
    price = current_price or latest_price(db, symbol)
    rec = _str(row, "recommendation") or _str(row, "signal") or ""

    result.entry_validation["has_entry"] = entry > 0
    result.entry_validation["has_target"] = target > 0
    result.entry_validation["has_stop"] = stop > 0
    result.entry_validation["price_not_too_far"] = True
    result.entry_validation["risk_reward_ok"] = False
    result.entry_validation["stop_realistic"] = True
    result.entry_validation["target_realistic"] = True
    result.entry_validation["liquidity_ok"] = True
    result.entry_validation["technical_confirmed"] = True
    result.entry_validation["news_not_negative"] = True
    result.entry_validation["telegram_not_sole_reason"] = True

    if not result.entry_validation["has_entry"]:
        result.reasons.append("No entry price available")
    if not result.entry_validation["has_target"]:
        result.reasons.append("No target price available")
    if not result.entry_validation["has_stop"]:
        result.reasons.append("No stop loss available")

    if entry and price and price > 0:
        pct_from_entry = abs(price - entry) / entry * 100
        if pct_from_entry > 5:
            result.entry_validation["price_not_too_far"] = False
            result.reasons.append(f"Price {price:.2f} is {pct_from_entry:.1f}% from entry {entry:.2f} (max 5%)")

    if entry and stop and entry > 0 and stop > 0:
        risk_per_share = abs(entry - stop)
        result.entry_validation["stop_realistic"] = risk_per_share / entry < 0.15
        if not result.entry_validation["stop_realistic"]:
            result.reasons.append(f"Stop loss {stop:.2f} is too wide ({(risk_per_share/entry*100):.1f}% risk)")

    if entry and target and entry > 0 and target > 0:
        reward = abs(target - entry)
        risk = abs(entry - stop) if stop and stop > 0 else reward * 0.5
        rr = reward / risk if risk > 0 else 0
        result.entry_validation["risk_reward_ok"] = rr >= 2.0
        if not result.entry_validation["risk_reward_ok"]:
            result.reasons.append(f"Risk/reward ratio {rr:.1f}:1 is below minimum 1:2")

    if target and entry and target > 0 and entry > 0:
        target_pct = abs(target - entry) / entry * 100
        result.entry_validation["target_realistic"] = target_pct <= 50
        if not result.entry_validation["target_realistic"]:
            result.reasons.append(f"Target {target:.2f} is {target_pct:.0f}% from entry (unrealistic)")

    liquidity = db.scalar(
        select(LiquiditySnapshot).where(LiquiditySnapshot.symbol == symbol)
        .order_by(LiquiditySnapshot.created_at.desc())
    )
    if liquidity:
        liq_ok = liquidity.status not in ("low", "critical")
        result.entry_validation["liquidity_ok"] = liq_ok
        if not liq_ok:
            result.reasons.append(f"Liquidity {liquidity.status} ({liquidity.liquidity_score:.0f})")

    # Technical confirmation
    comp = _dict(row, "components_json") or {}
    tech = _dict(comp, "technical_score") or {}
    tech_score = _float(tech, "score") or _float(tech, "technical_score") or 0
    result.entry_validation["technical_confirmed"] = tech_score >= 60
    if not result.entry_validation["technical_confirmed"]:
        result.reasons.append(f"Technical score {tech_score:.0f} below 60")

    news_val = _float(comp, "news_score") or 50
    result.entry_validation["news_not_negative"] = news_val >= 40
    if not result.entry_validation["news_not_negative"]:
        result.reasons.append(f"Negative news score {news_val:.0f}")

    telegram_val = _float(comp, "telegram_score") or 0
    if telegram_val >= 70 and tech_score < 50:
        result.entry_validation["telegram_not_sole_reason"] = False
        result.reasons.append("Telegram hype is the only reason for BUY (technical weak)")

    all_passed = all(result.entry_validation.values())
    result.passed = all_passed
    if all_passed and not result.reasons:
        result.reasons.append("All validation checks passed")
    return result


# ---------------------------------------------------------------------------
# 3. Recommendation Stage Classification
# ---------------------------------------------------------------------------

def classify_stage(row: dict[str, Any], market_condition: dict[str, Any],
                   filter_result: NoTradeFilterResult) -> PreTradeResult:
    score = _float(row, "final_score") or _float(row, "confidence") or 0
    rec = _str(row, "recommendation") or _str(row, "signal") or ""
    entry = _float(row, "entry_price") or _float(row, "entry_zone_low") or 0
    target = _float(row, "target_price") or _float(row, "target_1") or 0
    stop = _float(row, "stop_loss") or 0
    regime = market_condition.get("regime", "unknown")

    result = PreTradeResult(
        stage=RecommendationStage.AVOID,
        passed=False,
        market_regime=regime,
        confidence_score=score,
        entry_validation=filter_result.entry_validation,
    )

    if not filter_result.passed:
        result.reasons = filter_result.reasons
        result.final_action = "BLOCKED: No-trade filter failed"
        result.explanation = _build_explanation(row, result)
        return result

    is_strong_buy = rec in ("STRONG BUY", "STRONG_BUY") or score >= 85
    is_buy = rec in ("BUY", "CONDITIONAL BUY") or score >= 70
    is_watch = rec in ("WATCH", "WATCH ONLY", "NEUTRAL") or score >= 55

    if score < 55 or rec in ("AVOID", "SELL", "HIGH_RISK"):
        result.stage = RecommendationStage.AVOID
        result.passed = False
        result.reasons = [f"Score {score:.0f} below minimum threshold"]
        result.final_action = "AVOID"
    elif is_strong_buy:
        result.stage = RecommendationStage.STRONG_BUY
        result.passed = True
    elif is_buy:
        result.stage = RecommendationStage.BUY
        result.passed = True
    elif is_watch and entry > 0:
        result.stage = RecommendationStage.NEAR_ENTRY
        result.passed = True
        result.reasons = ["Score moderate but entry zone defined"]
    elif is_watch:
        result.stage = RecommendationStage.WATCH
        result.passed = True
        result.reasons = ["Monitor for improvement"]
    else:
        result.stage = RecommendationStage.AVOID
        result.passed = False
        result.reasons = [f"Score {score:.0f} insufficient for any positive stage"]

    stage_after, impact = market_regime_downgrade(regime, result.stage, score)
    if stage_after != result.stage:
        result.market_regime_impact = impact
        result.warnings.append(impact)
        result.stage = stage_after
        if stage_after in (RecommendationStage.WATCH, RecommendationStage.AVOID) and result.passed:
            result.passed = False
            result.reasons.append(impact)

    if result.stage in ACTION_STAGES and result.passed:
        result.final_action = f"ACTION REQUIRED: {result.stage.value}"
    elif result.stage == RecommendationStage.NEAR_ENTRY:
        result.final_action = "MONITOR: Price near entry; wait for confirmation"
    elif result.stage == RecommendationStage.WATCH:
        result.final_action = "WATCHLIST: No action; monitor for change"
    else:
        result.final_action = f"AVOID: {', '.join(result.reasons[:2])}"

    result.risk_factors = _assess_risk_factors(row, market_condition)
    result.explanation = _build_explanation(row, result)
    return result


def _build_explanation(row: dict[str, Any], result: PreTradeResult) -> str:
    try:
        symbol = _str(row, "symbol") or "?"
        score = result.confidence_score or _float(row, "final_score") or 0
        rec = result.stage.value
        expl = explain_recommendation(symbol, rec, score, row.get("components_json") or {})
        parts = []
        if expl.get("why_buy"):
            parts.append(f"Buy: {'; '.join(expl['why_buy'][:3])}")
        if expl.get("why_avoid"):
            parts.append(f"Avoid: {'; '.join(expl['why_avoid'][:3])}")
        if expl.get("why_wait"):
            parts.append(f"Wait: {'; '.join(expl['why_wait'][:3])}")
        if expl.get("main_risks"):
            parts.append(f"Risks: {'; '.join(expl['main_risks'][:3])}")
        if expl.get("confidence_factors"):
            parts.append(f"Confidence: {'; '.join(expl['confidence_factors'][:3])}")
        return " | ".join(parts) if parts else f"{symbol}: Score {score:.0f}, Stage {rec}"
    except Exception:
        symbol = _str(row, "symbol") or "?"
        score = _float(row, "final_score") or _float(row, "confidence") or 0
        parts = [
            f"{symbol} | Score: {score:.0f} | Stage: {result.stage.value}",
            f"Market: {result.market_regime}",
        ]
        if result.reasons:
            parts.append(f"Reason: {'; '.join(result.reasons[:3])}")
        if result.warnings:
            parts.append(f"Warning: {'; '.join(result.warnings[:2])}")
        if result.risk_factors:
            parts.append(f"Risk: {'; '.join(result.risk_factors[:3])}")
        parts.append(f"Action: {result.final_action}")
        return " | ".join(parts)


def _assess_risk_factors(row: dict[str, Any], market: dict[str, Any]) -> list[str]:
    risks = []
    regime = market.get("regime", "")
    if regime == "bearish":
        risks.append("Market in bearish territory")
    if regime == "high_volatility":
        risks.append("High market volatility")
    if not market.get("liquidity_ok", True):
        risks.append("Low overall market liquidity")
    rvol = _float(row, "risk_reward") or 0
    if rvol < 2:
        risks.append(f"Risk/reward {rvol:.1f}:1 below 1:2")
    vol_score = _float(row, "risk_liquidity_score") or _float(row, "risk_score") or 50
    if vol_score < 50:
        risks.append(f"Low risk/liquidity score ({vol_score:.0f})")
    return risks


# ---------------------------------------------------------------------------
# 4. Alert Policy
# ---------------------------------------------------------------------------

MAX_BUY_ALERTS_PER_DAY = 5
MAX_ALERTS_PER_STOCK_PER_DAY = 2


def get_daily_alert_counts(db: Session) -> dict[str, int]:
    today_start = datetime.now(CAIRO_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    total = db.query(func.count(NotificationLog.id)).filter(
        NotificationLog.sent_at >= today_start,
        NotificationLog.notification_type.in_(["strategy", "opportunity"]),
    ).scalar() or 0

    per_stock_rows = db.query(
        NotificationLog.symbol,
        func.count(NotificationLog.id).label("cnt")
    ).filter(
        NotificationLog.sent_at >= today_start,
        NotificationLog.notification_type.in_(["strategy", "opportunity"]),
    ).group_by(NotificationLog.symbol).all()

    return {
        "total_buy_alerts": total,
        "per_stock": {r.symbol: r.cnt for r in per_stock_rows},
    }


def check_alert_policy(db: Session, symbol: str, stage: RecommendationStage) -> tuple[bool, str]:
    if stage not in ACTION_STAGES:
        return False, f"Stage {stage.value} does not trigger ACTION alerts"

    counts = get_daily_alert_counts(db)
    if counts["total_buy_alerts"] >= MAX_BUY_ALERTS_PER_DAY:
        return False, f"Daily BUY alert limit reached ({counts['total_buy_alerts']}/{MAX_BUY_ALERTS_PER_DAY})"
    stock_count = counts["per_stock"].get(symbol, 0)
    if stock_count >= MAX_ALERTS_PER_STOCK_PER_DAY:
        return False, f"Stock {symbol} daily alert limit reached ({stock_count}/{MAX_ALERTS_PER_STOCK_PER_DAY})"
    return True, ""


# ---------------------------------------------------------------------------
# 5. Trading Journal
# ---------------------------------------------------------------------------

def journal_recommendation(db: Session, symbol: str, result: PreTradeResult,
                           row: dict[str, Any]) -> TradeJournal:
    entry = _float(row, "entry_price") or _float(row, "entry_zone_low") or 0
    target = _float(row, "target_price") or _float(row, "target_1") or 0
    stop = _float(row, "stop_loss") or 0
    journal = TradeJournal(
        date=datetime.now(CAIRO_TZ),
        symbol=symbol,
        signal=result.stage.value,
        entry_zone=f"{entry:.2f}" if entry else "",
        stop_loss=stop if stop else None,
        targets=[target] if target else None,
        reason_for_entry=result.explanation,
    )
    db.add(journal)
    db.commit()
    return journal


# ---------------------------------------------------------------------------
# 6. Main Pre-Trade Validation Entry Point
# ---------------------------------------------------------------------------

def pre_trade_validate(db: Session, symbol: str, row: dict[str, Any],
                       current_price: float | None = None) -> PreTradeResult:
    market = analyze_market_condition(db)
    filter_result = check_no_trade_filters(db, symbol, row, current_price)
    result = classify_stage(row, market, filter_result)
    return result


def should_send_alert(db: Session, symbol: str, result: PreTradeResult) -> tuple[bool, str]:
    if not result.passed:
        return False, f"Pre-trade validation failed: {result.final_action}"
    if result.stage not in ACTION_STAGES:
        return False, f"Stage {result.stage.value} is not an action stage"
    ok, reason = check_alert_policy(db, symbol, result.stage)
    if not ok:
        return False, reason
    return True, ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float(d: Any, key: str, default: float = 0.0) -> float:
    if isinstance(d, dict):
        v = d.get(key)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return default


def _str(d: Any, key: str, default: str = "") -> str:
    if isinstance(d, dict):
        v = d.get(key)
        if v is not None:
            return str(v)
    return default


def _dict(d: Any, key: str) -> dict:
    if isinstance(d, dict):
        v = d.get(key)
        if isinstance(v, dict):
            return v
    return {}
