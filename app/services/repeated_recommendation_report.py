from __future__ import annotations

import argparse
import json
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import REPORT_TIMEZONE
from app.database import SessionLocal, init_db
from app.models import (
    NotificationLog,
    RecommendationItem,
    RecommendationReport,
    RepeatedRecommendationAudit,
    StrategyResult,
    TelegramMessageSymbol,
)


CAIRO_TZ = ZoneInfo(REPORT_TIMEZONE)
AUDIT_DIR = Path("reports") / "audits"


def cairo_today() -> date:
    return datetime.now(CAIRO_TZ).date()


def _window(days: int, end_date: date | None = None) -> tuple[datetime, datetime]:
    end_day = end_date or cairo_today()
    start_day = end_day - timedelta(days=max(1, int(days)) - 1)
    return datetime(start_day.year, start_day.month, start_day.day), datetime(end_day.year, end_day.month, end_day.day) + timedelta(days=1)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _source_flags(items: list[RecommendationItem], telegram_mentions: int, strategy_hits: int) -> tuple[bool, bool, bool, str]:
    avg_telegram = sum(_safe_float(row.telegram_score) for row in items) / len(items) if items else 0.0
    avg_strategy = sum(_safe_float(row.strategy_score) for row in items) / len(items) if items else 0.0
    telegram_caused = telegram_mentions > 0 or avg_telegram >= 65
    strategy_caused = strategy_hits > 0 or avg_strategy >= 65
    report_caused = len(items) > 1
    if telegram_caused and strategy_caused:
        source = "telegram_and_strategy"
    elif telegram_caused:
        source = "telegram"
    elif strategy_caused:
        source = "strategy"
    elif report_caused:
        source = "report_generation"
    else:
        source = "unknown"
    return telegram_caused, strategy_caused, report_caused, source


def build_repeated_recommendation_report(
    *,
    days: int = 7,
    end_date: date | None = None,
    symbol_filter: str | None = None,
    persist: bool = True,
    db: Session | None = None,
) -> dict[str, Any]:
    start, end = _window(days, end_date)
    run_id = f"repeat_{datetime.now(CAIRO_TZ):%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}"

    def _run(active_db: Session) -> dict[str, Any]:
        reports = active_db.scalars(
            select(RecommendationReport)
            .where(RecommendationReport.report_time >= start, RecommendationReport.report_time < end)
            .order_by(RecommendationReport.report_time.asc(), RecommendationReport.id.asc())
        ).all()
        report_ids = [row.id for row in reports]
        items: list[RecommendationItem] = []
        if report_ids:
            query = select(RecommendationItem).where(RecommendationItem.report_id.in_(report_ids))
            if symbol_filter:
                query = query.where(RecommendationItem.symbol == symbol_filter.upper())
            items = list(active_db.scalars(query.order_by(RecommendationItem.symbol.asc(), RecommendationItem.id.asc())).all())
        grouped: dict[tuple[str, str], list[RecommendationItem]] = defaultdict(list)
        report_by_id = {row.id: row for row in reports}
        for item in items:
            grouped[(item.symbol.upper(), str(item.signal or "UNKNOWN").upper())].append(item)

        rows: list[dict[str, Any]] = []
        for (symbol, rec), rec_items in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0][0])):
            if len(rec_items) <= 1:
                continue
            repeated_times = [
                (report_by_id[item.report_id].report_time if item.report_id in report_by_id else None)
                for item in rec_items
            ]
            repeated_times_text = [dt.isoformat(sep=" ", timespec="seconds") for dt in repeated_times if dt]
            telegram_mentions = int(
                active_db.scalar(
                    select(func.count()).select_from(TelegramMessageSymbol).where(
                        TelegramMessageSymbol.symbol == symbol,
                        TelegramMessageSymbol.created_at >= start,
                        TelegramMessageSymbol.created_at < end,
                    )
                )
                or 0
            )
            strategy_hits = int(
                active_db.scalar(
                    select(func.count()).select_from(StrategyResult).where(
                        StrategyResult.symbol == symbol,
                        StrategyResult.created_at >= start,
                        StrategyResult.created_at < end,
                    )
                )
                or 0
            )
            sent_alerts = int(
                active_db.scalar(
                    select(func.count()).select_from(NotificationLog).where(
                        NotificationLog.symbol == symbol,
                        NotificationLog.recommendation == rec,
                        NotificationLog.sent_at >= start,
                        NotificationLog.sent_at < end,
                    )
                )
                or 0
            )
            telegram_caused, strategy_caused, report_caused, source = _source_flags(rec_items, telegram_mentions, strategy_hits)
            dedup_blocked = sent_alerts < len(rec_items)
            root = (
                f"{symbol} repeated {len(rec_items)} times as {rec}. "
                f"Outbound alert rows: {sent_alerts}; dedupe {'limited outbound sends' if dedup_blocked else 'did not limit these rows'}. "
                f"Telegram mentions: {telegram_mentions}; strategy rows: {strategy_hits}."
            )
            row = {
                "run_id": run_id,
                "period_start": start.isoformat(sep=" ", timespec="seconds"),
                "period_end": (end - timedelta(seconds=1)).isoformat(sep=" ", timespec="seconds"),
                "symbol": symbol,
                "recommendation_type": rec,
                "repeats_count": len(rec_items),
                "dates_times_repeated": "; ".join(repeated_times_text),
                "source_of_repeat": source,
                "telegram_caused": telegram_caused,
                "strategy_caused": strategy_caused,
                "report_generation_caused": report_caused,
                "telegram_mentions": telegram_mentions,
                "strategy_hits": strategy_hits,
                "outbound_alerts_sent": sent_alerts,
                "deduplication_blocked": dedup_blocked,
                "root_cause": root,
            }
            rows.append(row)
            if persist:
                active_db.add(
                    RepeatedRecommendationAudit(
                        run_id=run_id,
                        period_start=start,
                        period_end=end,
                        symbol=symbol,
                        recommendation_type=rec,
                        repeats_count=len(rec_items),
                        repeated_at_json=repeated_times_text,
                        source_of_repeat=source,
                        telegram_caused=telegram_caused,
                        strategy_caused=strategy_caused,
                        report_generation_caused=report_caused,
                        deduplication_blocked=dedup_blocked,
                        root_cause=root,
                        details_json={
                            "telegram_mentions": telegram_mentions,
                            "strategy_hits": strategy_hits,
                            "outbound_alerts_sent": sent_alerts,
                            "item_ids": [item.id for item in rec_items],
                        },
                    )
                )
        if persist:
            active_db.commit()
        aalr_rows = [row for row in rows if row["symbol"] == "AALR"]
        aalr_root_cause = (
            aalr_rows[0]["root_cause"]
            if aalr_rows
            else "AALR did not repeat inside the selected window, or it repeated only outside recommendation_reports."
        )
        return {
            "run_id": run_id,
            "period_start": start.date().isoformat(),
            "period_end": (end - timedelta(days=1)).date().isoformat(),
            "rows": rows,
            "summary": {
                "repeated_symbols": len({row["symbol"] for row in rows}),
                "repeated_symbol_recommendation_pairs": len(rows),
                "total_repeated_rows": sum(int(row["repeats_count"]) for row in rows),
                "dedupe_limited_pairs": sum(1 for row in rows if row["deduplication_blocked"]),
            },
            "aalr_root_cause": aalr_root_cause,
        }

    if db is not None:
        return _run(db)
    init_db(seed=True)
    with SessionLocal() as active_db:
        return _run(active_db)


def export_repeated_report(result: dict[str, Any], *, excel: bool = True) -> dict[str, str]:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"repeated_recommendations_{result['period_start']}_to_{result['period_end']}"
    csv_path = AUDIT_DIR / f"{stem}.csv"
    pd.DataFrame(result.get("rows") or []).to_csv(csv_path, index=False, encoding="utf-8-sig")
    output = {"csv_path": str(csv_path)}
    if excel:
        xlsx_path = AUDIT_DIR / f"{stem}.xlsx"
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            pd.DataFrame([result.get("summary") or {}]).to_excel(writer, index=False, sheet_name="Summary")
            pd.DataFrame(result.get("rows") or []).to_excel(writer, index=False, sheet_name="Repeated")
            pd.DataFrame([{"AALR Root Cause": result.get("aalr_root_cause")}]).to_excel(writer, index=False, sheet_name="AALR")
        output["excel_path"] = str(xlsx_path)
    return output


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build repeated recommendation audit report.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--end-date")
    parser.add_argument("--symbol")
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = build_repeated_recommendation_report(days=args.days, end_date=_parse_date(args.end_date), symbol_filter=args.symbol)
    if args.export:
        result["exports"] = export_repeated_report(result)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Repeated recommendation report: {result['period_start']} to {result['period_end']}")
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
        print(f"AALR: {result['aalr_root_cause']}")
        if args.export:
            print(json.dumps(result["exports"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
