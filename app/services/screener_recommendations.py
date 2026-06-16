from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DISCLAIMER, Settings, get_settings
from app.models import ExtractedSignal, FinalAnalysis, Stock, TelegramSource
from app.services.market_data.tradingview_screener import TradingViewScreenerProvider


@dataclass
class ScreenerRun:
    rows: list[dict[str, Any]]
    provider: str
    provider_status: str
    provider_warning: str | None = None
    generated_at: datetime = field(default_factory=datetime.utcnow)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _bound(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _tv_vote(rec_all: float, rsi: float) -> str:
    if rec_all >= 0.5 and rsi < 78:
        return "STRONG_BUY"
    if rec_all >= 0.2:
        return "BUY"
    if rec_all <= -0.5:
        return "SELL"
    if rec_all <= -0.2:
        return "AVOID"
    return "NEUTRAL"


def _telegram_vote(score: float, buy_count: int, sell_count: int) -> str:
    if sell_count > buy_count and score < 45:
        return "NEGATIVE"
    if buy_count >= 2 and score >= 60:
        return "POSITIVE"
    if buy_count or sell_count:
        return "MIXED"
    return "NONE"


def _final_decision(final_score: float, tv_vote: str, telegram_vote: str, rsi: float, warnings: list[str]) -> str:
    if "Telegram hype is high" in warnings and rsi >= 78:
        return "HIGH_RISK"
    if tv_vote == "SELL" or final_score < 32:
        return "SELL"
    if tv_vote == "AVOID" or final_score < 45:
        return "AVOID"
    if final_score >= 78 and tv_vote in {"STRONG_BUY", "BUY"} and telegram_vote in {"POSITIVE", "MIXED", "NONE"}:
        return "BUY"
    if final_score >= 60:
        return "WATCH"
    return "NEUTRAL"


def tradingview_chart_url(symbol: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol=EGX%3A{symbol.upper()}"


def _smart_pro_overlay(
    symbol: str,
    last_price: float | None,
    rec_all: float,
    rec_ma: float,
    rsi: float,
    change: float,
    liquidity_rank: float,
    final_score: float,
    final_decision: str,
    telegram_vote: str,
) -> dict[str, Any]:
    score = 0
    score += 1 if rec_all > 0 else 0
    score += 1 if rec_all >= 0.2 else 0
    score += 1 if rec_ma >= 0.2 else 0
    score += 1 if rec_ma >= 0.5 else 0
    score += 1 if 45 <= rsi < 80 else 0
    score += 1 if rsi >= 50 and rsi < 75 else 0
    score += 1 if change > 0 else 0
    score += 1 if change >= 1.5 else 0
    score += 1 if liquidity_rank >= 0.65 else 0
    score += 1 if telegram_vote in {"POSITIVE", "MIXED"} else 0

    if rec_ma >= 0.6 and final_score >= 78:
        main_trend = "LONG BULLISH"
    elif rec_ma >= 0.35 and final_score >= 68:
        main_trend = "SWING BULLISH"
    elif rec_all > 0 and final_score >= 58:
        main_trend = "SHORT BULLISH"
    elif rec_all <= -0.2:
        main_trend = "BEARISH"
    else:
        main_trend = "NEUTRAL"

    if liquidity_rank >= 0.85:
        volume_status = "Very Strong"
    elif liquidity_rank >= 0.65:
        volume_status = "Strong"
    elif liquidity_rank <= 0.25:
        volume_status = "Weak"
    else:
        volume_status = "Normal"

    pressure = "Buy Pressure" if rec_all > 0.2 and change >= 0 else "Sell Pressure" if rec_all < -0.2 or change < -2 else "Neutral"

    if final_decision in {"SELL", "AVOID"} or pressure == "Sell Pressure":
        action_now = "DO NOT BUY NOW"
    elif final_decision == "BUY" and rsi >= 76:
        action_now = "WAIT PULLBACK"
    elif final_decision == "BUY" and score >= 7:
        action_now = "BUY NOW"
    elif final_decision in {"BUY", "WATCH"} and change >= 1.5 and volume_status in {"Strong", "Very Strong"}:
        action_now = "BREAKOUT BUY"
    elif telegram_vote == "POSITIVE" and final_decision in {"WATCH", "BUY"}:
        action_now = "WATCH EARLY BUY"
    elif final_decision == "WATCH":
        action_now = "WATCH"
    else:
        action_now = "WAIT"

    if main_trend == "LONG BULLISH" and score >= 8:
        plan = "BUY & HOLD"
        target_pct = 12.0
    elif main_trend in {"LONG BULLISH", "SWING BULLISH"} and score >= 6:
        plan = "SWING TRADE"
        target_pct = 6.0
    elif score >= 5:
        plan = "SCALP ONLY"
        target_pct = 2.5
    else:
        plan = "WAIT"
        target_pct = 2.5

    buy_zone = None
    suggested_entry = None
    suggested_stop = None
    target_scalp = None
    target_swing = None
    target_long = None
    if last_price:
        zone_pct = 0.012 if volume_status in {"Strong", "Very Strong"} else 0.018
        low = last_price * (1 - zone_pct)
        high = last_price * (1 + zone_pct * 0.55)
        buy_zone = f"{low:.2f} - {high:.2f}"
        suggested_entry = round(min(last_price, high), 2)
        suggested_stop = round(suggested_entry * 0.965, 2)
        target_scalp = round(suggested_entry * 1.025, 2)
        target_swing = round(suggested_entry * 1.06, 2)
        target_long = round(suggested_entry * 1.12, 2)

    if action_now == "BUY NOW" and plan == "BUY & HOLD":
        advice = "Best long setup. Enter near buy zone only."
    elif action_now == "BUY NOW":
        advice = "Setup is active; use stop and targets."
    elif action_now == "WAIT PULLBACK":
        advice = "Setup is good, but price/RSI is stretched. Wait for buy zone."
    elif action_now == "WATCH EARLY BUY":
        advice = "Telegram attention and technicals deserve monitoring; wait for confirmation."
    elif action_now == "BREAKOUT BUY":
        advice = "Breakout-style setup; risk is higher after the jump."
    elif action_now == "DO NOT BUY NOW":
        advice = "Avoid new buy until pressure and trend improve."
    else:
        advice = "Wait for clearer setup."

    return {
        "smart_action_now": action_now,
        "smart_advice": advice,
        "smart_main_trend": main_trend,
        "smart_plan": plan,
        "smart_score_10": score,
        "smart_pressure": pressure,
        "smart_volume_status": volume_status,
        "smart_buy_zone": buy_zone,
        "smart_suggested_entry": suggested_entry,
        "smart_suggested_stop": suggested_stop,
        "smart_target_scalp": target_scalp,
        "smart_target_swing": target_swing,
        "smart_target_long": target_long,
        "smart_target_selected": target_pct,
        "tradingview_chart_url": tradingview_chart_url(symbol),
    }


def telegram_consensus(db: Session, lookback_days: int = 45) -> dict[str, dict[str, Any]]:
    since = datetime.utcnow() - timedelta(days=lookback_days)
    signals = db.scalars(
        select(ExtractedSignal)
        .where(ExtractedSignal.stock_symbol.is_not(None))
        .where(ExtractedSignal.created_at >= since)
    ).all()
    latest_final_by_symbol: dict[str, FinalAnalysis] = {}
    finals = db.scalars(select(FinalAnalysis).order_by(FinalAnalysis.created_at.desc())).all()
    for final in finals:
        latest_final_by_symbol.setdefault(final.symbol, final)

    source_trust = {
        source.id: source.trust_score
        for source in db.scalars(select(TelegramSource)).all()
    }
    grouped: dict[str, dict[str, Any]] = {}
    for signal in signals:
        symbol = signal.stock_symbol.upper()
        item = grouped.setdefault(
            symbol,
            {
                "telegram_signals": 0,
                "telegram_buy": 0,
                "telegram_watch": 0,
                "telegram_sell": 0,
                "telegram_hype": 0,
                "missing_stop": 0,
                "direction_score": 0.0,
                "trust_total": 0.0,
                "trust_count": 0,
            },
        )
        direction = (signal.direction or "").upper()
        item["telegram_signals"] += 1
        item["telegram_buy"] += 1 if direction == "BUY" else 0
        item["telegram_watch"] += 1 if direction in {"WATCH", "HOLD"} else 0
        item["telegram_sell"] += 1 if direction in {"SELL", "AVOID"} else 0
        item["telegram_hype"] += 1 if signal.hype_words else 0
        item["missing_stop"] += 1 if "missing_stop_loss" in (signal.risk_flags or []) else 0
        if direction == "BUY":
            item["direction_score"] += 12
        elif direction in {"WATCH", "HOLD"}:
            item["direction_score"] += 5
        elif direction in {"SELL", "AVOID"}:
            item["direction_score"] -= 12
        if signal.hype_words:
            item["direction_score"] -= 4
        if signal.source_id in source_trust:
            item["trust_total"] += source_trust[signal.source_id]
            item["trust_count"] += 1

    for symbol, item in grouped.items():
        latest = latest_final_by_symbol.get(symbol)
        avg_trust = item["trust_total"] / item["trust_count"] if item["trust_count"] else 50.0
        confidence_boost = ((latest.confidence_score if latest else 50.0) - 50.0) * 0.35
        trust_boost = (avg_trust - 50.0) * 0.15
        item["telegram_score"] = _bound(50.0 + item["direction_score"] + confidence_boost + trust_boost)
        item["telegram_vote"] = _telegram_vote(item["telegram_score"], item["telegram_buy"], item["telegram_sell"])
        item["latest_analysis_decision"] = latest.final_decision if latest else None
        item["latest_analysis_confidence"] = latest.confidence_score if latest else None
        item["avg_source_trust"] = round(avg_trust, 2)
    return grouped


def build_final_recommendations(db: Session, settings: Settings | None = None, limit: int = 500) -> ScreenerRun:
    settings = settings or get_settings()
    stocks = db.scalars(select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.symbol)).all()
    symbols = [stock.symbol for stock in stocks]
    stock_by_symbol = {stock.symbol: stock for stock in stocks}
    consensus = telegram_consensus(db)

    provider = TradingViewScreenerProvider(settings)
    try:
        tv_df = provider._scan(symbols=symbols, limit=max(limit, len(symbols)))
        provider_status = "available"
        provider_warning = None
    except Exception as exc:
        tv_df = pd.DataFrame()
        provider_status = "unavailable"
        provider_warning = str(exc)

    rows: list[dict[str, Any]] = []
    if tv_df.empty:
        for symbol, item in consensus.items():
            stock = stock_by_symbol.get(symbol)
            final_score = item["telegram_score"] * 0.75
            warnings = ["TradingView screener unavailable; recommendation uses Telegram consensus only."]
            rows.append(
                {
                    "symbol": symbol,
                    "name": stock.name_en if stock else symbol,
                    "sector": stock.sector if stock else None,
                    "last_price": None,
                    "change_percent": None,
                    "volume": None,
                    "rsi": None,
                    "tv_recommend_all": None,
                    "tv_vote": "UNAVAILABLE",
                    "telegram_vote": item["telegram_vote"],
                    "telegram_signals": item["telegram_signals"],
                    "telegram_buy": item["telegram_buy"],
                    "telegram_watch": item["telegram_watch"],
                    "telegram_sell": item["telegram_sell"],
                    "telegram_hype": item["telegram_hype"],
                    "missing_stop": item["missing_stop"],
                    "final_score": round(final_score, 2),
                    "final_recommendation": "WATCH" if final_score >= 55 else "NEUTRAL",
                    **_smart_pro_overlay(
                        symbol=symbol,
                        last_price=None,
                        rec_all=0.0,
                        rec_ma=0.0,
                        rsi=50.0,
                        change=0.0,
                        liquidity_rank=0.0,
                        final_score=final_score,
                        final_decision="WATCH" if final_score >= 55 else "NEUTRAL",
                        telegram_vote=item["telegram_vote"],
                    ),
                    "reasons": ["Telegram consensus evaluated without live TradingView confirmation."],
                    "warnings": warnings,
                    "disclaimer": DISCLAIMER,
                }
            )
        return ScreenerRun(rows=rows, provider="tradingview_screener", provider_status=provider_status, provider_warning=provider_warning)

    tv_df = tv_df.copy()
    for col in ["close", "change_percent", "volume", "RSI", "Recommend.All", "Recommend.MA", "Recommend.Other"]:
        if col in tv_df.columns:
            tv_df[col] = pd.to_numeric(tv_df[col], errors="coerce")
    volume_rank = tv_df["volume"].rank(pct=True).fillna(0.0) if "volume" in tv_df.columns else pd.Series([0.0] * len(tv_df))

    for idx, row in tv_df.iterrows():
        symbol = str(row.get("symbol", "")).upper()
        if symbol not in stock_by_symbol:
            continue
        stock = stock_by_symbol[symbol]
        rec_all = _num(row.get("Recommend.All"))
        rec_ma = _num(row.get("Recommend.MA"))
        rec_other = _num(row.get("Recommend.Other"))
        rsi = _num(row.get("RSI"), 50.0)
        change = _num(row.get("change_percent"))
        liquidity = _num(volume_rank.loc[idx] if idx in volume_rank.index else 0.0)
        tv_score = 50 + rec_all * 32 + rec_ma * 9 + rec_other * 5 + max(-8, min(8, change * 1.2)) + liquidity * 8
        if 45 <= rsi <= 68:
            tv_score += 5
        elif rsi < 30:
            tv_score += 2
        elif rsi > 78:
            tv_score -= 12
        tv_score = _bound(tv_score)

        item = consensus.get(
            symbol,
            {
                "telegram_score": 50.0,
                "telegram_vote": "NONE",
                "telegram_signals": 0,
                "telegram_buy": 0,
                "telegram_watch": 0,
                "telegram_sell": 0,
                "telegram_hype": 0,
                "missing_stop": 0,
            },
        )
        final_score = _bound(tv_score * 0.68 + item["telegram_score"] * 0.32)
        tv_action = _tv_vote(rec_all, rsi)

        reasons: list[str] = []
        warnings: list[str] = []
        if tv_action in {"STRONG_BUY", "BUY"}:
            reasons.append(f"TradingView technical vote is {tv_action}.")
        if item["telegram_vote"] == "POSITIVE":
            reasons.append("Telegram consensus is positive.")
        elif item["telegram_vote"] == "MIXED":
            reasons.append("Telegram has recent attention but the signal quality is mixed.")
        if liquidity >= 0.8:
            reasons.append("Volume ranks in the top 20% of the current screener.")
        if rsi >= 78:
            warnings.append("RSI is overbought; chase risk is elevated.")
        if item["telegram_hype"] >= 2:
            warnings.append("Telegram hype is high")
        if item["missing_stop"] >= 1:
            warnings.append("Some Telegram signals were missing stop loss.")
        if item["telegram_sell"] > item["telegram_buy"]:
            warnings.append("Telegram consensus contains more sell/avoid than buy signals.")
        if not reasons:
            reasons.append("No strong confirmation; keep on watchlist only.")

        final_decision = _final_decision(final_score, tv_action, item["telegram_vote"], rsi, warnings)
        last_price = _num(row.get("close"), None)
        rows.append(
            {
                "symbol": symbol,
                "name": stock.name_en or row.get("description") or symbol,
                "sector": stock.sector,
                "last_price": last_price,
                "change_percent": round(change, 2),
                "volume": _num(row.get("volume"), None),
                "rsi": round(rsi, 2),
                "tv_recommend_all": round(rec_all, 3),
                "tv_recommend_ma": round(rec_ma, 3),
                "tv_recommend_other": round(rec_other, 3),
                "tv_score": tv_score,
                "tv_vote": tv_action,
                "telegram_score": item["telegram_score"],
                "telegram_vote": item["telegram_vote"],
                "telegram_signals": item["telegram_signals"],
                "telegram_buy": item["telegram_buy"],
                "telegram_watch": item["telegram_watch"],
                "telegram_sell": item["telegram_sell"],
                "telegram_hype": item["telegram_hype"],
                "missing_stop": item["missing_stop"],
                "latest_analysis_decision": item.get("latest_analysis_decision"),
                "latest_analysis_confidence": item.get("latest_analysis_confidence"),
                "final_score": final_score,
                "final_recommendation": final_decision,
                **_smart_pro_overlay(
                    symbol=symbol,
                    last_price=last_price,
                    rec_all=rec_all,
                    rec_ma=rec_ma,
                    rsi=rsi,
                    change=change,
                    liquidity_rank=liquidity,
                    final_score=final_score,
                    final_decision=final_decision,
                    telegram_vote=item["telegram_vote"],
                ),
                "reasons": reasons,
                "warnings": warnings,
                "disclaimer": DISCLAIMER,
            }
        )

    rows.sort(key=lambda item: (item["final_score"], item["telegram_signals"], item.get("volume") or 0), reverse=True)
    return ScreenerRun(rows=rows, provider="tradingview_screener", provider_status=provider_status, provider_warning=provider_warning)
