from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.market_data import latest_price
from app.intelligence.portfolio_bot import calculate_quantity, get_portfolio_settings, portfolio_value
from app.models import ConfidenceCalibration, FinalStockDecision, MistakeReview, PortfolioTrade, SignalAccuracyTracking, Stock, TelegramChannelPerformance


def scenario_for_stock(db: Session, symbol: str, capital: float) -> dict[str, Any]:
    settings = get_portfolio_settings(db)
    decision = db.scalar(select(FinalStockDecision).where(FinalStockDecision.symbol == symbol).order_by(FinalStockDecision.decision_date.desc()))
    entry = (decision.entry_price if decision else None) or latest_price(db, symbol)
    stop = decision.stop_loss if decision else None
    tp1 = decision.take_profit_1 if decision else None
    tp2 = decision.take_profit_2 if decision else None
    if not entry:
        return {"status": "missing_price", "symbol": symbol, "reason": "No price available."}
    risk_per_share = max(entry - stop, 0.0001) if stop else entry * 0.03
    quantity = int(capital // entry)
    risk_qty = calculate_quantity(settings, entry, stop, max(portfolio_value(db, settings)["total_value"], capital))
    quantity = min(quantity, risk_qty) if risk_qty > 0 else quantity
    expected_loss = quantity * risk_per_share
    expected_gain_1 = quantity * max((tp1 or entry) - entry, 0)
    expected_gain_2 = quantity * max((tp2 or entry) - entry, 0)
    return {
        "status": "ok",
        "symbol": symbol,
        "capital": capital,
        "quantity": quantity,
        "entry_price": entry,
        "stop_loss": stop,
        "expected_loss": round(expected_loss, 2),
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "expected_gain_1": round(expected_gain_1, 2),
        "expected_gain_2": round(expected_gain_2, 2),
        "risk_reward_ratio": round(expected_gain_1 / expected_loss, 2) if expected_loss else None,
    }


def build_mistake_reviews(db: Session, *, limit: int = 200) -> dict[str, int]:
    trades = db.scalars(
        select(PortfolioTrade)
        .where(PortfolioTrade.trade_type == "SELL", PortfolioTrade.profit_loss < 0)
        .order_by(PortfolioTrade.trade_date.desc())
        .limit(limit)
    ).all()
    created = 0
    for trade in trades:
        existing = db.scalar(select(MistakeReview).where(MistakeReview.trade_id == trade.id))
        if existing:
            continue
        reasons: list[str] = []
        if (trade.technical_score or 50) < 50:
            reasons.append("wrong technical signal")
        if (trade.news_score or 50) < 45:
            reasons.append("bad news after entry")
        if (trade.financial_score or 50) < 45:
            reasons.append("weak financials")
        if (trade.telegram_score or 50) > 70 and (trade.profit_loss or 0) < 0:
            reasons.append("Telegram hype was wrong")
        if not reasons:
            reasons.append("entry was late or stop loss was too tight")
        db.add(
            MistakeReview(
                trade_id=trade.id,
                symbol=trade.symbol,
                loss_amount=trade.profit_loss,
                loss_pct=trade.profit_loss_pct,
                suspected_reason=", ".join(reasons),
                improvement="Review entry timing, market regime, liquidity, and stop distance before the next similar trade.",
                related_json={"trade_date": trade.trade_date.isoformat() if trade.trade_date else None},
            )
        )
        created += 1
    return {"created": created}


def confidence_calibration(db: Session, *, persist: bool = True) -> list[dict[str, Any]]:
    rows = db.scalars(select(FinalStockDecision).order_by(FinalStockDecision.decision_date.desc()).limit(1000)).all()
    accuracy = {
        (row.symbol, row.decision_date): row
        for row in db.scalars(select(SignalAccuracyTracking).where(SignalAccuracyTracking.final_decision_correct.is_not(None))).all()
    }
    buckets: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        key = (row.symbol, row.decision_date)
        acc = accuracy.get(key)
        if not acc:
            continue
        score = float(row.final_score or 50)
        bucket_floor = int(score // 10) * 10
        bucket = f"{bucket_floor}-{bucket_floor + 9}"
        buckets[bucket].append(bool(acc.final_decision_correct))
    output: list[dict[str, Any]] = []
    for bucket, values in sorted(buckets.items()):
        expected = float(bucket.split("-", 1)[0]) + 5
        observed = sum(values) / len(values) * 100 if values else 0
        payload = {
            "bucket": bucket,
            "analysis_type": "final_model",
            "sample_count": len(values),
            "expected_confidence": expected,
            "observed_win_rate": round(observed, 2),
            "calibration_error": round(abs(observed - expected), 2),
        }
        if persist:
            db.add(ConfidenceCalibration(**payload))
        output.append(payload)
    return output


def best_analysis_by_symbol(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(SignalAccuracyTracking)).all()
    grouped: dict[str, list[SignalAccuracyTracking]] = defaultdict(list)
    for row in rows:
        grouped[row.symbol].append(row)
    output: list[dict[str, Any]] = []
    fields = ["technical", "financial", "news", "telegram", "strategy", "final_decision"]
    for symbol, items in grouped.items():
        result = {"symbol": symbol}
        rates: dict[str, float] = {}
        for field in fields:
            values = [getattr(row, f"{field}_correct") for row in items if getattr(row, f"{field}_correct") is not None]
            rate = round(sum(1 for value in values if value) / len(values) * 100, 2) if values else None
            result[f"{field}_accuracy"] = rate
            if rate is not None:
                rates[field] = rate
        result["best_historical_analysis"] = max(rates.items(), key=lambda item: item[1])[0] if rates else None
        output.append(result)
    return output


def best_analysis_by_market_condition(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(FinalStockDecision).where(FinalStockDecision.market_regime.is_not(None))).all()
    grouped: dict[str, list[FinalStockDecision]] = defaultdict(list)
    for row in rows:
        grouped[row.market_regime or "unknown"].append(row)
    output = []
    for regime, decisions in grouped.items():
        averages = {
            "technical": sum(float(row.technical_score or 50) for row in decisions) / len(decisions),
            "financial": sum(float(row.financial_score or 50) for row in decisions) / len(decisions),
            "news": sum(float(row.news_score or 50) for row in decisions) / len(decisions),
            "telegram": sum(float(row.telegram_score or 50) for row in decisions) / len(decisions),
            "strategy": sum(float(row.strategy_score or 50) for row in decisions) / len(decisions),
        }
        output.append({"market_regime": regime, "sample_count": len(decisions), "best_analysis": max(averages.items(), key=lambda item: item[1])[0], **{f"{k}_avg": round(v, 2) for k, v in averages.items()}})
    return output

