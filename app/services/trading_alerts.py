from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import RISK_NOTE
from app.data.market_data import latest_price
from app.models import (
    FinalStockDecision,
    Opportunity,
    PortfolioPosition,
    RecommendationItem,
    RecommendationReport,
    Stock,
    StockCombinedAnalysis,
)
from app.services.notification_dedup import mark_sent, should_send
from app.services.recommendation_validation import CONDITIONAL_BUY, validate_recommendation


BUY_SIGNALS = {"STRONG BUY", "BUY", CONDITIONAL_BUY}
SELL_SIGNALS = {"AVOID", "AVOID / SELL", "SELL", "STRONG SELL"}


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _round(value: Any, digits: int = 2) -> float | None:
    number = _safe_float(value)
    return round(number, digits) if number is not None else None


def _risk_reward(entry: Any, stop_loss: Any, target: Any) -> float | None:
    entry_value = _safe_float(entry)
    stop_value = _safe_float(stop_loss)
    target_value = _safe_float(target)
    if entry_value is None or stop_value is None or target_value is None:
        return None
    risk = entry_value - stop_value
    reward = target_value - entry_value
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _opportunity_component(row: Opportunity, key: str, default: float = 50.0) -> float:
    payload = row.components_json or {}
    components = payload.get("components") if isinstance(payload, dict) else {}
    if not isinstance(components, dict):
        components = {}
    aliases = {
        "telegram": ["telegram", "telegram_score"],
        "strategy": ["cli_v6_strategy", "strategy", "strategy_score"],
        "backtest": ["backtest", "backtest_score"],
        "risk_liquidity": ["risk_score", "risk_liquidity_score", "liquidity"],
        "technical": ["technical", "tradingview", "system_recommendation"],
        "news": ["news", "news_score"],
    }
    for name in aliases.get(key, [key]):
        if name in components:
            return _safe_float(components.get(name), default) or default
        if isinstance(payload, dict) and name in payload:
            return _safe_float(payload.get(name), default) or default
    return default


def classify_position_alert(
    *,
    current_price: float | None,
    stop_loss: float | None,
    take_profit_1: float | None,
    take_profit_2: float | None,
    final_signal: str | None = None,
    final_score: float | None = None,
    hold_threshold: float = 45.0,
) -> dict[str, Any] | None:
    price = _safe_float(current_price)
    stop = _safe_float(stop_loss)
    tp1 = _safe_float(take_profit_1)
    tp2 = _safe_float(take_profit_2)
    score = _safe_float(final_score)
    signal = str(final_signal or "").upper()

    if price is not None and tp2 is not None and price >= tp2:
        return {"alert_type": "TAKE PROFIT", "priority": 98, "trigger": "TP2 reached", "trigger_price": tp2}
    if price is not None and tp1 is not None and price >= tp1:
        return {"alert_type": "TAKE PROFIT", "priority": 94, "trigger": "TP1 reached", "trigger_price": tp1}
    if price is not None and stop is not None and price <= stop:
        return {"alert_type": "SELL", "priority": 99, "trigger": "Stop loss hit", "trigger_price": stop}
    if signal in SELL_SIGNALS or (score is not None and score < hold_threshold):
        return {"alert_type": "SELL", "priority": 86, "trigger": "Final analysis no longer supports holding", "trigger_price": price}
    return None


def _latest_final_decisions(db: Session, limit: int = 1000) -> dict[str, FinalStockDecision]:
    rows = db.scalars(select(FinalStockDecision).order_by(FinalStockDecision.decision_date.desc(), FinalStockDecision.id.desc()).limit(limit)).all()
    latest: dict[str, FinalStockDecision] = {}
    for row in rows:
        latest.setdefault(row.symbol, row)
    return latest


def _stock_names(db: Session) -> dict[str, str]:
    return {row.symbol: row.name or row.name_en or row.name_ar or row.symbol for row in db.scalars(select(Stock)).all()}


def _latest_report_items(db: Session) -> list[RecommendationItem]:
    report = db.scalar(select(RecommendationReport).order_by(RecommendationReport.created_at.desc(), RecommendationReport.id.desc()))
    if not report:
        return []
    return db.scalars(select(RecommendationItem).where(RecommendationItem.report_id == report.id).order_by(RecommendationItem.final_score.desc())).all()


def _base_alert(symbol: str, company_name: str | None, alert_type: str, source: str, priority: float) -> dict[str, Any]:
    return {
        "alert_type": alert_type,
        "symbol": symbol,
        "company_name": company_name or symbol,
        "priority": float(priority),
        "source": source,
        "sources": [source],
        "created_at": datetime.utcnow(),
    }


def _merge_alert(alerts: dict[tuple[str, str], dict[str, Any]], alert: dict[str, Any]) -> None:
    key = (str(alert.get("alert_type") or ""), str(alert.get("symbol") or ""))
    existing = alerts.get(key)
    if existing is None:
        alerts[key] = alert
        return
    existing["priority"] = max(float(existing.get("priority") or 0), float(alert.get("priority") or 0))
    existing["confidence"] = max(float(existing.get("confidence") or 0), float(alert.get("confidence") or 0))
    existing["final_score"] = max(float(existing.get("final_score") or 0), float(alert.get("final_score") or 0))
    for source in alert.get("sources") or [alert.get("source")]:
        if source and source not in existing["sources"]:
            existing["sources"].append(source)
    existing["source"] = ", ".join(existing["sources"])
    existing["reason"] = " | ".join(
        part
        for part in [existing.get("reason"), alert.get("reason")]
        if part and part not in str(existing.get("reason") or "")
    )
    for field in ["current_price", "entry_price", "stop_loss", "target_1", "target_2", "trigger_price", "risk_level", "market_regime"]:
        if existing.get(field) is None and alert.get(field) is not None:
            existing[field] = alert[field]


def _alert_from_decision(row: FinalStockDecision, company_name: str | None, *, min_buy_score: float) -> dict[str, Any] | None:
    signal = str(row.final_signal or "").upper()
    score = _safe_float(row.final_score, 0) or 0
    if signal not in BUY_SIGNALS or score < min_buy_score:
        return None
    validation = validate_recommendation(
        {
            "signal": signal,
            "final_score": score,
            "telegram_score": row.telegram_score,
            "technical_score": row.technical_score,
            "strategy_score": row.strategy_score,
            "news_score": row.news_score,
            "backtest_score": None,
            "risk_liquidity_score": row.liquidity_score,
            "entry_zone_low": row.entry_price,
            "entry_zone_high": row.entry_price,
            "stop_loss": row.stop_loss,
            "risk_reward": _risk_reward(row.entry_price, row.stop_loss, row.take_profit_1 or row.take_profit_2),
        },
        current_price=latest_price_from_decision(row),
    )
    if validation.signal != CONDITIONAL_BUY:
        return None
    if str(row.risk_level or "").upper() == "HIGH" and score < 78:
        return None
    alert = _base_alert(row.symbol, company_name, "BUY", "Final weighted decision", score)
    alert.update(
        {
            "action": CONDITIONAL_BUY,
            "confidence": _round(score, 0),
            "final_score": _round(score, 0),
            "current_price": _round(latest_price_from_decision(row)),
            "entry_price": _round(row.entry_price),
            "stop_loss": _round(row.stop_loss),
            "target_1": _round(row.take_profit_1),
            "target_2": _round(row.take_profit_2),
            "risk_level": row.risk_level,
            "market_regime": row.market_regime,
            "reason": (row.reason or f"Final signal is {row.final_signal} with score {score:.0f}.")
            + " Conditions: price must reach entry zone, volume must confirm, and risk per trade must stay below 1%.",
            "component_scores": {
                "technical": row.technical_score,
                "financial": row.financial_score,
                "news": row.news_score,
                "telegram": row.telegram_score,
                "strategy": row.strategy_score,
                "liquidity": row.liquidity_score,
            },
        }
    )
    return alert


def latest_price_from_decision(row: FinalStockDecision) -> float | None:
    return _safe_float(row.entry_price)


def _alert_from_opportunity(row: Opportunity, company_name: str | None, *, min_buy_score: float) -> dict[str, Any] | None:
    score = _safe_float(row.final_score, 0) or 0
    recommendation = str(row.recommendation or "").upper()
    if recommendation not in BUY_SIGNALS or score < min_buy_score:
        return None
    validation = validate_recommendation(
        {
            "signal": recommendation,
            "final_score": score,
            "telegram_score": _opportunity_component(row, "telegram"),
            "technical_score": _opportunity_component(row, "technical"),
            "strategy_score": _opportunity_component(row, "strategy"),
            "news_score": _opportunity_component(row, "news"),
            "backtest_score": _opportunity_component(row, "backtest"),
            "risk_liquidity_score": _opportunity_component(row, "risk_liquidity", row.confidence if row.confidence is not None else score),
            "entry_zone_low": row.entry_price,
            "entry_zone_high": row.entry_price,
            "stop_loss": row.stop_loss,
            "risk_reward": _risk_reward(row.entry_price, row.stop_loss, row.target_price),
        },
        current_price=latest_price_from_decision(row) if isinstance(row, FinalStockDecision) else row.entry_price,
    )
    if validation.signal != CONDITIONAL_BUY:
        return None
    alert = _base_alert(row.symbol, company_name, "BUY", "Opportunity engine", score)
    alert.update(
        {
            "action": CONDITIONAL_BUY,
            "confidence": _round(row.confidence if row.confidence else score, 0),
            "final_score": _round(score, 0),
            "entry_price": _round(row.entry_price),
            "stop_loss": _round(row.stop_loss),
            "target_1": _round(row.target_price),
            "reason": (row.reason or f"Opportunity engine recommendation is {row.recommendation}.")
            + " Conditions: enter only inside the entry zone with volume confirmation.",
        }
    )
    return alert


def _alert_from_report_item(row: RecommendationItem, company_name: str | None, *, min_buy_score: float) -> dict[str, Any] | None:
    score = _safe_float(row.final_score, 0) or 0
    signal = str(row.signal or "").upper()
    if signal not in BUY_SIGNALS or score < min_buy_score:
        return None
    validation = validate_recommendation(
        {
            "signal": signal,
            "final_score": row.final_score,
            "telegram_score": row.telegram_score,
            "technical_score": row.technical_score,
            "strategy_score": row.strategy_score,
            "news_score": row.news_score,
            "backtest_score": row.backtest_score,
            "risk_liquidity_score": row.risk_liquidity_score,
            "entry_zone_low": row.entry_zone_low,
            "entry_zone_high": row.entry_zone_high,
            "stop_loss": row.stop_loss,
            "risk_reward": row.risk_reward,
        },
        current_price=row.entry_zone_high,
    )
    if validation.signal != CONDITIONAL_BUY:
        return None
    alert = _base_alert(row.symbol, company_name or row.company_name, "BUY", "Daily stock report", score)
    alert.update(
        {
            "action": CONDITIONAL_BUY,
            "confidence": _round(score, 0),
            "final_score": _round(score, 0),
            "entry_price": _round(row.entry_zone_high or row.entry_zone_low),
            "stop_loss": _round(row.stop_loss),
            "target_1": _round(row.target_1),
            "target_2": _round(row.target_2),
            "trigger_price": _round(row.entry_zone_low),
            "reason": (row.explanation or f"Daily report signal is {row.signal} with score {score:.0f}.")
            + " Entry is conditional; do not enter unless price reaches the zone and volume confirms.",
            "component_scores": {
                "telegram": row.telegram_score,
                "technical": row.technical_score,
                "strategy": row.strategy_score,
                "news": row.news_score,
                "backtest": row.backtest_score,
                "risk_liquidity": row.risk_liquidity_score,
            },
        }
    )
    return alert


def _alert_from_position(
    db: Session,
    position: PortfolioPosition,
    decision: FinalStockDecision | None,
    company_name: str | None,
) -> dict[str, Any] | None:
    price = latest_price(db, position.symbol) or position.current_price or position.buy_price
    classification = classify_position_alert(
        current_price=price,
        stop_loss=position.stop_loss,
        take_profit_1=position.take_profit_1,
        take_profit_2=position.take_profit_2,
        final_signal=decision.final_signal if decision else None,
        final_score=decision.final_score if decision else None,
    )
    if not classification:
        return None
    alert = _base_alert(position.symbol, company_name, classification["alert_type"], "Portfolio position", classification["priority"])
    pnl_pct = ((price - position.buy_price) / position.buy_price * 100) if position.buy_price else None
    alert.update(
        {
            "action": classification["alert_type"],
            "confidence": _round(classification["priority"], 0),
            "final_score": _round(decision.final_score if decision else None, 0),
            "current_price": _round(price),
            "entry_price": _round(position.buy_price),
            "stop_loss": _round(position.stop_loss),
            "target_1": _round(position.take_profit_1),
            "target_2": _round(position.take_profit_2),
            "trigger_price": _round(classification.get("trigger_price")),
            "risk_level": decision.risk_level if decision else None,
            "reason": f"{classification['trigger']}. Open position P/L is {_round(pnl_pct, 2)}%.",
        }
    )
    return alert


def build_trading_alerts(
    db: Session,
    *,
    min_buy_score: float = 65.0,
    include_sell_without_position: bool = True,
    limit: int = 300,
) -> list[dict[str, Any]]:
    names = _stock_names(db)
    decisions = _latest_final_decisions(db)
    alerts: dict[tuple[str, str], dict[str, Any]] = {}

    for row in decisions.values():
        alert = _alert_from_decision(row, names.get(row.symbol), min_buy_score=min_buy_score)
        if alert:
            alert["current_price"] = _round(latest_price(db, row.symbol) or row.entry_price)
            _merge_alert(alerts, alert)
        elif include_sell_without_position and str(row.final_signal or "").upper() in SELL_SIGNALS:
            score = _safe_float(row.final_score, 0) or 0
            sell = _base_alert(row.symbol, names.get(row.symbol), "SELL", "Final weighted decision", max(40, 100 - score))
            sell.update(
                {
                    "action": "SELL / AVOID",
                    "confidence": _round(max(40, 100 - score), 0),
                    "final_score": _round(score, 0),
                    "current_price": _round(latest_price(db, row.symbol) or row.entry_price),
                    "entry_price": _round(row.entry_price),
                    "stop_loss": _round(row.stop_loss),
                    "target_1": _round(row.take_profit_1),
                    "target_2": _round(row.take_profit_2),
                    "risk_level": row.risk_level,
                    "market_regime": row.market_regime,
                    "reason": row.reason or f"Final signal is {row.final_signal}; avoid or exit if holding.",
                }
            )
            _merge_alert(alerts, sell)

    opportunities = db.scalars(select(Opportunity).order_by(Opportunity.updated_at.desc(), Opportunity.final_score.desc()).limit(limit)).all()
    for row in opportunities:
        alert = _alert_from_opportunity(row, names.get(row.symbol), min_buy_score=min_buy_score)
        if alert:
            alert["current_price"] = _round(latest_price(db, row.symbol) or row.entry_price)
            _merge_alert(alerts, alert)

    for row in _latest_report_items(db):
        alert = _alert_from_report_item(row, names.get(row.symbol), min_buy_score=min_buy_score)
        if alert:
            alert["current_price"] = _round(latest_price(db, row.symbol) or row.entry_zone_high or row.entry_zone_low)
            _merge_alert(alerts, alert)

    combined_rows = db.scalars(select(StockCombinedAnalysis).order_by(StockCombinedAnalysis.updated_at.desc(), StockCombinedAnalysis.final_score.desc()).limit(limit)).all()
    for row in combined_rows:
        score = _safe_float(row.final_score, 0) or 0
        if str(row.final_recommendation or "").upper() in BUY_SIGNALS and score >= min_buy_score:
            alert = _base_alert(row.symbol, names.get(row.symbol), "BUY", "Combined analysis", score)
            alert.update(
                {
                    "action": "BUY",
                    "confidence": _round(row.confidence if row.confidence else score, 0),
                    "final_score": _round(score, 0),
                    "current_price": _round(latest_price(db, row.symbol)),
                    "reason": row.reason or f"Combined analysis is {row.final_recommendation} at {score:.0f}.",
                    "component_scores": row.components_json,
                }
            )
            _merge_alert(alerts, alert)

    for position in db.scalars(select(PortfolioPosition).where(PortfolioPosition.status == "open")).all():
        alert = _alert_from_position(db, position, decisions.get(position.symbol), names.get(position.symbol))
        if alert:
            _merge_alert(alerts, alert)

    rows = sorted(alerts.values(), key=lambda item: (float(item.get("priority") or 0), float(item.get("final_score") or 0)), reverse=True)
    return rows[:limit]


def format_trading_alerts_message(alerts: list[dict[str, Any]], *, max_items: int = 10) -> str:
    lines = ["EGX Trading Alerts", ""]
    if not alerts:
        lines.append("No BUY, SELL, or TAKE PROFIT alerts right now.")
    for idx, alert in enumerate(alerts[:max_items], start=1):
        lines.extend(
            [
                f"{idx}. {alert.get('alert_type')} - {alert.get('symbol')} {alert.get('company_name') or ''}",
                f"Signal: {alert.get('action') or alert.get('alert_type')} | Confidence: {alert.get('confidence') or '-'}% | Score: {alert.get('final_score') or '-'}",
                f"Current: {alert.get('current_price') or '-'} | Entry: {alert.get('entry_price') or '-'} | Stop: {alert.get('stop_loss') or '-'}",
                f"Targets: {alert.get('target_1') or '-'} / {alert.get('target_2') or '-'} | Trigger: {alert.get('trigger_price') or '-'}",
                f"Source: {alert.get('source') or '-'}",
                f"Reason: {alert.get('reason') or '-'}",
                "",
            ]
        )
    lines.append(f"Risk Note: {RISK_NOTE}")
    return "\n".join(lines)


def send_trading_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    if not alerts:
        return {"sent": 0, "message": "No alerts to send."}
    from app.services.telegram_bot import send_private_message_sync
    from app.database import SessionLocal

    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with SessionLocal() as db:
        for alert in alerts:
            symbol = str(alert.get("symbol") or "").strip().upper()
            recommendation = str(alert.get("action") or alert.get("alert_type") or "").strip().upper()
            alert_type = f"TRADING_{str(alert.get('alert_type') or 'ALERT').upper()}"[:64]
            ok, reason = should_send(
                db,
                symbol=symbol,
                recommendation=recommendation,
                notification_type=alert_type,
                entry_zone=str(alert.get("entry_price") or ""),
                target=str(alert.get("target_1") or ""),
                stop_loss=str(alert.get("stop_loss") or ""),
            )
            if ok:
                eligible.append(alert)
            else:
                skipped.append({"symbol": symbol, "recommendation": recommendation, "reason": reason})

        if not eligible:
            return {"sent": 0, "items": 0, "skipped_duplicate": len(skipped), "skipped": skipped, "message": "All visible alerts were duplicate or over quota."}

        message = format_trading_alerts_message(eligible)
        send_private_message_sync(message)
        for alert in eligible:
            symbol = str(alert.get("symbol") or "").strip().upper()
            recommendation = str(alert.get("action") or alert.get("alert_type") or "").strip().upper()
            alert_type = f"TRADING_{str(alert.get('alert_type') or 'ALERT').upper()}"[:64]
            mark_sent(
                db,
                symbol=symbol,
                recommendation=recommendation,
                notification_type=alert_type,
                source_module=str(alert.get("source") or "Trading Alerts"),
                score=_safe_float(alert.get("final_score")),
                entry_zone=str(alert.get("entry_price") or ""),
                target=str(alert.get("target_1") or ""),
                stop_loss=str(alert.get("stop_loss") or ""),
            )
        db.commit()
    return {"sent": 1, "items": len(eligible), "skipped_duplicate": len(skipped), "skipped": skipped}
