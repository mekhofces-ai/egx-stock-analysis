from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import (
    DAILY_REPORT_TIMES,
    DEFAULT_COMMISSION_RATE,
    DEFAULT_SLIPPAGE_RATE,
    RECOMMENDATION_TOP_N,
    RECOMMENDATION_WEIGHTS,
    REPORT_TIMEZONE,
    RISK_NOTE,
    get_settings,
)
from app.data.market_data import get_ohlcv, latest_price
from app.database import SessionLocal, init_db, run_with_db_retry, sqlite_write_lock
from app.models import (
    FinalStockDecision,
    LiquiditySnapshot,
    NewsSignal,
    RecommendationItem,
    RecommendationReport,
    Stock,
    StockNews,
    StrategyBacktestSummary,
    StrategyCliV6Result,
    StrategyResult,
    StrategySignal,
    TelegramMessage,
    TelegramMessageSymbol,
    TelegramSignal,
)
from app.news.news_engine import analyze_news
from app.services.recommendation_validation import apply_validation_to_row
from app.technical.indicators import add_indicators
from app.technical.support_resistance import support_resistance
from app.technical.technical_engine import analyze_technical


logger = logging.getLogger(__name__)
REPORT_DISCLAIMER = "This is an analytical report, not financial advice. Always manage your risk."
REPORT_SOURCE = "EGX Daily Stock Report"
ARROW_UP = "\u25b2"
ARROW_DOWN = "\u25bc"
CHART_ICON = "\U0001f4ca"
FIRE_ICON = "\U0001f525"
CLOCK_ICON = "\U0001f558"
DATE_ICON = "\U0001f4c5"
WARNING_ICON = "\u26a0\ufe0f"
OK_ICON = "\u2705"


def cairo_now() -> datetime:
    return datetime.now(ZoneInfo(REPORT_TIMEZONE))


def clamp(value: float | int | None, low: float = 0.0, high: float = 100.0) -> float:
    try:
        number = float(value if value is not None else 0.0)
    except Exception:
        number = 0.0
    return round(max(low, min(high, number)), 2)


def normalize_score(value: float | int | None, default: float = 50.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except Exception:
        return default
    if -1.0 <= number <= 1.0:
        number *= 100.0
    return clamp(number)


def calculate_final_score(scores: dict[str, float | int | None], weights: dict[str, float] | None = None) -> float:
    weights = weights or RECOMMENDATION_WEIGHTS
    total = 0.0
    weight_sum = 0.0
    for key, weight in weights.items():
        total += normalize_score(scores.get(key), default=50.0) * float(weight)
        weight_sum += float(weight)
    if weight_sum <= 0:
        return 50.0
    return round(total / weight_sum, 2)


def recommendation_signal(
    final_score: float,
    *,
    entry_valid: bool,
    risk_reward: float | None,
    backtest_score: float,
    risk_liquidity_score: float,
) -> str:
    if not entry_valid or risk_liquidity_score < 60 or backtest_score < 60:
        return "WATCH ONLY" if final_score >= 55 else "AVOID"
    if risk_reward is not None and risk_reward < 1.8:
        return "WATCH ONLY" if final_score >= 55 else "AVOID"
    if final_score >= 78:
        return "CONDITIONAL BUY"
    if final_score >= 62:
        return "WATCH ONLY"
    return "AVOID"


def _as_percent(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if -1.0 <= number <= 1.0:
        return number * 100.0
    return number


def _latest(db: Session, model: type[Any], symbol: str, date_column: Any) -> Any | None:
    return db.scalar(select(model).where(model.symbol == symbol).order_by(date_column.desc(), model.id.desc()))


def _signal_to_score(signal: str | None, confidence: float | None = None, fallback: float = 50.0) -> float:
    label = str(signal or "").upper().strip()
    mapping = {
        "STRONG BUY": 92.0,
        "BUY": 80.0,
        "BULLISH": 75.0,
        "WEAK BUY": 68.0,
        "WATCH": 60.0,
        "HOLD": 50.0,
        "NEUTRAL": 50.0,
        "WATCH ONLY": 55.0,
        "WEAK SELL": 35.0,
        "BEARISH": 30.0,
        "SELL": 22.0,
        "STRONG SELL": 12.0,
        "AVOID": 25.0,
    }
    base = mapping.get(label, fallback)
    if confidence is None:
        return clamp(base)
    return clamp((base * 0.65) + (normalize_score(confidence, default=base) * 0.35))


def _message_date(row: TelegramMessageSymbol, message: TelegramMessage | None) -> datetime:
    if message and message.message_date:
        return message.message_date
    if message and message.created_at:
        return message.created_at
    return row.created_at


def get_stock_news(db: Session, symbol: str, limit: int = 20) -> list[StockNews]:
    return db.scalars(
        select(StockNews)
        .where(StockNews.symbol == symbol)
        .order_by(StockNews.published_at.desc().nullslast(), StockNews.created_at.desc(), StockNews.id.desc())
        .limit(limit)
    ).all()


def analyze_news_sentiment(news_items: Iterable[StockNews]) -> dict[str, Any]:
    rows = list(news_items)
    if not rows:
        return {"news_score": 50.0, "news_signal": "NEUTRAL", "reason": "No recent news found", "drivers": []}
    weighted: list[float] = []
    drivers: list[str] = []
    for row in rows:
        sentiment = float(row.sentiment_score or 0.0)
        impact = max(10.0, float(row.impact_score or 25.0))
        weighted.append(sentiment * (impact / 100.0))
        label = row.title or row.body or row.source or "news"
        drivers.append(str(label).replace("\n", " ")[:120])
    score = clamp(50.0 + (sum(weighted) / max(1, len(weighted))))
    signal = "BULLISH" if score >= 65 else "BEARISH" if score < 40 else "NEUTRAL"
    return {"news_score": score, "news_signal": signal, "reason": f"{len(rows)} recent news item(s) analyzed.", "drivers": drivers[:5]}


def telegram_score(db: Session, symbol: str, *, now: datetime | None = None, days: int = 7) -> dict[str, Any]:
    now = now or datetime.utcnow()
    since = now - timedelta(days=days)
    pairs = db.execute(
        select(TelegramMessageSymbol, TelegramMessage)
        .outerjoin(TelegramMessage, TelegramMessageSymbol.telegram_message_id == TelegramMessage.id)
        .where(TelegramMessageSymbol.symbol == symbol, TelegramMessageSymbol.created_at >= since)
        .order_by(TelegramMessageSymbol.created_at.desc())
        .limit(250)
    ).all()
    latest_signal = _latest(db, TelegramSignal, symbol, TelegramSignal.signal_date)
    if not pairs and not latest_signal:
        return {"score": 50.0, "label": "Neutral", "reason": "No recent Telegram mentions found."}

    positive_words = {"buy", "strong buy", "positive", "bullish", "target", "breakout", "support", "accumulate"}
    negative_words = {"sell", "avoid", "negative", "bearish", "warning", "stop", "breakdown", "risk"}
    mentions = len(pairs)
    positive = 0
    negative = 0
    image_mentions = 0
    channels: set[str] = set()
    latest_dt: datetime | None = None
    for row, message in pairs:
        text = " ".join(
            str(value or "")
            for value in [
                row.intent,
                row.reason,
                row.source,
                message.sentiment if message else "",
                message.recommendation_type if message else "",
                message.message_type if message else "",
                message.text if message else "",
                message.message_text if message else "",
                message.image_text if message else "",
            ]
        ).lower()
        if any(word in text for word in positive_words):
            positive += 1
        if any(word in text for word in negative_words):
            negative += 1
        if message and (message.has_image or message.image_text or message.media_type in {"photo", "image", "jpg", "jpeg", "png"}):
            image_mentions += 1
        if message and message.channel_name:
            channels.add(str(message.channel_name))
        msg_dt = _message_date(row, message)
        latest_dt = max(latest_dt, msg_dt) if latest_dt else msg_dt

    mention_component = min(25.0, math.sqrt(max(mentions, 0)) * 8.0)
    sentiment_component = 0.0
    if mentions:
        sentiment_component = ((positive - negative) / max(1, mentions)) * 22.0
    recency_component = 0.0
    if latest_dt:
        age_hours = max(0.0, (now - latest_dt).total_seconds() / 3600.0)
        recency_component = max(0.0, 15.0 - min(15.0, age_hours / 8.0))
    image_component = min(8.0, image_mentions * 4.0)
    channel_component = min(10.0, max(0, len(channels) - 1) * 5.0)
    raw_score = clamp(45.0 + mention_component + sentiment_component + recency_component + image_component + channel_component)

    if latest_signal and latest_signal.telegram_score is not None:
        raw_score = clamp((raw_score * 0.65) + (normalize_score(latest_signal.telegram_score) * 0.35))
    elif latest_signal:
        raw_score = clamp((raw_score * 0.75) + (_signal_to_score(latest_signal.telegram_signal) * 0.25))

    label = "Positive" if raw_score >= 65 else "Negative" if raw_score < 40 else "Neutral"
    reason = (
        f"{mentions} recent mention(s), {positive} positive, {negative} negative, "
        f"{len(channels)} channel(s), {image_mentions} image/chart mention(s)."
    )
    return {"score": raw_score, "label": label, "reason": reason}


def technical_score(db: Session, symbol: str) -> dict[str, Any]:
    try:
        result = analyze_technical(db, symbol, persist=False)
    except Exception as exc:
        logger.warning("Technical analysis failed for %s: %s", symbol, exc)
        return {"score": 50.0, "label": "Neutral", "reason": f"Technical analysis failed: {exc}"}
    score = normalize_score(result.get("technical_score"), default=50.0)
    signal = result.get("signal") or "HOLD"
    return {"score": score, "label": signal, "reason": result.get("reason") or "Technical score calculated."}


def strategy_score(db: Session, symbol: str) -> dict[str, Any]:
    rows = db.scalars(
        select(StrategyResult)
        .where(StrategyResult.symbol == symbol, StrategyResult.timeframe == "summary")
        .order_by(StrategyResult.created_at.desc(), StrategyResult.id.desc())
        .limit(20)
    ).all()
    latest_by_code: dict[str, StrategyResult] = {}
    for row in rows:
        latest_by_code.setdefault(row.strategy_code, row)

    cli_row = db.scalar(select(StrategyCliV6Result).where(StrategyCliV6Result.symbol == symbol).order_by(StrategyCliV6Result.created_at.desc(), StrategyCliV6Result.id.desc()))
    signal_row = _latest(db, StrategySignal, symbol, StrategySignal.signal_date)
    values: list[float] = []
    buy_count = 0
    labels: list[str] = []
    for row in latest_by_code.values():
        label = row.recommendation or row.signal
        score = normalize_score(row.confidence if row.confidence is not None else row.score, default=_signal_to_score(label))
        values.append(score)
        labels.append(f"{row.strategy_code}:{label or '-'}")
        if score >= 65:
            buy_count += 1
    if cli_row:
        score = normalize_score(cli_row.confidence, default=_signal_to_score(cli_row.recommendation))
        values.append(score)
        labels.append(f"cli_v6:{cli_row.recommendation or '-'}")
        if score >= 65:
            buy_count += 1
    if signal_row:
        score = normalize_score(signal_row.score, default=_signal_to_score(signal_row.signal))
        values.append(score)
        labels.append(f"{signal_row.strategy_name}:{signal_row.signal or '-'}")
        if score >= 65:
            buy_count += 1

    if not values:
        return {"score": 50.0, "label": "Neutral", "reason": "No strategy result found yet."}
    score = clamp(sum(values) / len(values))
    label = "Confirmed BUY" if buy_count >= max(1, math.ceil(len(values) * 0.5)) and score >= 65 else "Mixed/Watch" if score >= 50 else "Weak"
    reason = f"{len(values)} strategy signal(s); {buy_count} bullish confirmation(s). " + ", ".join(labels[:5])
    return {"score": score, "label": label, "reason": reason}


def news_score(db: Session, symbol: str) -> dict[str, Any]:
    latest_signal = _latest(db, NewsSignal, symbol, NewsSignal.signal_date)
    rows = get_stock_news(db, symbol)
    if latest_signal and latest_signal.news_score is not None:
        score = normalize_score(latest_signal.news_score)
        label = latest_signal.news_signal or "NEUTRAL"
        reason = latest_signal.reason or f"{len(rows)} news item(s) stored."
        return {"score": score, "label": label, "reason": reason}
    if rows:
        result = analyze_news_sentiment(rows)
        return {"score": result["news_score"], "label": result["news_signal"], "reason": result["reason"]}
    try:
        result = analyze_news(db, symbol, persist=False)
        return {"score": normalize_score(result.get("news_score")), "label": result.get("news_signal") or "NEUTRAL", "reason": result.get("reason") or "No recent news found"}
    except Exception:
        return {"score": 50.0, "label": "Neutral", "reason": "No recent news found"}


@dataclass
class BacktestMetrics:
    win_rate: float = 0.0
    avg_return: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    number_of_trades: int = 0
    backtest_score: float = 50.0
    reason: str = "No backtest data."


def _score_backtest_metrics(metrics: BacktestMetrics) -> float:
    score = 50.0
    score += (metrics.win_rate - 50.0) * 0.35
    score += max(-20.0, min(25.0, (metrics.profit_factor - 1.0) * 15.0))
    score += max(-15.0, min(18.0, metrics.avg_return * 0.35))
    score -= min(30.0, abs(metrics.max_drawdown)) * 0.45
    if metrics.number_of_trades < 3:
        score -= 15.0
    elif metrics.number_of_trades < 6:
        score -= 7.0
    return clamp(score)


def _max_drawdown_pct(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak:
            max_dd = min(max_dd, (value - peak) / peak * 100.0)
    return round(abs(max_dd), 2)


def lightweight_strategy_backtest(df: pd.DataFrame, *, periods: tuple[int, ...] = (30, 90, 180, 365)) -> BacktestMetrics:
    if df.empty or len(df) < 35:
        return BacktestMetrics(reason="Insufficient OHLCV candles for backtest.")
    enriched = add_indicators(df.copy())
    all_returns: list[float] = []
    period_metrics: list[BacktestMetrics] = []
    for period in periods:
        subset = enriched.tail(period).reset_index(drop=True)
        if len(subset) < 30:
            continue
        in_trade = False
        entry_price = 0.0
        returns: list[float] = []
        equity = [100.0]
        for idx in range(1, len(subset) - 1):
            row = subset.iloc[idx]
            next_row = subset.iloc[idx + 1]
            buy_signal = (
                row["close"] > row["ema20"]
                and row["ema20"] > row["ema50"]
                and 45 <= row["rsi14"] <= 72
                and row["macd_hist"] > 0
            )
            sell_signal = row["close"] < row["ema20"] or row["rsi14"] > 78 or row["macd_hist"] < 0
            next_open = float(next_row["open"] if pd.notna(next_row["open"]) else next_row["close"])
            if not in_trade and buy_signal:
                entry_price = next_open * (1 + DEFAULT_SLIPPAGE_RATE)
                in_trade = True
            elif in_trade and sell_signal:
                exit_price = next_open * (1 - DEFAULT_SLIPPAGE_RATE)
                pct = (((exit_price - entry_price) / entry_price) - (DEFAULT_COMMISSION_RATE * 2)) * 100.0
                returns.append(pct)
                equity.append(equity[-1] * (1.0 + pct / 100.0))
                in_trade = False
        if in_trade:
            exit_price = float(subset.iloc[-1]["close"]) * (1 - DEFAULT_SLIPPAGE_RATE)
            pct = (((exit_price - entry_price) / entry_price) - (DEFAULT_COMMISSION_RATE * 2)) * 100.0
            returns.append(pct)
            equity.append(equity[-1] * (1.0 + pct / 100.0))
        if not returns:
            continue
        wins = [value for value in returns if value > 0]
        losses = [value for value in returns if value <= 0]
        profit_factor = sum(wins) / abs(sum(losses)) if losses and abs(sum(losses)) > 0 else (3.0 if wins else 0.0)
        period_metrics.append(
            BacktestMetrics(
                win_rate=(len(wins) / len(returns)) * 100.0,
                avg_return=sum(returns) / len(returns),
                max_drawdown=_max_drawdown_pct(equity),
                profit_factor=profit_factor,
                number_of_trades=len(returns),
                reason=f"{period}d lightweight strategy backtest.",
            )
        )
        all_returns.extend(returns)
    if not period_metrics:
        return BacktestMetrics(reason="Backtest produced too few trades.")
    combined = BacktestMetrics(
        win_rate=sum(row.win_rate for row in period_metrics) / len(period_metrics),
        avg_return=sum(row.avg_return for row in period_metrics) / len(period_metrics),
        max_drawdown=max(row.max_drawdown for row in period_metrics),
        profit_factor=sum(row.profit_factor for row in period_metrics) / len(period_metrics),
        number_of_trades=sum(row.number_of_trades for row in period_metrics),
        reason=f"Lightweight backtest across {len(period_metrics)} period(s), {len(all_returns)} trade(s).",
    )
    combined.backtest_score = _score_backtest_metrics(combined)
    return combined


def backtest_score(db: Session, symbol: str, df: pd.DataFrame | None = None) -> dict[str, Any]:
    summaries = db.scalars(
        select(StrategyBacktestSummary)
        .where(StrategyBacktestSummary.symbol == symbol)
        .order_by(StrategyBacktestSummary.updated_at.desc(), StrategyBacktestSummary.id.desc())
        .limit(5)
    ).all()
    scored: list[BacktestMetrics] = []
    for row in summaries:
        metrics = BacktestMetrics(
            win_rate=normalize_score(row.win_rate, default=0.0),
            avg_return=float(_as_percent(row.total_return) or 0.0),
            max_drawdown=abs(float(_as_percent(row.max_drawdown) or 0.0)),
            profit_factor=float(row.profit_factor or 0.0),
            number_of_trades=int(row.trades_count or 0),
            reason=f"Stored {row.strategy_name} {row.timeframe} backtest.",
        )
        metrics.backtest_score = normalize_score(row.score, default=_score_backtest_metrics(metrics))
        scored.append(metrics)
    if scored:
        best = max(scored, key=lambda item: item.backtest_score)
        return {
            "score": best.backtest_score,
            "label": "Positive" if best.backtest_score >= 65 else "Weak" if best.backtest_score < 40 else "Neutral",
            "reason": best.reason,
            "metrics": best.__dict__,
        }

    df = df if df is not None else get_ohlcv(db, symbol, timeframe="1D", limit=390)
    metrics = lightweight_strategy_backtest(df)
    return {
        "score": metrics.backtest_score,
        "label": "Positive" if metrics.backtest_score >= 65 else "Weak" if metrics.backtest_score < 40 else "Neutral",
        "reason": metrics.reason,
        "metrics": metrics.__dict__,
    }


def risk_liquidity_score(db: Session, symbol: str, df: pd.DataFrame | None = None) -> dict[str, Any]:
    latest_decision = _latest(db, FinalStockDecision, symbol, FinalStockDecision.decision_date)
    latest_liquidity = db.scalar(select(LiquiditySnapshot).where(LiquiditySnapshot.symbol == symbol).order_by(LiquiditySnapshot.created_at.desc(), LiquiditySnapshot.id.desc()))
    values: list[float] = []
    reasons: list[str] = []
    if latest_decision and latest_decision.liquidity_score is not None:
        values.append(normalize_score(latest_decision.liquidity_score))
        reasons.append("latest final decision liquidity")
    if latest_liquidity and latest_liquidity.liquidity_score is not None:
        values.append(normalize_score(latest_liquidity.liquidity_score))
        reasons.append("liquidity snapshot")
    df = df if df is not None else get_ohlcv(db, symbol, timeframe="1D", limit=80)
    if not df.empty and {"volume", "close"}.issubset(df.columns):
        recent = df.tail(20).copy()
        avg_volume = float(recent["volume"].fillna(0).mean() or 0.0)
        avg_value = float((recent["volume"].fillna(0) * recent["close"].fillna(0)).mean() or 0.0)
        if avg_value >= 5_000_000:
            volume_score = 92.0
        elif avg_value >= 1_000_000:
            volume_score = 78.0
        elif avg_value >= 250_000:
            volume_score = 62.0
        elif avg_value >= 50_000:
            volume_score = 45.0
        elif avg_volume > 0:
            volume_score = 30.0
        else:
            volume_score = 25.0
        values.append(volume_score)
        reasons.append(f"avg traded value {avg_value:,.0f}")
    if not values:
        return {"score": 50.0, "label": "Unknown", "reason": "No liquidity data found."}
    score = clamp(sum(values) / len(values))
    if latest_decision and str(latest_decision.risk_level or "").upper() == "HIGH":
        score = clamp(score - 12.0)
        reasons.append("high risk penalty")
    label = "Liquid/OK" if score >= 60 else "Weak liquidity" if score < 40 else "Moderate"
    return {"score": score, "label": label, "reason": ", ".join(reasons)}


def calculate_entry_zone(df: pd.DataFrame, current_price: float | None = None) -> dict[str, Any]:
    if df.empty or len(df) < 20:
        return {"valid": False, "reason": "Insufficient candles for entry zone."}
    enriched = add_indicators(df.copy())
    last = enriched.iloc[-1]
    close = float(current_price if current_price is not None else last["close"])
    ema20 = float(last.get("ema20") or close)
    atr = float(last.get("atr14") or 0.0)
    if close <= 0 or atr <= 0 or pd.isna(atr):
        return {"valid": False, "reason": "Invalid price or ATR."}
    sr = support_resistance(enriched)
    recent = enriched.tail(60)
    support = float(sr.get("support") or recent["low"].min())
    resistance_candidates = recent.loc[recent["high"] > close, "high"]
    resistance = float(sr.get("resistance") or (resistance_candidates.min() if not resistance_candidates.empty else recent["high"].max()))
    entry_zone_low = max(0.01, support, ema20 - 0.5 * atr)
    entry_zone_high = min(close, ema20 + 0.3 * atr)
    if entry_zone_low >= entry_zone_high:
        return {
            "valid": False,
            "entry_zone_low": round(entry_zone_low, 4),
            "entry_zone_high": round(entry_zone_high, 4),
            "reason": "Entry zone is not valid; wait for pullback/retest.",
        }
    stop_loss = max(0.01, entry_zone_low - 1.0 * atr)
    target_1 = entry_zone_high + 1.5 * atr
    target_2 = entry_zone_high + 2.5 * atr
    target_3 = max(resistance, entry_zone_high + 3.0 * atr)
    risk = max(entry_zone_high - stop_loss, 0.0001)
    reward = max(target_2 - entry_zone_high, 0.0)
    return {
        "valid": True,
        "entry_zone_low": round(entry_zone_low, 4),
        "entry_zone_high": round(entry_zone_high, 4),
        "stop_loss": round(stop_loss, 4),
        "target_1": round(target_1, 4),
        "target_2": round(target_2, 4),
        "target_3": round(target_3, 4),
        "risk_reward": round(reward / risk, 2),
        "support": round(support, 4),
        "resistance": round(resistance, 4),
        "atr": round(atr, 4),
        "reason": "Entry zone derived from support, EMA20 and ATR.",
    }


def _analyze_symbol(db: Session, stock: Stock, now: datetime) -> dict[str, Any]:
    symbol = stock.symbol
    df = get_ohlcv(db, symbol, timeframe="1D", limit=390)
    current = latest_price(db, symbol)
    entry = calculate_entry_zone(df, current_price=current)
    telegram = telegram_score(db, symbol, now=now)
    technical = technical_score(db, symbol)
    strategy = strategy_score(db, symbol)
    news = news_score(db, symbol)
    backtest = backtest_score(db, symbol, df=df)
    risk = risk_liquidity_score(db, symbol, df=df)
    scores = {
        "telegram_score": telegram["score"],
        "technical_score": technical["score"],
        "strategy_score": strategy["score"],
        "news_score": news["score"],
        "backtest_score": backtest["score"],
        "risk_liquidity_score": risk["score"],
    }
    final = calculate_final_score(scores)
    signal = recommendation_signal(
        final,
        entry_valid=bool(entry.get("valid")),
        risk_reward=entry.get("risk_reward"),
        backtest_score=backtest["score"],
        risk_liquidity_score=risk["score"],
    )
    explanation_lines = [
        f"Telegram sentiment: {telegram['label']} ({telegram['reason']})",
        f"Technical: {technical['label']} ({technical['reason']})",
        f"Strategy: {strategy['label']} ({strategy['reason']})",
        f"News: {news['label']} ({news['reason']})",
        f"Backtest: {backtest['label']} ({backtest['reason']})",
        f"Liquidity/Risk: {risk['label']} ({risk['reason']})",
    ]
    if not entry.get("valid"):
        explanation_lines.append(f"No-trade/watch note: {entry.get('reason')}")
    row = {
        "symbol": symbol,
        "company_name": stock.name or stock.name_en or stock.name_ar or symbol,
        "final_score": final,
        "telegram_score": telegram["score"],
        "technical_score": technical["score"],
        "strategy_score": strategy["score"],
        "news_score": news["score"],
        "backtest_score": backtest["score"],
        "risk_liquidity_score": risk["score"],
        "signal": signal,
        "entry_zone_low": entry.get("entry_zone_low"),
        "entry_zone_high": entry.get("entry_zone_high"),
        "stop_loss": entry.get("stop_loss"),
        "target_1": entry.get("target_1"),
        "target_2": entry.get("target_2"),
        "target_3": entry.get("target_3"),
        "risk_reward": entry.get("risk_reward"),
        "explanation": "\n".join(explanation_lines),
        "details": {
            "telegram": telegram,
            "technical": technical,
            "strategy": strategy,
            "news": news,
            "backtest": backtest,
            "risk_liquidity": risk,
            "entry_zone": entry,
            "weights": RECOMMENDATION_WEIGHTS,
        },
    }
    return apply_validation_to_row(row, current_price=current)


def sort_top_recommendations(items: list[dict[str, Any]], top_n: int = RECOMMENDATION_TOP_N) -> list[dict[str, Any]]:
    return sorted(items, key=lambda row: (float(row.get("final_score") or 0), float(row.get("risk_liquidity_score") or 0)), reverse=True)[:top_n]


def build_report_items(db: Session, *, top_n: int = RECOMMENDATION_TOP_N, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.utcnow()
    stocks = db.scalars(select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.symbol.asc())).all()
    try:
        from app.services.learning_system import apply_market_regime_guard, apply_pump_risk_guard
        from app.services.market_daily_evaluation import evaluate_daily_market

        market = evaluate_daily_market(db, target_date=now.date(), persist=False)
    except Exception as exc:
        logger.warning("Learning guards unavailable for daily report: %s", exc)
        apply_market_regime_guard = None
        apply_pump_risk_guard = None
        market = None
    items: list[dict[str, Any]] = []
    for stock in stocks:
        try:
            row = _analyze_symbol(db, stock, now)
            if apply_pump_risk_guard is not None:
                row = apply_pump_risk_guard(db, row, now=now, persist=False)
            if apply_market_regime_guard is not None and market:
                row = apply_market_regime_guard(row, market)
            items.append(row)
        except Exception as exc:
            logger.exception("Daily report analysis failed for %s", stock.symbol)
            items.append(
                {
                    "symbol": stock.symbol,
                    "company_name": stock.name or stock.name_en or stock.name_ar or stock.symbol,
                    "final_score": 50.0,
                    "telegram_score": 50.0,
                    "technical_score": 50.0,
                    "strategy_score": 50.0,
                    "news_score": 50.0,
                    "backtest_score": 50.0,
                    "risk_liquidity_score": 50.0,
                    "signal": "WATCH ONLY",
                    "explanation": f"Analysis failed for this stock: {exc}",
                    "details": {"error": str(exc)},
                }
            )
    return sort_top_recommendations(items, top_n=top_n)


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def _report_type_label(report_type: str) -> str:
    return "09:00 AM Cairo" if report_type == "morning" else "09:00 PM Cairo" if report_type == "evening" else f"{report_type} Cairo"


def _comparison_for_report(db: Session, report_time: datetime) -> dict[str, Any]:
    from app.services.daily_loss_audit import build_daily_loss_audit

    target_date = report_time.astimezone(ZoneInfo(REPORT_TIMEZONE)).date() if report_time.tzinfo else report_time.date()
    candidates = [target_date, target_date - timedelta(days=1)]
    last_error: str | None = None
    for candidate in candidates:
        try:
            audit = build_daily_loss_audit(target_date=candidate, persist=False, db=db)
        except Exception as exc:
            last_error = str(exc)
            continue
        items = audit.get("items") or []
        if items or candidate == candidates[-1]:
            status_counts: dict[str, int] = {}
            returns: list[float] = []
            mfe: list[float] = []
            mae: list[float] = []
            evaluated_items: list[dict[str, Any]] = []
            for row in items:
                status = str(row.get("evaluation_status") or "NOT_EVALUATED")
                status_counts[status] = status_counts.get(status, 0) + 1
                if status not in {"NOT_EVALUATED", "DATA_MISSING", "ENTRY_NOT_REACHED"}:
                    evaluated_items.append(row)
                    try:
                        if row.get("actual_return") is not None:
                            returns.append(float(row.get("actual_return")))
                    except Exception:
                        pass
                    try:
                        if row.get("max_favorable_move_pct") is not None:
                            mfe.append(float(row.get("max_favorable_move_pct")))
                        if row.get("max_adverse_move_pct") is not None:
                            mae.append(float(row.get("max_adverse_move_pct")))
                    except Exception:
                        pass
            evaluated = len(evaluated_items)
            sorted_items = sorted(
                evaluated_items,
                key=lambda row: float(row.get("actual_return") or -999999),
                reverse=True,
            )[:5]
            return {
                "date": candidate.isoformat(),
                "summary": audit.get("summary") or {},
                "diagnosis": audit.get("diagnosis"),
                "items": sorted_items,
                "status_counts": status_counts,
                "evaluated": evaluated,
                "avg_return": round(sum(returns) / len(returns), 2) if returns else None,
                "avg_mfe": round(sum(mfe) / len(mfe), 2) if mfe else None,
                "avg_mae": round(sum(mae) / len(mae), 2) if mae else None,
            }
    return {
        "date": target_date.isoformat(),
        "summary": {},
        "diagnosis": f"Comparison unavailable: {last_error or 'no recommendation reports found'}",
        "items": [],
        "status_counts": {},
        "evaluated": 0,
        "avg_return": None,
        "avg_mfe": None,
        "avg_mae": None,
    }


def _append_comparison_block(lines: list[str], comparison: dict[str, Any] | None) -> None:
    if not comparison:
        return
    summary = comparison.get("summary") or {}
    status_counts = comparison.get("status_counts") or {}
    evaluated = int(comparison.get("evaluated") or 0)
    total = int(summary.get("total_recommendations") or 0)
    not_ready = int(status_counts.get("NOT_EVALUATED", 0)) + int(status_counts.get("DATA_MISSING", 0)) + int(status_counts.get("ENTRY_NOT_REACHED", 0))
    lines.extend(
        [
            "Recommendation vs What Happened",
            f"Compared date: {comparison.get('date')}",
            f"Recommendations: {total} | Evaluated: {evaluated} | Not evaluated/missing: {not_ready}",
            f"Statuses: Target {status_counts.get('TARGET_HIT', 0)} / Stop {status_counts.get('STOP_HIT', 0)} / Entry not reached {status_counts.get('ENTRY_NOT_REACHED', 0)} / Pending {status_counts.get('NOT_EVALUATED', 0)} / Missing {status_counts.get('DATA_MISSING', 0)}",
        ]
    )
    if evaluated >= 5:
        lines.extend(
            [
                f"Win rate: {_fmt(summary.get('win_rate_pct'))}% | Good: {summary.get('good_calls', 0)} | Bad: {summary.get('bad_calls', 0)}",
                f"Avg return: {_fmt(comparison.get('avg_return'))}% | Avg MFE: {_fmt(comparison.get('avg_mfe'))}% | Avg MAE: {_fmt(comparison.get('avg_mae'))}%",
            ]
        )
    else:
        lines.append("Accuracy is not reliable yet because evaluated sample size is too small.")
    if total and not_ready / max(1, total) >= 0.5:
        lines.append("Warning: most recommendations are not complete trades yet (NOT_EVALUATED, ENTRY_NOT_REACHED, or DATA_MISSING), so accuracy is not final.")
    if comparison.get("diagnosis"):
        lines.append(f"Diagnosis: {comparison.get('diagnosis')}")
    rows = comparison.get("items") or []
    if rows:
        lines.append("Top evaluated results:")
        for row in rows[:5]:
            lines.append(
                f"- {row.get('symbol')}: {row.get('recommended_signal')} -> {row.get('evaluation_status') or row.get('result')} | "
                f"return {_fmt(row.get('actual_return'))}% | quality {row.get('final_quality') or row.get('evaluation_quality')}"
            )
    lines.extend(["", "-" * 50, ""])


def _performance_for_report(db: Session, report_time: datetime) -> dict[str, Any]:
    try:
        from app.services.recommendation_performance import build_performance_frames

        frames = build_performance_frames(db)
        summary = {row["Metric"]: row["Value"] for row in frames["summary"].to_dict("records")}
        stock_rows = frames["stock_by_stock"]
        evaluated_today = stock_rows
        if not stock_rows.empty and "Evaluated At" in stock_rows.columns:
            day = report_time.astimezone(ZoneInfo(REPORT_TIMEZONE)).date() if report_time.tzinfo else report_time.date()
            dates = pd.to_datetime(stock_rows["Evaluated At"], errors="coerce").dt.date
            evaluated_today = stock_rows[dates == day]
        target_today = evaluated_today[evaluated_today["Status"].eq("TARGET_HIT")] if not evaluated_today.empty and "Status" in evaluated_today.columns else pd.DataFrame()
        stop_today = evaluated_today[evaluated_today["Status"].eq("STOP_HIT")] if not evaluated_today.empty and "Status" in evaluated_today.columns else pd.DataFrame()
        open_rows = stock_rows[stock_rows["Status"].eq("EVALUATED")] if not stock_rows.empty and "Status" in stock_rows.columns else pd.DataFrame()
        best_worst = stock_rows[~stock_rows["Status"].isin(["NOT_EVALUATED", "DATA_MISSING", "ENTRY_NOT_REACHED"])] if not stock_rows.empty and "Status" in stock_rows.columns else pd.DataFrame()
        if not best_worst.empty and "Actual Return %" in best_worst.columns:
            best_worst = best_worst.assign(_ret=pd.to_numeric(best_worst["Actual Return %"], errors="coerce")).sort_values("_ret", ascending=False)
        return {
            "summary": summary,
            "evaluated_today": evaluated_today.head(5).to_dict("records") if not evaluated_today.empty else [],
            "target_hits_today": target_today.head(5).to_dict("records") if not target_today.empty else [],
            "stop_hits_today": stop_today.head(5).to_dict("records") if not stop_today.empty else [],
            "open_count": int(len(open_rows)),
            "best_active": best_worst.head(3).to_dict("records") if not best_worst.empty else [],
            "worst_active": best_worst.tail(3).to_dict("records") if not best_worst.empty else [],
        }
    except Exception as exc:
        return {"error": str(exc)}


def _append_performance_block(lines: list[str], performance: dict[str, Any] | None) -> None:
    if not performance:
        return
    if performance.get("error"):
        lines.extend(["Previous Recommendation Performance", f"Performance block unavailable: {performance['error']}", "", "-" * 50, ""])
        return
    summary = performance.get("summary") or {}
    lines.extend(
        [
            "Previous Recommendation Performance",
            f"Evaluated total: {summary.get('evaluated_recommendations', 0)} | Open: {performance.get('open_count', 0)}",
            f"Target hits today: {len(performance.get('target_hits_today') or [])} | Stop hits today: {len(performance.get('stop_hits_today') or [])}",
        ]
    )
    if summary.get("win_rate_pct") is None:
        lines.append("Win rate: not reliable yet because evaluated sample size is below 5.")
    else:
        lines.append(f"Win rate: {_fmt(summary.get('win_rate_pct'))}% | Avg return: {_fmt(summary.get('average_return_pct'))}%")
    evaluated_today = performance.get("evaluated_today") or []
    if evaluated_today:
        lines.append("Newly evaluated today:")
        for row in evaluated_today[:5]:
            lines.append(
                f"- {row.get('Stock Symbol')}: {row.get('Recommendation Stage')} -> {row.get('Status')} | "
                f"return {_fmt(row.get('Actual Return %'))}% | {row.get('Quality') or '-'}"
            )
    target_hits = performance.get("target_hits_today") or []
    if target_hits:
        lines.append("Targets hit today: " + ", ".join(str(row.get("Stock Symbol")) for row in target_hits[:8]))
    stop_hits = performance.get("stop_hits_today") or []
    if stop_hits:
        lines.append("Stops hit today: " + ", ".join(str(row.get("Stock Symbol")) for row in stop_hits[:8]))
    lines.extend(["", "-" * 50, ""])


def format_telegram_report(
    report_type: str,
    report_time: datetime,
    items: list[dict[str, Any]],
    comparison: dict[str, Any] | None = None,
    performance: dict[str, Any] | None = None,
) -> str:
    if report_time.tzinfo is None:
        report_time = report_time.replace(tzinfo=ZoneInfo(REPORT_TIMEZONE))
    lines = [
        f"{CHART_ICON} EGX Daily Stock Report",
        f"{CLOCK_ICON} Report Time: {_report_type_label(report_type)}",
        f"{DATE_ICON} Date: {report_time:%Y-%m-%d}",
        "",
        f"{WARNING_ICON} Disclaimer:",
        REPORT_DISCLAIMER,
        "",
        f"{FIRE_ICON} Best 5 Stocks Today",
        "",
    ]
    if not items:
        lines.extend(["No active EGX stocks had enough stored data to rank today.", "", f"Risk Note: {RISK_NOTE}"])
        return "\n".join(lines)

    _append_comparison_block(lines, comparison)
    if report_type == "evening":
        _append_performance_block(lines, performance)

    for idx, row in enumerate(items[:RECOMMENDATION_TOP_N], start=1):
        metrics = (row.get("details") or {}).get("backtest", {}).get("metrics", {}) if isinstance(row.get("details"), dict) else {}
        why = row.get("explanation") or ""
        why_lines = [line for line in why.splitlines() if line.strip()][:5]
        lines.extend(
            [
                f"{idx}) {row.get('symbol')} - {row.get('company_name') or row.get('symbol')}",
                f"Final Score: {_fmt(row.get('final_score'), 0)}/100",
                f"Signal: {row.get('signal') or '-'} | Grade: {row.get('signal_grade') or '-'}",
                f"Entry Zone: {_fmt(row.get('entry_zone_low'))} - {_fmt(row.get('entry_zone_high'))}",
                f"Stop Loss: {_fmt(row.get('stop_loss'))}",
                f"Targets: {_fmt(row.get('target_1'))} / {_fmt(row.get('target_2'))} / {_fmt(row.get('target_3'))}",
                f"Risk/Reward: 1:{_fmt(row.get('risk_reward'), 2)}",
                "",
                "Entry is allowed only if:",
                "1. Price reaches the entry zone.",
                "2. Volume confirms the move.",
                "3. Market condition is not bearish/high-volatility.",
                "4. Stop loss is accepted before entry.",
                "5. Risk per trade is less than 1%.",
                "",
                "Why:",
                *[f"- {line}" for line in why_lines],
                f"- Backtest metrics: Win rate {_fmt(metrics.get('win_rate'), 0)}%, Max DD {_fmt(metrics.get('max_drawdown'), 1)}%, PF {_fmt(metrics.get('profit_factor'), 2)}",
                "-" * 50,
                "",
            ]
        )

    best_setup = max(items, key=lambda row: float(row.get("final_score") or 0))
    highest_telegram = max(items, key=lambda row: float(row.get("telegram_score") or 0))
    best_technical = max(items, key=lambda row: float(row.get("technical_score") or 0))
    best_backtest = max(items, key=lambda row: float(row.get("backtest_score") or 0))
    highest_risk = min(items, key=lambda row: float(row.get("risk_liquidity_score") or 0))
    lines.extend(
        [
            f"{OK_ICON} Summary:",
            f"Best setup: {best_setup.get('symbol')}",
            f"Highest Telegram attention: {highest_telegram.get('symbol')}",
            f"Best technical setup: {best_technical.get('symbol')}",
            f"Best backtest result: {best_backtest.get('symbol')}",
            f"Highest risk stock: {highest_risk.get('symbol')}",
            "",
            "Generated automatically by EGX Analysis Bot.",
            f"Risk Note: {RISK_NOTE}",
        ]
    )
    return "\n".join(lines)


def _message_chunks(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n" + "-" * 50, 0, limit)
        if cut <= 0:
            cut = remaining.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


def _report_window(report_type: str, report_time: datetime) -> tuple[datetime, datetime]:
    day = report_time.date()
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    end = start + timedelta(days=1)
    return start, end


def _existing_sent_report(db: Session, report_type: str, report_time: datetime) -> RecommendationReport | None:
    start, end = _report_window(report_type, report_time)
    return db.scalar(
        select(RecommendationReport)
        .where(
            RecommendationReport.report_type == report_type,
            RecommendationReport.report_time >= start,
            RecommendationReport.report_time < end,
            RecommendationReport.sent_to_telegram.is_(True),
        )
        .order_by(RecommendationReport.created_at.desc())
    )


def _persist_report(db: Session, report_type: str, report_time: datetime, items: list[dict[str, Any]], status: str) -> RecommendationReport:
    report = RecommendationReport(report_type=report_type, report_time=report_time.replace(tzinfo=None), status=status)
    db.add(report)
    db.flush()
    recommendation_items: list[RecommendationItem] = []
    for row in items:
        recommendation_item = RecommendationItem(
            report_id=report.id,
            symbol=row.get("symbol"),
            company_name=row.get("company_name"),
            final_score=row.get("final_score"),
            telegram_score=row.get("telegram_score"),
            technical_score=row.get("technical_score"),
            strategy_score=row.get("strategy_score"),
            news_score=row.get("news_score"),
            backtest_score=row.get("backtest_score"),
            risk_liquidity_score=row.get("risk_liquidity_score"),
            signal=row.get("signal"),
            entry_zone_low=row.get("entry_zone_low"),
            entry_zone_high=row.get("entry_zone_high"),
            stop_loss=row.get("stop_loss"),
            target_1=row.get("target_1"),
            target_2=row.get("target_2"),
            target_3=row.get("target_3"),
            risk_reward=row.get("risk_reward"),
            explanation=row.get("explanation"),
            details_json=row.get("details"),
        )
        db.add(recommendation_item)
        recommendation_items.append(recommendation_item)
    db.flush()
    try:
        from app.services.learning_system import create_decision_snapshot, store_recommendation_quality

        market = None
        for recommendation_item in recommendation_items:
            create_decision_snapshot(db, recommendation_item, recommendation_time=report.report_time, market_condition=market)
            store_recommendation_quality(db, recommendation_item)
        db.flush()
    except Exception as exc:
        logger.exception("Decision snapshot capture failed for report %s: %s", report.id, exc)
    return report


def generate_report_payload(
    db: Session,
    *,
    report_type: str,
    top_n: int = RECOMMENDATION_TOP_N,
    report_time: datetime | None = None,
) -> dict[str, Any]:
    report_time = report_time or cairo_now()
    utc_now = datetime.now(UTC).replace(tzinfo=None)
    items = build_report_items(db, top_n=top_n, now=utc_now)
    comparison = _comparison_for_report(db, report_time)
    performance = _performance_for_report(db, report_time) if report_type == "evening" else None
    message = format_telegram_report(report_type, report_time, items, comparison=comparison, performance=performance)
    return {"report_type": report_type, "report_time": report_time, "items": items, "message": message, "comparison": comparison, "performance": performance}


def send_report_message(message: str, *, retries: int = 3) -> int:
    from app.services.telegram_bot import send_private_message_sync

    sent_chunks = 0
    last_error: Exception | None = None
    for chunk in _message_chunks(message):
        for attempt in range(1, retries + 1):
            try:
                send_private_message_sync(chunk)
                sent_chunks += 1
                break
            except Exception as exc:
                last_error = exc
                logger.warning("Daily report Telegram send failed attempt %s/%s: %s", attempt, retries, exc)
        else:
            if last_error:
                raise last_error
    return sent_chunks


def generate_daily_report(
    *,
    report_type: str = "morning",
    send: bool = False,
    dry_run: bool = False,
    force: bool = False,
    top_n: int | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    top_n = int(top_n or RECOMMENDATION_TOP_N)
    report_type = report_type.strip().lower()
    if report_type not in {"morning", "evening"}:
        raise ValueError("report_type must be morning or evening")

    def _run(active_db: Session) -> dict[str, Any]:
        payload = generate_report_payload(active_db, report_type=report_type, top_n=top_n)
        existing = _existing_sent_report(active_db, report_type, payload["report_time"])
        if send and existing and not force:
            items = active_db.scalars(select(RecommendationItem).where(RecommendationItem.report_id == existing.id).order_by(RecommendationItem.final_score.desc())).all()
            return {
                "sent": False,
                "skipped_duplicate": True,
                "report_id": existing.id,
                "items_count": len(items),
                "message": payload["message"],
                "status": "duplicate_skipped",
            }
        if dry_run:
            return {"sent": False, "dry_run": True, "items_count": len(payload["items"]), "message": payload["message"], "status": "dry_run"}

        report = _persist_report(active_db, report_type, payload["report_time"], payload["items"], status="created")
        active_db.commit()
        if send:
            try:
                chunks = send_report_message(payload["message"])
            except Exception as exc:
                report.status = "send_failed"
                report.error_message = str(exc)
                report.sent_to_telegram = False
                active_db.commit()
                return {"sent": False, "report_id": report.id, "items_count": len(payload["items"]), "message": payload["message"], "status": "send_failed", "error": str(exc)}
            report.status = "sent"
            report.sent_to_telegram = True
            active_db.commit()
            return {"sent": True, "chunks": chunks, "report_id": report.id, "items_count": len(payload["items"]), "message": payload["message"], "status": "sent"}
        report.status = "created"
        active_db.commit()
        return {"sent": False, "report_id": report.id, "items_count": len(payload["items"]), "message": payload["message"], "status": "created"}

    if db is not None:
        return _run(db)
    init_db(seed=True)
    with sqlite_write_lock():
        with SessionLocal() as active_db:
            return run_with_db_retry(lambda: _run(active_db), attempts=3)


def send_daily_stock_report(report_type: str = "morning", *, force: bool = False) -> dict[str, Any]:
    return generate_daily_report(report_type=report_type, send=True, force=force)


def latest_report(db: Session, report_type: str | None = None) -> RecommendationReport | None:
    stmt = select(RecommendationReport).order_by(RecommendationReport.created_at.desc(), RecommendationReport.id.desc())
    if report_type:
        stmt = stmt.where(RecommendationReport.report_type == report_type)
    return db.scalar(stmt)


def latest_report_items(db: Session, report_id: int) -> list[RecommendationItem]:
    return db.scalars(select(RecommendationItem).where(RecommendationItem.report_id == report_id).order_by(RecommendationItem.final_score.desc())).all()


def _cli() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Generate EGX daily stock recommendation reports.")
    parser.add_argument("--dry-run", action="store_true", help="Print the report without saving or sending.")
    parser.add_argument("--send-now", choices=["morning", "evening"], help="Generate and send the selected report now.")
    parser.add_argument("--type", choices=["morning", "evening"], default="morning", help="Report type for non-send runs.")
    parser.add_argument("--top-n", type=int, default=RECOMMENDATION_TOP_N, help="Number of ranked stocks to include.")
    parser.add_argument("--force", action="store_true", help="Send even if today's report was already sent.")
    args = parser.parse_args()

    report_type = args.send_now or args.type
    result = generate_daily_report(report_type=report_type, send=bool(args.send_now), dry_run=args.dry_run, force=args.force, top_n=args.top_n)
    print(result["message"])
    print("")
    print(json.dumps({key: value for key, value in result.items() if key != "message"}, ensure_ascii=True, indent=2, default=str))


if __name__ == "__main__":
    _cli()
