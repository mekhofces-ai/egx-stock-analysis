from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    AiStockOpinion,
    FinancialSignal,
    FinalStockDecision,
    NewsSignal,
    OHLCVData,
    Opportunity,
    Stock,
    StockCombinedAnalysis,
    StrategyResult,
    StrategySignal,
    TechnicalSignal,
    TelegramSignal,
)
from app.services.dynamic_settings import combined_weights

logger = logging.getLogger(__name__)


def _bound(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def latest_technical_signal(db: Session, symbol: str) -> TechnicalSignal | None:
    return db.scalar(
        select(TechnicalSignal)
        .where(TechnicalSignal.symbol == symbol)
        .order_by(TechnicalSignal.signal_date.desc())
    )


def latest_financial_signal(db: Session, symbol: str) -> FinancialSignal | None:
    return db.scalar(
        select(FinancialSignal)
        .where(FinancialSignal.symbol == symbol)
        .order_by(FinancialSignal.signal_date.desc())
    )


def latest_news_signal(db: Session, symbol: str) -> NewsSignal | None:
    return db.scalar(
        select(NewsSignal)
        .where(NewsSignal.symbol == symbol)
        .order_by(NewsSignal.signal_date.desc())
    )


def latest_telegram_signal(db: Session, symbol: str) -> TelegramSignal | None:
    return db.scalar(
        select(TelegramSignal)
        .where(TelegramSignal.symbol == symbol)
        .order_by(TelegramSignal.signal_date.desc())
    )


def latest_strategy_signal(db: Session, symbol: str) -> StrategySignal | None:
    return db.scalar(
        select(StrategySignal)
        .where(StrategySignal.symbol == symbol)
        .order_by(StrategySignal.signal_date.desc())
    )


def latest_final_decision(db: Session, symbol: str) -> FinalStockDecision | None:
    return db.scalar(
        select(FinalStockDecision)
        .where(FinalStockDecision.symbol == symbol)
        .order_by(FinalStockDecision.decision_date.desc())
    )


def latest_combined_analysis(db: Session, symbol: str) -> StockCombinedAnalysis | None:
    return db.scalar(
        select(StockCombinedAnalysis)
        .where(StockCombinedAnalysis.symbol == symbol)
        .order_by(StockCombinedAnalysis.updated_at.desc())
    )


def latest_opportunity(db: Session, symbol: str) -> Opportunity | None:
    return db.scalar(
        select(Opportunity)
        .where(Opportunity.symbol == symbol)
    )


def latest_price(db: Session, symbol: str) -> float | None:
    row = db.scalar(
        select(OHLCVData)
        .where(OHLCVData.symbol == symbol)
        .order_by(OHLCVData.datetime.desc())
    )
    return row.close if row else None


def get_technical_details(signal: TechnicalSignal | None) -> dict[str, Any]:
    if not signal:
        return {"score": None, "signal": "HOLD", "trend": None, "risk": None}
    return {
        "score": _num(signal.technical_score),
        "signal": signal.signal or "HOLD",
        "confidence": _num(signal.confidence),
        "entry_price": _num(signal.entry_price),
        "stop_loss": _num(signal.stop_loss),
        "take_profit_1": _num(signal.take_profit_1),
        "take_profit_2": _num(signal.take_profit_2),
        "risk_level": signal.risk_level,
        "reason": signal.reason,
    }


def get_financial_details(signal: FinancialSignal | None) -> dict[str, Any]:
    if not signal:
        return {"score": None, "signal": "NEUTRAL", "risk": None}
    return {
        "score": _num(signal.financial_score),
        "signal": signal.financial_signal or "NEUTRAL",
        "profitability_score": _num(signal.profitability_score),
        "growth_score": _num(signal.growth_score),
        "valuation_score": _num(signal.valuation_score),
        "debt_score": _num(signal.debt_score),
        "cashflow_score": _num(signal.cashflow_score),
        "risk_level": signal.risk_level,
        "reason": signal.reason,
    }


def get_news_details(signal: NewsSignal | None) -> dict[str, Any]:
    if not signal:
        return {"score": None, "signal": "NEUTRAL"}
    return {
        "score": _num(signal.news_score),
        "signal": signal.news_signal or "NEUTRAL",
        "main_drivers": signal.main_news_drivers,
        "reason": signal.reason,
    }


def get_telegram_details(signal: TelegramSignal | None) -> dict[str, Any]:
    if not signal:
        return {"score": None, "signal": "NEUTRAL"}
    return {
        "score": _num(signal.telegram_score),
        "signal": signal.telegram_signal or "NEUTRAL",
        "top_channels": signal.top_channels,
        "reason": signal.reason,
    }


def get_strategy_details(signal: StrategySignal | None) -> dict[str, Any]:
    if not signal:
        return {"score": None, "signal": "HOLD"}
    return {
        "score": _num(signal.score),
        "signal": signal.signal or "HOLD",
        "entry_price": _num(signal.entry_price),
        "stop_loss": _num(signal.stop_loss),
        "take_profit_1": _num(signal.take_profit_1),
        "take_profit_2": _num(signal.take_profit_2),
        "strategy_name": signal.strategy_name,
        "reason": signal.reason,
    }


def get_final_details(decision: FinalStockDecision | None) -> dict[str, Any]:
    if not decision:
        return {"score": None, "signal": "WATCH", "risk_level": None}
    return {
        "score": _num(decision.final_score),
        "signal": decision.final_signal or "WATCH",
        "technical_score": _num(decision.technical_score),
        "financial_score": _num(decision.financial_score),
        "news_score": _num(decision.news_score),
        "telegram_score": _num(decision.telegram_score),
        "strategy_score": _num(decision.strategy_score),
        "liquidity_score": _num(decision.liquidity_score),
        "sector_score": _num(decision.sector_score),
        "market_regime": decision.market_regime,
        "no_trade_reason": decision.no_trade_reason,
        "entry_price": _num(decision.entry_price),
        "stop_loss": _num(decision.stop_loss),
        "take_profit_1": _num(decision.take_profit_1),
        "take_profit_2": _num(decision.take_profit_2),
        "risk_level": decision.risk_level,
        "reason": decision.reason,
        "best_analysis_today": decision.best_analysis_today,
        "best_strategy_today": decision.best_strategy_today,
    }


def get_ai_analysis_for_symbol(symbol: str) -> dict[str, Any]:
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
        tech = latest_technical_signal(db, symbol)
        fin = latest_financial_signal(db, symbol)
        news = latest_news_signal(db, symbol)
        tele = latest_telegram_signal(db, symbol)
        strat = latest_strategy_signal(db, symbol)
        decision = latest_final_decision(db, symbol)
        combined = latest_combined_analysis(db, symbol)
        opp = latest_opportunity(db, symbol)
        price = latest_price(db, symbol)

        weights = combined_weights(db)

        scores = {}
        for key, signal in [("technical", tech), ("financial", fin), ("news", news), ("telegram", tele), ("strategy", strat)]:
            if signal:
                s = getattr(signal, f"{key}_score" if key != "strategy" else "score", None)
                scores[key] = _num(s)

        weighted_total = 0.0
        weight_sum = 0.0
        score_map = {
            "technical": scores.get("technical"),
            "financial": scores.get("financial"),
            "news": scores.get("news"),
            "telegram": scores.get("telegram"),
            "strategy": scores.get("strategy"),
        }
        weight_map = {
            "technical": weights.get("strategy_legacy", 20.0),
            "financial": 25.0,
            "news": weights.get("daily_report", 15.0),
            "telegram": weights.get("telegram", 20.0),
            "strategy": weights.get("cli_v6", 20.0),
        }
        for key in score_map:
            s = score_map[key]
            w = weight_map.get(key, 10.0)
            if s is not None:
                weighted_total += s * w
                weight_sum += w
        fallback_score = _bound(weighted_total / weight_sum) if weight_sum > 0 else None

        # Primary score: use stock_combined_analysis (rich TradingView-derived data, 223 symbols)
        if combined and combined.final_score is not None:
            ai_score = _bound(combined.final_score)
        elif fallback_score is not None:
            ai_score = fallback_score
        else:
            ai_score = 50.0

    opinion_row = db.scalar(
        select(AiStockOpinion)
        .where(AiStockOpinion.symbol == symbol)
        .order_by(AiStockOpinion.created_at.desc())
    )

    result = {
        "symbol": symbol,
        "name": f"{stock.name_en or stock.symbol} ({stock.sector or 'N/A'})" if stock else symbol,
        "sector": stock.sector if stock else None,
        "last_price": price,
        "ai_score": ai_score,
        "ai_signal": _score_to_signal(ai_score),
        "technical": get_technical_details(tech),
        "financial": get_financial_details(fin),
        "news": get_news_details(news),
        "telegram": get_telegram_details(tele),
        "strategy": get_strategy_details(strat),
        "final_decision": get_final_details(decision),
        "combined_analysis": {
            "score": _num(combined.final_score if combined else None),
            "recommendation": combined.final_recommendation if combined else None,
            "confidence": _num(combined.confidence if combined else None),
        } if combined else None,
        "opportunity": {
            "score": _num(opp.final_score if opp else None),
            "recommendation": opp.recommendation if opp else None,
            "confidence": _num(opp.confidence if opp else None),
        } if opp else None,
        "ai_opinion": {
            "score": opinion_row.ai_score,
            "signal": opinion_row.ai_signal,
            "opinion": opinion_row.ai_opinion,
            "reasoning": opinion_row.ai_reasoning,
            "key_drivers": opinion_row.ai_key_drivers,
            "risks": opinion_row.ai_risks,
            "catalyst": opinion_row.ai_catalyst,
            "entry_zone": opinion_row.ai_entry_zone,
            "stop_loss": opinion_row.ai_stop_loss,
            "target_1": opinion_row.ai_target_1,
            "target_2": opinion_row.ai_target_2,
            "time_horizon": opinion_row.ai_time_horizon,
            "confidence": opinion_row.ai_confidence,
            "model_used": opinion_row.model_used,
            "tokens_used": opinion_row.tokens_used,
        } if opinion_row else None,
        "analysis_time": datetime.now(timezone.utc).isoformat(),
    }
    return result


def _score_to_signal(score: float) -> str:
    if score >= 80:
        return "STRONG BUY"
    if score >= 65:
        return "BUY"
    if score >= 50:
        return "WATCH"
    if score >= 35:
        return "NEUTRAL"
    if score >= 20:
        return "AVOID"
    return "SELL"


def get_all_ai_analyses() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        symbols = db.scalars(
            select(Stock.symbol).where(Stock.is_active == True).order_by(Stock.symbol.asc())
        ).all()
    results = []
    for sym in symbols:
        try:
            results.append(get_ai_analysis_for_symbol(sym))
        except Exception as exc:
            logger.warning("AI analysis failed for %s: %s", sym, exc)
    return sorted(results, key=lambda r: r["ai_score"], reverse=True)


def get_market_overview() -> dict[str, Any]:
    with SessionLocal() as db:
        total = int(db.scalar(select(func.count()).select_from(Stock).where(Stock.is_active == True)) or 0)
        rows = db.scalars(
            select(StockCombinedAnalysis)
            .order_by(StockCombinedAnalysis.updated_at.desc(), StockCombinedAnalysis.final_score.desc())
            .limit(500)
        ).all()
    if total == 0 and not rows:
        return {"total_symbols": 0}

    latest: dict[str, StockCombinedAnalysis] = {}
    for row in rows:
        latest.setdefault(row.symbol, row)
    analyses = [
        {
            "symbol": row.symbol,
            "ai_score": _bound(float(row.final_score or 50.0)),
            "ai_signal": _score_to_signal(float(row.final_score or 50.0)),
            "recommendation": row.final_recommendation,
            "confidence": row.confidence,
        }
        for row in latest.values()
    ]
    analyses = sorted(analyses, key=lambda r: float(r["ai_score"] or 0), reverse=True)
    evaluated = len(analyses)
    strong_buys = sum(1 for a in analyses if a["ai_signal"] == "STRONG BUY")
    buys = sum(1 for a in analyses if a["ai_signal"] == "BUY")
    watches = sum(1 for a in analyses if a["ai_signal"] == "WATCH")
    neutrals = sum(1 for a in analyses if a["ai_signal"] == "NEUTRAL")
    avoids = sum(1 for a in analyses if a["ai_signal"] == "AVOID")
    sells = sum(1 for a in analyses if a["ai_signal"] == "SELL")
    avg_score = sum(float(a["ai_score"] or 0) for a in analyses) / evaluated if evaluated > 0 else 0
    top = analyses[:10]
    worst = list(reversed(analyses[-10:])) if len(analyses) >= 10 else list(reversed(analyses))
    return {
        "total_symbols": total,
        "evaluated_symbols": evaluated,
        "strong_buys": strong_buys,
        "buys": buys,
        "watches": watches,
        "neutrals": neutrals,
        "avoids": avoids,
        "sells": sells,
        "avg_score": round(avg_score, 2),
        "top_opportunities": top,
        "worst_performers": worst,
    }
