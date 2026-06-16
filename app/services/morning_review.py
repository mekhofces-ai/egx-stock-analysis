"""Morning Recommendation Review Module

Compares morning recommendations with actual same-day market results.
Generates detailed P&L analysis and identifies system mistakes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DISCLAIMER, get_settings
from app.models import MarketPrice, Opportunity, RecommendationItem, RecommendationReport, Stock

logger = logging.getLogger(__name__)

CAIRO_TZ = ZoneInfo("Africa/Cairo")


def _fmt(v: Any, d: int = 2) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):.{d}f}"
    except (ValueError, TypeError):
        return "-"


def _pct_change(entry: float | None, current: float | None) -> float | None:
    if entry is None or current is None or entry == 0:
        return None
    return (current - entry) / entry * 100


def _get_latest_price(db: Session, symbol: str) -> float | None:
    price = db.scalar(
        select(MarketPrice)
        .where(MarketPrice.symbol == symbol, MarketPrice.timeframe == "1D")
        .order_by(MarketPrice.timestamp.desc(), MarketPrice.id.desc())
    )
    return price.close if price else None


def get_today_morning_reports(db: Session) -> list[dict[str, Any]]:
    """Fetch all morning recommendation reports generated today (Cairo time)."""
    now_cairo = datetime.now(CAIRO_TZ)
    today_start = now_cairo.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(hours=15)  # up to 3 PM Cairo

    reports = db.scalars(
        select(RecommendationReport)
        .where(
            RecommendationReport.report_type == "morning",
            RecommendationReport.report_time >= today_start,
        )
        .order_by(RecommendationReport.report_time.desc())
    ).all()

    results = []
    for r in reports:
        items = db.scalars(
            select(RecommendationItem)
            .where(RecommendationItem.report_id == r.id)
            .order_by(RecommendationItem.final_score.desc().nullslast())
        ).all()
        results.append({
            "report_id": r.id,
            "report_type": r.report_type,
            "report_time": r.report_time,
            "status": r.status,
            "telegram_sent": r.sent_to_telegram,
            "items": [
                {
                    "symbol": i.symbol,
                    "signal": i.signal,
                    "final_score": i.final_score,
                    "entry_low": i.entry_zone_low,
                    "entry_high": i.entry_zone_high,
                    "stop_loss": i.stop_loss,
                    "target_1": i.target_1,
                    "target_2": i.target_2,
                    "target_3": i.target_3,
                    "risk_reward": i.risk_reward,
                    "explanation": i.explanation,
                }
                for i in items
            ],
        })
    return results


def review_morning_recommendations(db: Session, settings: Any | None = None) -> dict[str, Any]:
    """Compare morning recommendations with actual current prices."""
    settings = settings or get_settings()
    reports = get_today_morning_reports(db)

    if not reports:
        return {
            "found": False,
            "message": "No morning recommendation reports found for today.",
            "reports": [],
            "summary": None,
        }

    all_reviews = []
    total_profit = 0.0
    total_trades = 0
    wins = 0
    losses = 0

    for report in reports:
        for item in report["items"]:
            symbol = item["symbol"]
            current_price = _get_latest_price(db, symbol)
            entry = item["entry_low"] or item["entry_high"]
            sl = item["stop_loss"]
            t1 = item["target_1"]
            t2 = item["target_2"]
            signal = (item["signal"] or "").upper()

            entry_pct = _pct_change(entry, current_price)
            sl_hit = None
            t1_hit = None
            t2_hit = None
            profit_loss = None

            if signal == "BUY" and entry and current_price:
                if sl and current_price <= sl:
                    sl_hit = True
                    profit_loss = _pct_change(entry, sl)
                elif t1 and current_price >= t1:
                    t1_hit = True
                    profit_loss = _pct_change(entry, t1)
                elif t2 and current_price >= t2:
                    t2_hit = True
                    profit_loss = _pct_change(entry, t2)
                else:
                    profit_loss = entry_pct

                if profit_loss is not None:
                    total_trades += 1
                    if profit_loss > 0:
                        wins += 1
                    else:
                        losses += 1
                    total_profit += profit_loss

            entry_zone = f"{_fmt(item['entry_low'])} - {_fmt(item['entry_high'])}" if item["entry_low"] else "-"

            review = {
                "symbol": symbol,
                "signal": signal,
                "entry_zone": entry_zone,
                "stop_loss": _fmt(sl),
                "target_1": _fmt(t1),
                "target_2": _fmt(t2),
                "entry_price": _fmt(entry),
                "current_price": _fmt(current_price),
                "movement_pct": entry_pct,
                "movement_pct_display": _fmt(entry_pct, 1),
                "profit_loss_pct": profit_loss,
                "profit_loss_pct_display": _fmt(profit_loss, 1),
                "sl_hit": sl_hit,
                "t1_hit": t1_hit,
                "t2_hit": t2_hit,
                "what_went_right": [],
                "what_went_wrong": [],
                "failure_reason": None,
            }

            if signal == "BUY":
                if profit_loss is not None and profit_loss < -1:
                    review["what_went_wrong"].append("Price moved against recommendation")
                    if sl_hit:
                        review["failure_reason"] = "stop_loss_hit"
                        review["what_went_wrong"].append("Stop loss was triggered")
                    else:
                        review["failure_reason"] = "price_dropped"
                        review["what_went_wrong"].append("Price dropped without hitting stop loss")
                elif profit_loss is not None and profit_loss > 1:
                    review["what_went_right"].append("Price moved in favor of recommendation")
                    if t1_hit:
                        review["what_went_right"].append("Target 1 was reached")
                    if t2_hit:
                        review["what_went_right"].append("Target 2 was reached")
                else:
                    review["what_went_right"].append("Price is within expected range")

            all_reviews.append(review)

    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    avg_profit = (total_profit / total_trades) if total_trades > 0 else 0.0

    summary = {
        "total_recommendations": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 1),
        "total_profit_loss_pct": round(total_profit, 1),
        "average_profit_loss_pct": round(avg_profit, 1),
        "review_time": datetime.now(CAIRO_TZ).isoformat(),
    }

    return {
        "found": True,
        "reports": reports,
        "reviews": all_reviews,
        "summary": summary,
    }


def format_review_for_telegram(review_result: dict[str, Any]) -> str:
    """Format the morning review for Telegram message."""
    if not review_result.get("found"):
        return f"No morning recommendations found for today.\n\nDisclaimer: {DISCLAIMER}"

    summary = review_result.get("summary", {})
    lines = [
        "EGX Morning Recommendation Review",
        f"Generated: {summary.get('review_time', '-')} (Cairo)",
        "",
        f"Summary: {summary.get('total_recommendations', 0)} recommendations",
        f"Wins: {summary.get('wins', 0)} | Losses: {summary.get('losses', 0)}",
        f"Win Rate: {summary.get('win_rate_pct', 0)}%",
        f"Total P&L: {summary.get('total_profit_loss_pct', 0)}%",
        f"Avg P&L: {summary.get('average_profit_loss_pct', 0)}% per trade",
        "",
        "--- Individual Reviews ---",
    ]

    for review in review_result.get("reviews", []):
        lines.append("")
        lines.append(f"{review['symbol']} ({review['signal']})")
        lines.append(f"  Entry: {review['entry_zone']} | SL: {review['stop_loss']}")
        lines.append(f"  Targets: T1={review['target_1']} T2={review['target_2']}")
        lines.append(f"  Current: {review['current_price']} | Move: {review.get('movement_pct_display', review['movement_pct'])}%")
        lines.append(f"  P&L: {review.get('profit_loss_pct_display', review['profit_loss_pct'])}%")
        if review["what_went_right"]:
            lines.append(f"  Right: {'; '.join(review['what_went_right'])}")
        if review["what_went_wrong"]:
            lines.append(f"  Wrong: {'; '.join(review['what_went_wrong'])}")
        if review["failure_reason"]:
            lines.append(f"  Failure: {review['failure_reason']}")

    lines.extend(["", f"Disclaimer: {DISCLAIMER}"])
    return "\n".join(lines)


def analyze_system_mistakes(review_result: dict[str, Any]) -> list[str]:
    """Analyze what caused bad recommendations."""
    mistakes = []
    failure_counts: dict[str, int] = {}
    for review in review_result.get("reviews", []):
        reason = review.get("failure_reason")
        if reason:
            failure_counts[reason] = failure_counts.get(reason, 0) + 1

    for reason, count in failure_counts.items():
        if reason == "stop_loss_hit":
            mistakes.append(f"Stop loss was hit {count} time(s) - review stop loss placement")
        elif reason == "price_dropped":
            mistakes.append(f"Price dropped {count} time(s) without SL - check entry timing and market direction")
        else:
            mistakes.append(f"Unknown failure: {count} time(s)")

    reviews = review_result.get("reviews", [])
    if reviews:
        moves = [r.get("movement_pct") for r in reviews if r.get("movement_pct") is not None]
        pnls = [r.get("profit_loss_pct") for r in reviews if r.get("profit_loss_pct") is not None]
        avg_move = sum(float(m) for m in moves) / len(moves) if moves else 0
        avg_pnl = sum(float(p) for p in pnls) / len(pnls) if pnls else 0
        if avg_move < -2:
            mistakes.append("Market-wide negative bias detected - consider checking market regime filter")
        if avg_pnl < -3 and avg_move < -3:
            mistakes.append("Broad market drop impacted recommendations - market regime filter may need adjustment")

    if not mistakes:
        mistakes.append("No significant system mistakes detected")

    return mistakes


def best_worst_signal_sources(db: Session) -> dict[str, str]:
    from sqlalchemy import func as sa_func
    from app.models import FinalStockDecision

    today = datetime.now(CAIRO_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    decisions = db.scalars(
        select(FinalStockDecision).where(FinalStockDecision.decision_date >= today)
    ).all()
    if not decisions:
        return {"best": "N/A (no data)", "worst": "N/A (no data)"}

    track: dict[str, list[float]] = {}
    for d in decisions:
        if d.best_analysis_today:
            track.setdefault(d.best_analysis_today, []).append(float(d.final_score or 0))
        if d.best_strategy_today:
            track.setdefault(d.best_strategy_today, []).append(float(d.final_score or 0))

    if not track:
        return {"best": "N/A", "worst": "N/A"}

    avgs = {k: sum(v) / len(v) for k, v in track.items()}
    if not avgs:
        return {"best": "N/A", "worst": "N/A"}
    best = max(avgs, key=avgs.get)
    worst = min(avgs, key=avgs.get)
    return {
        "best": f"{best} ({avgs[best]:.1f} avg score)",
        "worst": f"{worst} ({avgs[worst]:.1f} avg score)",
    }
