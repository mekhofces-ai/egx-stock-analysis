from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import REPORT_TIMEZONE, RISK_NOTE
from app.database import SessionLocal, init_db
from app.models import MarketPrice, RecommendationItem, RecommendationReport, TelegramMessageSymbol
from app.services.daily_loss_audit import build_daily_loss_audit


logger = logging.getLogger(__name__)
CAIRO_TZ = ZoneInfo(REPORT_TIMEZONE)
AUDIT_DIR = Path("reports") / "audits"


def cairo_today() -> date:
    return datetime.now(CAIRO_TZ).date()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _safe_float(value)
    return round(number, digits) if number is not None else None


def _window(days: int, end_date: date | None = None) -> tuple[datetime, datetime]:
    end_day = end_date or cairo_today()
    start_day = end_day - timedelta(days=max(1, int(days)) - 1)
    start = datetime(start_day.year, start_day.month, start_day.day)
    end = datetime(end_day.year, end_day.month, end_day.day) + timedelta(days=1)
    return start, end


def recommendation_repetition(db: Session, start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            select(
                RecommendationItem.symbol,
                RecommendationItem.signal,
                func.count(RecommendationItem.id).label("count"),
                func.max(RecommendationItem.final_score).label("best_score"),
            )
            .join(RecommendationReport, RecommendationReport.id == RecommendationItem.report_id)
            .where(RecommendationReport.report_time >= start, RecommendationReport.report_time < end)
            .group_by(RecommendationItem.symbol, RecommendationItem.signal)
            .order_by(func.count(RecommendationItem.id).desc(), RecommendationItem.symbol.asc())
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def telegram_mentions(db: Session, start: datetime, end: datetime, limit: int = 30) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            select(
                TelegramMessageSymbol.symbol,
                func.count(TelegramMessageSymbol.id).label("mentions"),
                func.avg(TelegramMessageSymbol.confidence).label("avg_confidence"),
            )
            .where(TelegramMessageSymbol.created_at >= start, TelegramMessageSymbol.created_at < end)
            .group_by(TelegramMessageSymbol.symbol)
            .order_by(func.count(TelegramMessageSymbol.id).desc(), TelegramMessageSymbol.symbol.asc())
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [{"symbol": row["symbol"], "mentions": row["mentions"], "avg_confidence": _round(row["avg_confidence"])} for row in rows]


def _market_moves(db: Session, start: datetime, end: datetime) -> list[dict[str, Any]]:
    prices = db.scalars(
        select(MarketPrice)
        .where(MarketPrice.timestamp >= start, MarketPrice.timestamp < end)
        .order_by(MarketPrice.symbol.asc(), MarketPrice.timestamp.asc())
    ).all()
    grouped: dict[str, list[MarketPrice]] = defaultdict(list)
    for row in prices:
        grouped[row.symbol].append(row)

    moves: list[dict[str, Any]] = []
    for symbol, rows in grouped.items():
        first = next((_safe_float(row.close) for row in rows if _safe_float(row.close) is not None), None)
        last = next((_safe_float(row.close) for row in reversed(rows) if _safe_float(row.close) is not None), None)
        lows = [_safe_float(row.low) for row in rows if _safe_float(row.low) is not None]
        highs = [_safe_float(row.high) for row in rows if _safe_float(row.high) is not None]
        if first is None or last is None or first == 0:
            continue
        moves.append(
            {
                "symbol": symbol,
                "first_close": _round(first),
                "last_close": _round(last),
                "return_pct": _round((last - first) / first * 100),
                "max_drawdown_pct": _round((min(lows) - first) / first * 100) if lows else None,
                "max_gain_pct": _round((max(highs) - first) / first * 100) if highs else None,
                "price_points": len(rows),
            }
        )
    return sorted(moves, key=lambda row: float(row.get("return_pct") or 0), reverse=True)


def _flatten_daily_result(day_result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    audit_date = day_result.get("audit_date")
    for row in day_result.get("items") or []:
        rows.append(
            {
                "audit_date": audit_date,
                "report_type": row.get("report_type"),
                "symbol": row.get("symbol"),
                "signal": row.get("recommended_signal"),
                "final_score": row.get("final_score"),
                "actual_return": row.get("actual_return"),
                "max_price_after_signal": row.get("max_price_after_signal"),
                "min_price_after_signal": row.get("min_price_after_signal"),
                "time_to_target_minutes": row.get("time_to_target_minutes"),
                "time_to_stop_minutes": row.get("time_to_stop_minutes"),
                "evaluation_quality": row.get("evaluation_quality"),
                "market_score_at_signal": row.get("market_score_at_signal"),
                "market_regime_at_signal": row.get("market_regime_at_signal"),
                "trade_permission_at_signal": row.get("trade_permission_at_signal"),
                "should_trade_yes_no": row.get("should_trade_yes_no"),
                "max_drawdown_after_entry": row.get("max_drawdown_after_entry"),
                "result": row.get("result"),
                "mistake_type": row.get("mistake_type"),
                "root_cause": row.get("root_cause"),
                "fix_required": row.get("fix_required"),
                "telegram_score": row.get("telegram_score"),
                "technical_score": row.get("technical_score"),
                "strategy_score": row.get("strategy_score"),
                "news_score": row.get("news_score"),
                "backtest_score": row.get("backtest_score"),
                "risk_liquidity_score": row.get("risk_liquidity_score"),
            }
        )
    return rows


def build_last7_audit(
    *,
    days: int = 7,
    end_date: date | None = None,
    persist_daily: bool = False,
    db: Session | None = None,
    daily_builder: Callable[..., dict[str, Any]] = build_daily_loss_audit,
) -> dict[str, Any]:
    """Review recommendations and data quality across the last N Cairo trading days.

    The function reuses the daily loss audit logic. It does not create fake
    prices; rows with missing movement data remain classified as data problems.
    """

    end_day = end_date or cairo_today()
    days = max(1, int(days))
    start, end = _window(days, end_day)

    def _run(active_db: Session) -> dict[str, Any]:
        daily_results: list[dict[str, Any]] = []
        audit_rows: list[dict[str, Any]] = []
        summary_totals = Counter()
        mistake_counts = Counter()

        for offset in range(days):
            day = end_day - timedelta(days=days - offset - 1)
            try:
                result = daily_builder(target_date=day, persist=persist_daily, db=active_db)
            except Exception as exc:
                logger.exception("Daily audit failed for %s", day)
                result = {
                    "audit_date": day.isoformat(),
                    "summary": {"total_recommendations": 0, "bad_calls": 0, "good_calls": 0, "no_entry": 0, "mistake_counts": {"DATA_PROBLEM": 1}},
                    "diagnosis": f"Daily audit failed: {exc}",
                    "items": [],
                    "error": str(exc),
                }
            daily_results.append(result)
            summary = result.get("summary") or {}
            for key in [
                "total_recommendations",
                "good_calls",
                "bad_calls",
                "no_entry",
                "stop_loss_hit",
                "target_hit",
            ]:
                summary_totals[key] += int(summary.get(key) or 0)
            mistake_counts.update(summary.get("mistake_counts") or {})
            audit_rows.extend(_flatten_daily_result(result))

        evaluated_rows = [
            row for row in audit_rows
            if row.get("evaluation_quality") not in {None, "NOT_EVALUATED", "LOW_MISSING_DATA"}
            or row.get("actual_return") is not None
        ]
        wins = [row for row in audit_rows if row.get("result") in {"GOOD_CALL", "TARGET_HIT", "OPEN_PROFIT"}]
        losses = [row for row in audit_rows if row.get("result") in {"BAD_CALL", "OPEN_LOSS", "STOP_LOSS_HIT", "BAD_ENTRY"}]
        returns = [_safe_float(row.get("actual_return")) for row in evaluated_rows]
        returns = [value for value in returns if value is not None]

        recommended_symbols = {str(row.get("symbol") or "").upper() for row in audit_rows if row.get("symbol")}
        repeats = recommendation_repetition(active_db, start, end)
        mentions = telegram_mentions(active_db, start, end)
        moves = _market_moves(active_db, start, end)
        missed = [row for row in moves if row["symbol"].upper() not in recommended_symbols][:10]

        best = sorted(evaluated_rows, key=lambda row: float(row.get("actual_return") or -9999), reverse=True)[:10]
        worst = sorted(evaluated_rows, key=lambda row: float(row.get("actual_return") or 9999))[:10]
        repeated_stocks = [row for row in repeats if int(row.get("count") or 0) > 1][:20]

        total = int(summary_totals.get("total_recommendations") or len(audit_rows))
        win_rate = round(len(wins) / max(1, len(wins) + len(losses)) * 100, 2)
        average_return = round(sum(returns) / len(returns), 2) if returns else None
        max_drawdown = min((_safe_float(row.get("max_drawdown_after_entry")) or 0 for row in audit_rows), default=None)
        top_failure = mistake_counts.most_common(1)[0][0] if mistake_counts else "NO_DATA"

        return {
            "period_start": start.date().isoformat(),
            "period_end": (end - timedelta(days=1)).date().isoformat(),
            "days": days,
            "summary": {
                "total_recommendations": total,
                "evaluated": len(evaluated_rows),
                "good_calls": int(summary_totals.get("good_calls") or len(wins)),
                "bad_calls": int(summary_totals.get("bad_calls") or len(losses)),
                "no_entry": int(summary_totals.get("no_entry") or 0),
                "win_rate": win_rate,
                "average_return": average_return,
                "max_drawdown": _round(max_drawdown),
                "top_failure_reason": top_failure,
                "mistake_counts": dict(mistake_counts),
                "repeated_symbols": len(repeated_stocks),
                "missed_opportunities": len(missed),
            },
            "best_recommendations": best,
            "worst_recommendations": worst,
            "repeated_stocks": repeated_stocks,
            "telegram_mentions": mentions,
            "market_moves": moves[:30],
            "missed_opportunities": missed,
            "daily_results": daily_results,
            "audit_rows": audit_rows,
            "risk_note": RISK_NOTE,
        }

    if db is not None:
        return _run(db)
    init_db(seed=True)
    with SessionLocal() as active_db:
        return _run(active_db)


def export_last7_csv(result: dict[str, Any]) -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIT_DIR / f"last7_audit_{result['period_start']}_to_{result['period_end']}.csv"
    pd.DataFrame(result.get("audit_rows") or []).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def export_last7_excel(result: dict[str, Any]) -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIT_DIR / f"last7_audit_{result['period_start']}_to_{result['period_end']}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([result.get("summary") or {}]).to_excel(writer, index=False, sheet_name="Summary")
        pd.DataFrame(result.get("audit_rows") or []).to_excel(writer, index=False, sheet_name="Audit Rows")
        pd.DataFrame(result.get("repeated_stocks") or []).to_excel(writer, index=False, sheet_name="Repeated")
        pd.DataFrame(result.get("missed_opportunities") or []).to_excel(writer, index=False, sheet_name="Missed")
        pd.DataFrame(result.get("telegram_mentions") or []).to_excel(writer, index=False, sheet_name="Telegram")
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            if ws.max_row and ws.max_column:
                ws.auto_filter.ref = ws.dimensions
            for column_cells in ws.columns:
                values = [str(cell.value or "") for cell in column_cells]
                ws.column_dimensions[column_cells[0].column_letter].width = min(50, max(12, max(len(v) for v in values) + 2))
    return path


def format_last7_summary(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    lines = [
        "EGX Last 7 Days System Audit",
        f"Period: {result.get('period_start')} to {result.get('period_end')}",
        "",
        f"Total recommendations: {summary.get('total_recommendations')}",
        f"Evaluated with price data: {summary.get('evaluated')}",
        f"Win rate: {summary.get('win_rate')}%",
        f"Average return: {summary.get('average_return')}",
        f"Max drawdown: {summary.get('max_drawdown')}",
        f"Top failure reason: {summary.get('top_failure_reason')}",
        "",
        "Worst recommendations:",
    ]
    for row in (result.get("worst_recommendations") or [])[:5]:
        lines.append(f"- {row.get('symbol')}: {row.get('actual_return')}% | {row.get('mistake_type')} | {row.get('root_cause')}")
    lines.extend(["", f"Risk Note: {RISK_NOTE}"])
    return "\n".join(lines)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Review EGX system recommendations over the last N days.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--end-date", help="YYYY-MM-DD; defaults to today in Cairo.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--export-excel", action="store_true")
    args = parser.parse_args()

    result = build_last7_audit(days=args.days, end_date=_parse_date(args.end_date))
    if args.export_csv:
        result["csv_path"] = str(export_last7_csv(result))
    if args.export_excel:
        result["excel_path"] = str(export_last7_excel(result))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_last7_summary(result))
        if args.export_csv:
            print(f"CSV exported: {result['csv_path']}")
        if args.export_excel:
            print(f"Excel exported: {result['excel_path']}")


if __name__ == "__main__":
    _cli()
