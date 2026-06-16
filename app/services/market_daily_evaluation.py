from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import REPORT_TIMEZONE, RISK_NOTE
from app.data.market_data import get_ohlcv
from app.database import SessionLocal, init_db
from app.models import (
    MarketDailyEvaluation,
    MarketPrice,
    Stock,
    StockNews,
    TelegramMessageSymbol,
    TradingViewScreeningResult,
    TradingViewScreeningRun,
)


CAIRO_TZ = ZoneInfo(REPORT_TIMEZONE)
EGX_OPEN = time(10, 0)
EGX_CLOSE = time(14, 30)

REGIMES = {
    "STRONG_BULLISH",
    "BULLISH",
    "NEUTRAL",
    "WEAK",
    "BEARISH",
    "HIGH_RISK",
    "MARKET_CLOSED",
    "DATA_INSUFFICIENT",
}

PERMISSIONS = {
    "TRADE_ALLOWED",
    "WATCH_ONLY",
    "BUY_BLOCKED",
    "SELL_ONLY",
    "NO_TRADING",
    "DATA_INSUFFICIENT",
}


def cairo_now() -> datetime:
    return datetime.now(CAIRO_TZ)


def market_is_open(now: datetime | None = None) -> bool:
    now = now or cairo_now()
    if now.weekday() in {4, 5}:  # Friday, Saturday
        return False
    local_time = now.time()
    return EGX_OPEN <= local_time <= EGX_CLOSE


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _latest_screening_rows(db: Session) -> list[TradingViewScreeningResult]:
    run = db.scalar(select(TradingViewScreeningRun).order_by(TradingViewScreeningRun.created_at.desc()))
    if not run:
        return []
    return list(db.scalars(select(TradingViewScreeningResult).where(TradingViewScreeningResult.run_id == run.id)).all())


def _breadth_from_market_prices(db: Session, day: date) -> dict[str, Any]:
    start, end = _day_bounds(day)
    rows = db.scalars(
        select(MarketPrice)
        .where(MarketPrice.timestamp >= start, MarketPrice.timestamp < end)
        .order_by(MarketPrice.symbol.asc(), MarketPrice.timestamp.asc())
    ).all()
    grouped: dict[str, list[MarketPrice]] = {}
    for row in rows:
        grouped.setdefault(row.symbol, []).append(row)
    advancing = declining = unchanged = 0
    total_volume = 0.0
    volatility_samples: list[float] = []
    for symbol_rows in grouped.values():
        first = symbol_rows[0]
        last = symbol_rows[-1]
        if first.close is None or last.close is None or not first.close:
            continue
        change = (float(last.close) - float(first.close)) / float(first.close) * 100
        if change > 0.25:
            advancing += 1
        elif change < -0.25:
            declining += 1
        else:
            unchanged += 1
        highs = [float(r.high) for r in symbol_rows if r.high is not None]
        lows = [float(r.low) for r in symbol_rows if r.low is not None]
        closes = [float(r.close) for r in symbol_rows if r.close is not None]
        volumes = [float(r.volume or 0) for r in symbol_rows]
        total_volume += sum(volumes)
        if highs and lows and closes and closes[0]:
            volatility_samples.append((max(highs) - min(lows)) / closes[0] * 100)
    return {
        "source": "market_price",
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,
        "symbols": len(grouped),
        "total_volume": total_volume,
        "avg_range_pct": sum(volatility_samples) / len(volatility_samples) if volatility_samples else None,
    }


def _breadth_from_screening(db: Session) -> dict[str, Any]:
    rows = _latest_screening_rows(db)
    advancing = declining = unchanged = 0
    total_volume = 0.0
    scores: list[float] = []
    for row in rows:
        rec = str(row.recommendation or "").upper()
        score = float(row.final_score or 50)
        scores.append(score)
        total_volume += float(row.volume or 0)
        if rec in {"STRONG BUY", "BUY"} or score >= 60:
            advancing += 1
        elif rec in {"SELL", "AVOID", "HIGH_RISK"} or score < 45:
            declining += 1
        else:
            unchanged += 1
    return {
        "source": "tradingview_screening",
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,
        "symbols": len(rows),
        "total_volume": total_volume,
        "avg_score": sum(scores) / len(scores) if scores else None,
        "avg_range_pct": None,
    }


def _index_trend_score(db: Session) -> tuple[float | None, str]:
    for symbol in ["EGX30", "EGX70", "EGX100"]:
        frame = get_ohlcv(db, symbol, limit=260)
        if frame.empty or len(frame) < 50:
            continue
        close = frame["close"]
        sma20 = close.rolling(20).mean().iloc[-1]
        sma50 = close.rolling(50).mean().iloc[-1]
        last = close.iloc[-1]
        if last > sma20 > sma50:
            return 75.0, f"{symbol} is above SMA20 and SMA50."
        if last < sma20 < sma50:
            return 30.0, f"{symbol} is below SMA20 and SMA50."
        return 52.0, f"{symbol} trend is mixed."
    return None, "No EGX index OHLCV available."


def _sentiment_scores(db: Session, day: date) -> tuple[float, float, str]:
    start, end = _day_bounds(day)
    mentions = int(
        db.scalar(
            select(func.count()).select_from(TelegramMessageSymbol).where(
                TelegramMessageSymbol.created_at >= start,
                TelegramMessageSymbol.created_at < end,
            )
        )
        or 0
    )
    telegram_score = _clamp(50 + min(mentions, 60) * 0.5)
    news_rows = db.scalars(select(StockNews).where(StockNews.published_at >= start, StockNews.published_at < end).limit(200)).all()
    if news_rows:
        avg_news = sum(float(row.sentiment_score or 0) for row in news_rows) / len(news_rows)
        news_score = _clamp(50 + avg_news * 50)
    else:
        news_score = 50.0
    reason = f"Telegram mentions today: {mentions}; news items today: {len(news_rows)}."
    return telegram_score, news_score, reason


def _regime_and_permission(score: float, *, open_now: bool, high_volatility: bool, insufficient: bool) -> tuple[str, str]:
    if not open_now:
        return "MARKET_CLOSED", "NO_TRADING"
    if insufficient:
        return "DATA_INSUFFICIENT", "DATA_INSUFFICIENT"
    if high_volatility:
        return "HIGH_RISK", "SELL_ONLY"
    if score >= 75:
        return "STRONG_BULLISH", "TRADE_ALLOWED"
    if score >= 60:
        return "BULLISH", "TRADE_ALLOWED"
    if score >= 45:
        return "NEUTRAL", "WATCH_ONLY"
    if score >= 35:
        return "WEAK", "BUY_BLOCKED"
    return "BEARISH", "NO_TRADING"


def evaluate_daily_market(
    db: Session,
    *,
    target_date: date | None = None,
    persist: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    local_now = now or cairo_now()
    day = target_date or local_now.date()
    open_now = market_is_open(local_now if target_date is None else datetime.combine(day, EGX_OPEN, tzinfo=CAIRO_TZ))
    breadth = _breadth_from_market_prices(db, day)
    if breadth["symbols"] < 5:
        breadth = _breadth_from_screening(db)
    trend_score, trend_reason = _index_trend_score(db)
    total_breadth = int(breadth["advancing"] + breadth["declining"] + breadth["unchanged"])
    insufficient = total_breadth < 5 and trend_score is None
    if total_breadth:
        breadth_score = _clamp(50 + ((breadth["advancing"] - breadth["declining"]) / total_breadth) * 45)
    else:
        breadth_score = float(breadth.get("avg_score") or 50)
    volume_score = _clamp(45 + min(float(breadth.get("total_volume") or 0) / 5_000_000, 10) * 5)
    avg_range = breadth.get("avg_range_pct")
    volatility_score = _clamp(100 - float(avg_range or 3) * 12)
    high_volatility = bool(avg_range is not None and float(avg_range) >= 5.0)
    telegram_score, news_score, sentiment_reason = _sentiment_scores(db, day)
    base_trend = trend_score if trend_score is not None else breadth_score
    market_score = _clamp(
        base_trend * 0.30
        + breadth_score * 0.25
        + volume_score * 0.15
        + volatility_score * 0.15
        + telegram_score * 0.075
        + news_score * 0.075
    )
    if high_volatility:
        market_score = _clamp(market_score - 15)
    regime, permission = _regime_and_permission(
        market_score,
        open_now=open_now,
        high_volatility=high_volatility,
        insufficient=insufficient,
    )
    warnings: list[str] = []
    if insufficient:
        warnings.append("Insufficient breadth/index data; live BUY trading must remain blocked.")
    if not open_now:
        warnings.append("Market is closed by Cairo schedule; live trading is blocked.")
    if high_volatility:
        warnings.append("High intraday range detected; new BUY trades are blocked.")
    if breadth.get("source") == "tradingview_screening":
        warnings.append("Market breadth uses latest TradingView screening fallback.")
    explanation = (
        f"Market score {market_score:.0f}/100. Breadth {breadth['advancing']} advancing, "
        f"{breadth['declining']} declining, {breadth['unchanged']} unchanged. "
        f"{trend_reason} {sentiment_reason}"
    )
    payload = {
        "evaluation_date": day.isoformat(),
        "market_status": "open" if open_now else "closed",
        "market_score": market_score,
        "market_regime": regime,
        "trade_permission": permission,
        "advancing_stocks": int(breadth["advancing"]),
        "declining_stocks": int(breadth["declining"]),
        "unchanged_stocks": int(breadth["unchanged"]),
        "volume_score": volume_score,
        "volatility_score": volatility_score,
        "liquidity_score": volume_score,
        "news_score": news_score,
        "telegram_score": telegram_score,
        "sector_summary": {"breadth_source": breadth.get("source"), "symbols": total_breadth},
        "warnings": warnings,
        "explanation": explanation,
        "details": {
            "breadth": breadth,
            "trend_score": trend_score,
            "trend_reason": trend_reason,
            "risk_note": RISK_NOTE,
        },
    }
    if persist:
        start, end = _day_bounds(day)
        existing = db.scalar(
            select(MarketDailyEvaluation).where(
                MarketDailyEvaluation.evaluation_date >= start,
                MarketDailyEvaluation.evaluation_date < end,
            )
        )
        row = existing or MarketDailyEvaluation(evaluation_date=datetime(day.year, day.month, day.day))
        row.market_status = payload["market_status"]
        row.market_score = payload["market_score"]
        row.market_regime = payload["market_regime"]
        row.trade_permission = payload["trade_permission"]
        row.advancing_stocks = payload["advancing_stocks"]
        row.declining_stocks = payload["declining_stocks"]
        row.unchanged_stocks = payload["unchanged_stocks"]
        row.volume_score = payload["volume_score"]
        row.volatility_score = payload["volatility_score"]
        row.liquidity_score = payload["liquidity_score"]
        row.news_score = payload["news_score"]
        row.telegram_score = payload["telegram_score"]
        row.sector_summary_json = payload["sector_summary"]
        row.warnings_json = payload["warnings"]
        row.explanation = payload["explanation"]
        row.details_json = payload["details"]
        db.add(row)
        db.flush()
        payload["id"] = row.id
    return payload


def latest_market_evaluation(db: Session, *, target_date: date | None = None) -> dict[str, Any] | None:
    if target_date:
        start, end = _day_bounds(target_date)
        row = db.scalar(
            select(MarketDailyEvaluation)
            .where(MarketDailyEvaluation.evaluation_date >= start, MarketDailyEvaluation.evaluation_date < end)
            .order_by(MarketDailyEvaluation.updated_at.desc())
        )
    else:
        row = db.scalar(select(MarketDailyEvaluation).order_by(MarketDailyEvaluation.evaluation_date.desc(), MarketDailyEvaluation.updated_at.desc()))
    if not row:
        return None
    return {
        "id": row.id,
        "evaluation_date": row.evaluation_date.date().isoformat(),
        "market_status": row.market_status,
        "market_score": row.market_score,
        "market_regime": row.market_regime,
        "trade_permission": row.trade_permission,
        "advancing_stocks": row.advancing_stocks,
        "declining_stocks": row.declining_stocks,
        "unchanged_stocks": row.unchanged_stocks,
        "warnings": row.warnings_json or [],
        "explanation": row.explanation,
    }


def _parse_date(value: str | None) -> date | None:
    if not value or value.lower() == "today":
        return None
    return date.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate EGX daily market condition.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    init_db(seed=True)
    with SessionLocal() as db:
        result = evaluate_daily_market(db, target_date=_parse_date(args.date), persist=True)
        db.commit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Market score: {result['market_score']}/100")
        print(f"Regime: {result['market_regime']}")
        print(f"Permission: {result['trade_permission']}")
        print(result["explanation"])
        if result["warnings"]:
            print("Warnings:")
            for warning in result["warnings"]:
                print(f"- {warning}")


if __name__ == "__main__":
    main()
