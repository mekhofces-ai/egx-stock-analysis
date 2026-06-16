from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.market_data import get_ohlcv, latest_price
from app.models import (
    DailyEGXReportRow,
    FinalStockDecision,
    LiquiditySnapshot,
    MarketRegimeSnapshot,
    NoTradeReason,
    SectorAnalysisSnapshot,
    Stock,
    TradingViewScreeningResult,
    TradingViewScreeningRun,
)
from app.services.dynamic_settings import get_float


INDEX_SYMBOLS = ["EGX30", "EGX70", "EGX100"]


def _bound(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _latest_tv_run(db: Session):
    return db.scalar(select(TradingViewScreeningRun).order_by(TradingViewScreeningRun.created_at.desc()))


def analyze_market_regime(db: Session, *, persist: bool = True) -> dict[str, Any]:
    index_symbol = next((symbol for symbol in INDEX_SYMBOLS if not get_ohlcv(db, symbol, limit=260).empty), "EGX30")
    df = get_ohlcv(db, index_symbol, limit=260)
    if not df.empty and len(df) >= 50:
        close = df["close"]
        sma20 = close.rolling(20).mean().iloc[-1]
        sma50 = close.rolling(50).mean().iloc[-1]
        returns = close.pct_change().dropna()
        volatility = float(returns.tail(20).std() * (252 ** 0.5) * 100) if not returns.empty else 0.0
        trend_score = 70.0 if close.iloc[-1] > sma20 > sma50 else 30.0 if close.iloc[-1] < sma20 < sma50 else 50.0
        volatility_score = _bound(100 - volatility * 2)
        regime = "bullish" if trend_score >= 65 else "bearish" if trend_score <= 35 else "sideways"
        if volatility >= 35:
            regime = "high_volatility"
        market_score = _bound(trend_score * 0.7 + volatility_score * 0.3)
        reason = f"{index_symbol} trend score {trend_score:.0f}, volatility {volatility:.1f}%."
    else:
        run = _latest_tv_run(db)
        tv_rows = []
        if run:
            tv_rows = db.scalars(select(TradingViewScreeningResult).where(TradingViewScreeningResult.run_id == run.id)).all()
        avg_score = sum(float(row.final_score or 50) for row in tv_rows) / len(tv_rows) if tv_rows else 50.0
        bullish = sum(1 for row in tv_rows if str(row.recommendation or "").upper() in {"BUY", "STRONG BUY", "WATCH"})
        bearish = sum(1 for row in tv_rows if str(row.recommendation or "").upper() in {"SELL", "AVOID", "HIGH_RISK"})
        regime = "bullish" if bullish > bearish * 1.4 and avg_score >= 58 else "bearish" if bearish > bullish * 1.2 and avg_score <= 45 else "sideways"
        trend_score = _bound(avg_score)
        volatility_score = None
        market_score = _bound(avg_score)
        reason = "Derived from latest TradingView screening breadth because no EGX index OHLCV exists."
    payload = {
        "index_symbol": index_symbol,
        "regime": regime,
        "trend_score": trend_score,
        "volatility_score": volatility_score,
        "market_score": market_score,
        "reason": reason,
    }
    if persist:
        db.add(MarketRegimeSnapshot(**payload))
    return payload


def analyze_sector_strength(db: Session, symbol: str, *, persist: bool = True) -> dict[str, Any]:
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
    sector = stock.sector if stock and stock.sector else "Unknown"
    peers = db.scalars(select(Stock.symbol).where(Stock.sector == sector, Stock.is_active.is_(True))).all() if sector != "Unknown" else []
    scores: list[float] = []
    for peer in peers:
        latest = db.scalar(select(FinalStockDecision).where(FinalStockDecision.symbol == peer).order_by(FinalStockDecision.decision_date.desc()))
        if latest and latest.final_score is not None:
            scores.append(float(latest.final_score))
        else:
            report = db.scalar(select(DailyEGXReportRow).where(DailyEGXReportRow.symbol == peer, DailyEGXReportRow.report_score.is_not(None)).order_by(DailyEGXReportRow.created_at.desc()))
            if report:
                scores.append(float(report.report_score or 50))
    sector_score = _bound(sum(scores) / len(scores)) if scores else 50.0
    own_latest = db.scalar(select(FinalStockDecision).where(FinalStockDecision.symbol == symbol).order_by(FinalStockDecision.decision_date.desc()))
    own_score = float(own_latest.final_score or sector_score) if own_latest else sector_score
    relative = _bound(50 + (own_score - sector_score))
    status = "outperforming" if relative >= 60 else "underperforming" if relative < 40 else "in_line"
    reason = f"{symbol} is {status} versus {sector} sector. Sector score {sector_score:.0f}, relative score {relative:.0f}."
    if persist:
        top_symbols = peers[:5]
        db.add(
            SectorAnalysisSnapshot(
                sector=sector,
                sector_score=sector_score,
                benchmark_score=relative,
                regime=status,
                top_symbols=top_symbols,
                weak_symbols=[],
                reason=reason,
            )
        )
    return {"sector": sector, "sector_score": sector_score, "relative_score": relative, "status": status, "reason": reason}


def liquidity_score(db: Session, symbol: str, *, persist: bool = True) -> dict[str, Any]:
    threshold = get_float(db, "liquidity_min_score", 35.0)
    df = get_ohlcv(db, symbol, limit=60)
    price = latest_price(db, symbol)
    if not df.empty and "volume" in df:
        tail = df.tail(20)
        avg_volume = float(tail["volume"].mean()) if not tail.empty else None
        avg_close = float(tail["close"].mean()) if not tail.empty else price
        avg_value = (avg_volume or 0) * (avg_close or 0)
    else:
        run = _latest_tv_run(db)
        row = None
        if run:
            row = db.scalar(select(TradingViewScreeningResult).where(TradingViewScreeningResult.run_id == run.id, TradingViewScreeningResult.symbol == symbol))
        avg_volume = float(row.volume) if row and row.volume is not None else None
        avg_value = avg_volume * float(row.close or price or 0) if avg_volume else None
    if avg_value is None:
        score = 50.0
        status = "unknown"
        reason = "No volume/value data available; liquidity is treated as neutral until OHLCV or screener volume is imported."
    else:
        score = _bound((avg_value / 1_000_000) * 30 + (avg_volume or 0) / 500_000 * 25)
        status = "ok" if score >= threshold else "weak"
        reason = f"Average value traded {avg_value:,.0f}; liquidity score {score:.0f}, threshold {threshold:.0f}."
    payload = {
        "symbol": symbol,
        "avg_volume": avg_volume,
        "avg_value_traded": avg_value,
        "liquidity_score": score,
        "threshold": threshold,
        "status": status,
        "reason": reason,
    }
    if persist:
        db.add(LiquiditySnapshot(**payload))
    return payload


def build_no_trade_reasons(
    *,
    final_score: float,
    final_signal: str,
    risk_level: str | None,
    market: dict[str, Any],
    sector: dict[str, Any],
    liquidity: dict[str, Any],
    scores: dict[str, float],
) -> list[str]:
    reasons: list[str] = []
    if final_score < 65:
        reasons.append("final score below buy threshold")
    if liquidity.get("status") == "weak":
        reasons.append("weak liquidity")
    if market.get("regime") == "bearish":
        reasons.append("bad market regime")
    if market.get("regime") == "high_volatility":
        reasons.append("high market volatility")
    if sector.get("status") == "underperforming":
        reasons.append("stock is underperforming sector")
    if risk_level == "HIGH":
        reasons.append("risk level is high")
    if scores.get("news", 50) < 40:
        reasons.append("news conflict")
    if scores.get("financial", 50) < 40:
        reasons.append("financial weakness")
    if scores.get("telegram", 50) < 40:
        reasons.append("Telegram signal unreliable")
    if final_signal not in {"STRONG BUY", "BUY"} and not reasons:
        reasons.append("risk/reward not good enough")
    return reasons


def apply_risk_quality_filters(
    db: Session,
    symbol: str,
    final_score: float,
    final_signal: str,
    risk_level: str | None,
    scores: dict[str, float],
    *,
    persist: bool = True,
) -> dict[str, Any]:
    market = analyze_market_regime(db, persist=persist)
    sector = analyze_sector_strength(db, symbol, persist=persist)
    liquidity = liquidity_score(db, symbol, persist=persist)
    adjusted = final_score
    if market["regime"] == "bearish":
        adjusted -= get_float(db, "market_regime_buy_penalty", 10.0)
    elif market["regime"] == "bullish":
        adjusted += 2
    elif market["regime"] == "high_volatility":
        adjusted -= 8
    sector_adjustment = ((sector.get("relative_score") or 50) - 50) * (get_float(db, "sector_strength_weight", 5.0) / 50)
    adjusted += sector_adjustment
    adjusted = _bound(adjusted)
    threshold = get_float(db, "liquidity_min_score", 35.0)
    signal = final_signal
    if liquidity.get("liquidity_score") is not None and float(liquidity["liquidity_score"]) < threshold and signal in {"STRONG BUY", "BUY"}:
        signal = "WATCH"
    if market["regime"] in {"bearish", "high_volatility"} and signal == "STRONG BUY":
        signal = "BUY"
    if adjusted < 65 and signal in {"STRONG BUY", "BUY"}:
        signal = "WATCH"
    no_trade = build_no_trade_reasons(
        final_score=adjusted,
        final_signal=signal,
        risk_level=risk_level,
        market=market,
        sector=sector,
        liquidity=liquidity,
        scores=scores,
    )
    if signal in {"STRONG BUY", "BUY"}:
        no_trade = []
    if persist and no_trade:
        db.add(
            NoTradeReason(
                symbol=symbol,
                final_score=adjusted,
                final_signal=signal,
                reasons_json=no_trade,
                reason_text=", ".join(no_trade),
            )
        )
    return {
        "final_score": adjusted,
        "final_signal": signal,
        "market": market,
        "sector": sector,
        "liquidity": liquidity,
        "no_trade_reasons": no_trade,
        "reason": "; ".join([market["reason"], sector["reason"], liquidity["reason"]]),
    }

