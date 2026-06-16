from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, init_db


TABLE_SYMBOL_COLUMNS = {
    "final_stock_decisions": "symbol",
    "opportunities": "symbol",
    "recommendation_items": "symbol",
    "telegram_message_symbols": "symbol",
    "market_prices": "symbol",
    "ohlcv_data": "symbol",
    "strategy_results": "symbol",
    "stock_combined_analysis": "symbol",
}


def _table_exists(db: Session, table: str) -> bool:
    row = db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table"), {"table": table}).first()
    return row is not None


def _count(db: Session, sql: str, params: dict[str, Any] | None = None) -> int:
    return int(db.execute(text(sql), params or {}).scalar() or 0)


def build_data_relationship_report(db: Session) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    stock_count = _count(db, "SELECT COUNT(*) FROM stocks")
    active_stock_count = _count(db, "SELECT COUNT(*) FROM stocks WHERE is_active=1")
    for table, column in TABLE_SYMBOL_COLUMNS.items():
        if not _table_exists(db, table):
            rows.append({"table": table, "status": "missing_table", "rows": None, "distinct_symbols": None, "orphan_symbols": None, "notes": "Table does not exist."})
            continue
        total = _count(db, f'SELECT COUNT(*) FROM "{table}"')
        distinct_symbols = _count(db, f'SELECT COUNT(DISTINCT UPPER({column})) FROM "{table}" WHERE {column} IS NOT NULL AND TRIM({column}) <> ""')
        orphan_symbols = _count(
            db,
            f'''
            SELECT COUNT(*) FROM (
                SELECT DISTINCT UPPER(t.{column}) AS symbol
                FROM "{table}" t
                LEFT JOIN stocks s ON UPPER(s.symbol)=UPPER(t.{column})
                WHERE t.{column} IS NOT NULL AND TRIM(t.{column}) <> "" AND s.symbol IS NULL
            )
            ''',
        )
        status = "ok" if orphan_symbols == 0 else "warning"
        rows.append(
            {
                "table": table,
                "status": status,
                "rows": total,
                "distinct_symbols": distinct_symbols,
                "orphan_symbols": orphan_symbols,
                "notes": "Linked to stocks table." if status == "ok" else "Some symbols are not in stocks table.",
            }
        )
    duplicate_final_decisions = []
    if _table_exists(db, "final_stock_decisions"):
        duplicate_final_decisions = [
            dict(row)
            for row in db.execute(
                text(
                    """
                    SELECT symbol, COUNT(*) AS decision_rows, MAX(decision_date) AS latest_decision
                    FROM final_stock_decisions
                    GROUP BY symbol
                    HAVING COUNT(*) > 1
                    ORDER BY decision_rows DESC
                    LIMIT 25
                    """
                )
            ).mappings()
        ]
    opportunity_without_latest_decision = []
    if _table_exists(db, "opportunities") and _table_exists(db, "final_stock_decisions"):
        opportunity_without_latest_decision = [
            dict(row)
            for row in db.execute(
                text(
                    """
                    SELECT o.symbol, o.recommendation, o.final_score, o.updated_at
                    FROM opportunities o
                    LEFT JOIN final_stock_decisions f ON UPPER(f.symbol)=UPPER(o.symbol)
                    WHERE f.symbol IS NULL
                    ORDER BY o.final_score DESC
                    LIMIT 25
                    """
                )
            ).mappings()
        ]
    issues = sum(1 for row in rows if row["status"] != "ok") + len(opportunity_without_latest_decision)
    return {
        "summary": {
            "stock_count": stock_count,
            "active_stock_count": active_stock_count,
            "tables_checked": len(rows),
            "issue_count": issues,
            "duplicate_final_decision_symbols": len(duplicate_final_decisions),
        },
        "table_relationships": rows,
        "duplicate_final_decisions": duplicate_final_decisions,
        "opportunities_without_final_decision": opportunity_without_latest_decision,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check EGX data relationships.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    init_db(seed=True)
    with SessionLocal() as db:
        result = build_data_relationship_report(db)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2, default=str))
        for row in result["table_relationships"]:
            print(f"{row['table']}: {row['status']} rows={row['rows']} symbols={row['distinct_symbols']} orphan={row['orphan_symbols']}")


if __name__ == "__main__":
    main()
