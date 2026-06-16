from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.market_data import latest_price
from app.financial.financial_engine import analyze_financial
from app.intelligence.risk_quality import apply_risk_quality_filters
from app.intelligence.weighting_engine import weighted_score, weights_for_symbol
from app.models import FinalStockDecision, FinancialSignal, NewsSignal, StrategySignal, TechnicalSignal, TelegramSignal
from app.news.news_engine import analyze_news
from app.strategies.strategy_runner import aggregate_strategy_score, run_strategies_for_symbol
from app.technical.technical_engine import analyze_technical
from app.telegram.telegram_analyzer import analyze_telegram_for_symbol
from app.services.market_daily_evaluation import evaluate_daily_market


def _signal_from_score(score: float) -> str:
    if score >= 80:
        return "STRONG BUY"
    if score >= 65:
        return "BUY"
    if score >= 50:
        return "WATCH"
    if score >= 40:
        return "HOLD"
    return "AVOID / SELL"


def _risk_level(score: float, source_risks: list[str]) -> str:
    if "HIGH" in source_risks or score < 45:
        return "HIGH"
    if score >= 75 and "MEDIUM" not in source_risks:
        return "LOW"
    return "MEDIUM"


def _latest_strategy_signal(db: Session, symbol: str) -> StrategySignal | None:
    return db.scalar(select(StrategySignal).where(StrategySignal.symbol == symbol).order_by(StrategySignal.signal_date.desc(), StrategySignal.id.desc()))


def _latest_source_snapshots(db: Session, symbol: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    technical_row = db.scalar(
        select(TechnicalSignal)
        .where(TechnicalSignal.symbol == symbol)
        .order_by(TechnicalSignal.signal_date.desc(), TechnicalSignal.id.desc())
    )
    financial_row = db.scalar(
        select(FinancialSignal)
        .where(FinancialSignal.symbol == symbol)
        .order_by(FinancialSignal.signal_date.desc(), FinancialSignal.id.desc())
    )
    news_row = db.scalar(
        select(NewsSignal)
        .where(NewsSignal.symbol == symbol)
        .order_by(NewsSignal.signal_date.desc(), NewsSignal.id.desc())
    )
    telegram_row = db.scalar(
        select(TelegramSignal)
        .where(TelegramSignal.symbol == symbol)
        .order_by(TelegramSignal.signal_date.desc(), TelegramSignal.id.desc())
    )
    strategy_row = _latest_strategy_signal(db, symbol)
    technical = {
        "technical_score": technical_row.technical_score,
        "entry_price": technical_row.entry_price,
        "stop_loss": technical_row.stop_loss,
        "take_profit_1": technical_row.take_profit_1,
        "take_profit_2": technical_row.take_profit_2,
        "risk_level": technical_row.risk_level,
        "reason": technical_row.reason,
    } if technical_row else {}
    financial = {
        "financial_score": financial_row.financial_score,
        "risk_level": financial_row.risk_level,
        "reason": financial_row.reason,
    } if financial_row else {}
    news = {
        "news_score": news_row.news_score,
        "reason": news_row.reason,
        "main_news_drivers": news_row.main_news_drivers,
    } if news_row else {}
    telegram = {
        "telegram_score": telegram_row.telegram_score,
        "reason": telegram_row.reason,
        "top_channels": telegram_row.top_channels,
    } if telegram_row else {}
    strategy = {
        "strategy_score": strategy_row.score,
        "best_strategy": strategy_row.strategy_name,
        "reason": strategy_row.reason,
    } if strategy_row else {"strategy_score": 50, "best_strategy": None}
    return technical, financial, news, telegram, strategy


def build_final_decision(
    db: Session,
    symbol: str,
    *,
    run_sources: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    if run_sources:
        technical = analyze_technical(db, symbol, persist=True)
        financial = analyze_financial(db, symbol, persist=True)
        news = analyze_news(db, symbol, persist=True)
        telegram = analyze_telegram_for_symbol(db, symbol, persist=True)
        strategy_results = run_strategies_for_symbol(db, symbol, persist=True)
        strategy = aggregate_strategy_score(strategy_results)
    else:
        technical, financial, news, telegram, strategy = _latest_source_snapshots(db, symbol)

    scores = {
        "technical": float((technical or {}).get("technical_score") or 50),
        "financial": float((financial or {}).get("financial_score") or 50),
        "news": float((news or {}).get("news_score") or 50),
        "telegram": float((telegram or {}).get("telegram_score") or 50),
        "strategy": float(strategy.get("strategy_score") or 50),
    }
    weights = weights_for_symbol(db, symbol)
    final_score, active_weights = weighted_score(scores, weights)
    final_signal = _signal_from_score(final_score)
    best_analysis = max(scores.items(), key=lambda item: item[1])[0]
    strategy_signal = _latest_strategy_signal(db, symbol)
    entry = (technical or {}).get("entry_price") or latest_price(db, symbol)
    stop = (technical or {}).get("stop_loss")
    tp1 = (technical or {}).get("take_profit_1")
    tp2 = (technical or {}).get("take_profit_2")
    risk = _risk_level(final_score, [(technical or {}).get("risk_level"), (financial or {}).get("risk_level")])
    quality = apply_risk_quality_filters(db, symbol, final_score, final_signal, risk, scores, persist=persist)
    final_score = float(quality["final_score"])
    final_signal = str(quality["final_signal"])
    no_trade_reasons = quality.get("no_trade_reasons") or []
    market_daily = evaluate_daily_market(db, persist=persist)
    permission = str(market_daily.get("trade_permission") or "DATA_INSUFFICIENT")
    if final_signal in {"STRONG BUY", "BUY"} and permission in {"WATCH_ONLY", "BUY_BLOCKED", "SELL_ONLY", "NO_TRADING", "DATA_INSUFFICIENT"}:
        if permission == "WATCH_ONLY":
            final_signal = "WATCH"
        else:
            final_signal = "AVOID / SELL" if permission in {"SELL_ONLY", "NO_TRADING"} else "WATCH"
        no_trade_reasons.append(f"market daily evaluation blocks BUY ({permission})")
    reason = (
        f"Weighted final score {final_score:.0f}. Best source today: {best_analysis}. "
        f"Technical {scores['technical']:.0f}, financial {scores['financial']:.0f}, news {scores['news']:.0f}, "
        f"Telegram {scores['telegram']:.0f}, strategy {scores['strategy']:.0f}. "
        f"{quality.get('reason') or ''}"
    )
    if no_trade_reasons:
        reason += " No-trade reasons: " + ", ".join(no_trade_reasons) + "."
    components = {
        "scores": scores,
        "weights": active_weights,
        "technical": technical,
        "financial": financial,
        "news": news,
        "telegram": telegram,
        "strategy": strategy,
        "risk_quality": quality,
        "market_daily_evaluation": market_daily,
    }
    if persist:
        db.add(
            FinalStockDecision(
                symbol=symbol,
                technical_score=scores["technical"],
                financial_score=scores["financial"],
                news_score=scores["news"],
                telegram_score=scores["telegram"],
                strategy_score=scores["strategy"],
                final_score=final_score,
                liquidity_score=(quality.get("liquidity") or {}).get("liquidity_score"),
                sector_score=(quality.get("sector") or {}).get("relative_score"),
                market_regime=market_daily.get("market_regime") or (quality.get("market") or {}).get("regime"),
                no_trade_reason=", ".join(no_trade_reasons) if no_trade_reasons else None,
                final_signal=final_signal,
                best_analysis_today=best_analysis,
                best_strategy_today=strategy.get("best_strategy") or (strategy_signal.strategy_name if strategy_signal else None),
                entry_price=entry,
                stop_loss=stop,
                take_profit_1=tp1,
                take_profit_2=tp2,
                reason=reason,
                risk_level=risk,
                components_json=components,
            )
        )
    return {
        "symbol": symbol,
        "final_signal": final_signal,
        "final_score": final_score,
        "best_analysis_today": best_analysis,
        "best_strategy_today": strategy.get("best_strategy") or (strategy_signal.strategy_name if strategy_signal else None),
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "reason": reason,
        "risk_level": risk,
        "liquidity_score": (quality.get("liquidity") or {}).get("liquidity_score"),
        "sector_score": (quality.get("sector") or {}).get("relative_score"),
        "market_regime": market_daily.get("market_regime") or (quality.get("market") or {}).get("regime"),
        "market_score": market_daily.get("market_score"),
        "trade_permission": market_daily.get("trade_permission"),
        "no_trade_reasons": no_trade_reasons,
        "components": components,
    }


def latest_final_decision(db: Session, symbol: str) -> FinalStockDecision | None:
    return db.scalar(select(FinalStockDecision).where(FinalStockDecision.symbol == symbol).order_by(FinalStockDecision.decision_date.desc(), FinalStockDecision.id.desc()))
