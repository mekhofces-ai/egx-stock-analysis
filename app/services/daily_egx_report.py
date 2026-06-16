from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, init_db, run_with_db_retry, sqlite_write_lock
from app.models import DailyEGXReportRow, DailyEGXReportUpload, Stock


REPORT_SOURCE = "egx_daily_excel"
EXPECTED_COLUMNS = {
    "ticker": "ticker",
    "datetime": "report_date",
    "date/time": "report_date",
    "date": "report_date",
    "buyprice": "buy_price",
    "buy price": "buy_price",
    "buy": "buy_price",
    "stop": "stop_loss",
    "stoploss": "stop_loss",
    "stop loss": "stop_loss",
    "target1": "target1",
    "target 1": "target1",
    "target2": "target2",
    "target 2": "target2",
    "status": "status_text",
    "sh term": "short_term",
    "shterm": "short_term",
    "short term": "short_term",
    "med term": "medium_term",
    "medterm": "medium_term",
    "medium term": "medium_term",
    "performance": "performance",
    "weight": "weight",
    "mode": "mode",
    "signals": "signal",
    "signal": "signal",
    "52wh": "week52_high",
    "52w h": "week52_high",
    "52w high": "week52_high",
    "52 week high": "week52_high",
    "52wl": "week52_low",
    "52w l": "week52_low",
    "52w low": "week52_low",
    "52 week low": "week52_low",
    "final arb": "final_arbitration",
    "finalarb": "final_arbitration",
    "final arbitration": "final_arbitration",
}


@dataclass
class ParsedReport:
    rows: list[dict[str, Any]]
    report_date: datetime | None
    sheet_name: str
    source_name: str


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = text.replace("EGX:", "")
    for suffix in (".CA", ".EY", ".EG"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return "".join(ch for ch in text if ch.isalnum() or ch == "_")


def _clean_text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text if text and text.lower() != "nan" else None


def _clean_header(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _num(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text or text.lower() == "nan":
            return None
        return float(text)
    except Exception:
        return None


def _date(value: Any) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime().replace(tzinfo=None)


def _contains(text: str | None, *needles: str) -> bool:
    hay = str(text or "").strip().lower()
    return any(needle.lower() in hay for needle in needles)


def score_daily_row(row: dict[str, Any]) -> tuple[float, str, float | None, list[str]]:
    score = 50.0
    reasons: list[str] = []

    signal = row.get("signal")
    if _contains(signal, "buy dips"):
        score += 18
        reasons.append("Signal is Buy Dips.")
    elif _contains(signal, "pending buy"):
        score += 10
        reasons.append("Signal is Pending Buy.")
    elif _contains(signal, "buy"):
        score += 25
        reasons.append("Signal is Buy.")
    elif _contains(signal, "hold"):
        reasons.append("Signal is Hold.")
    elif _contains(signal, "take profit"):
        score -= 8
        reasons.append("Signal is Take Profit.")
    elif _contains(signal, "reduce"):
        score -= 18
        reasons.append("Signal is Reduce.")
    elif _contains(signal, "sell rallies"):
        score -= 12
        reasons.append("Signal is Sell Rallies.")
    elif _contains(signal, "sell"):
        score -= 25
        reasons.append("Signal is Sell.")

    mode = row.get("mode")
    if _contains(mode, "buy"):
        score += 12
        reasons.append("Mode is Buy Mode.")
    elif _contains(mode, "sell"):
        score -= 12
        reasons.append("Mode is Sell Mode.")

    status = row.get("status_text")
    if _contains(status, "accumulation"):
        score += 10
        reasons.append("Accumulation status.")
    elif _contains(status, "distribution"):
        score -= 10
        reasons.append("Distribution status.")

    weight = row.get("weight")
    if _contains(weight, "overweight"):
        score += 10
        reasons.append("Overweight.")
    elif _contains(weight, "underweight"):
        score -= 10
        reasons.append("Underweight.")

    for label, value in [("short term", row.get("short_term")), ("medium term", row.get("medium_term"))]:
        if _contains(value, "uptrend"):
            score += 8
            reasons.append(f"{label.title()} uptrend.")
        elif _contains(value, "downtrend"):
            score -= 8
            reasons.append(f"{label.title()} downtrend.")

    performance = row.get("performance")
    if _contains(performance, "leading"):
        score += 8
        reasons.append("Performance is Leading.")
    elif _contains(performance, "weakening"):
        score -= 8
        reasons.append("Performance is Weakening.")
    elif _contains(performance, "lagging"):
        score -= 12
        reasons.append("Performance is Lagging.")

    buy_price = _num(row.get("buy_price"))
    stop_loss = _num(row.get("stop_loss"))
    target = _num(row.get("target1")) or _num(row.get("target2"))
    risk_reward = None
    if buy_price and stop_loss and target and buy_price > stop_loss:
        risk = buy_price - stop_loss
        reward = target - buy_price
        risk_reward = round(reward / risk, 2) if risk else None
        if risk_reward is not None:
            if risk_reward >= 2:
                score += 8
                reasons.append(f"Risk/reward {risk_reward:.2f}.")
            elif risk_reward >= 1.5:
                score += 4
                reasons.append(f"Risk/reward {risk_reward:.2f}.")
            elif risk_reward < 1:
                score -= 8
                reasons.append(f"Risk/reward only {risk_reward:.2f}.")

    score = round(max(0.0, min(100.0, score)), 2)
    if score >= 78:
        recommendation = "BUY"
    elif score >= 62:
        recommendation = "WATCH"
    elif score <= 35:
        recommendation = "AVOID"
    elif score <= 45:
        recommendation = "SELL"
    else:
        recommendation = "NEUTRAL"
    return score, recommendation, risk_reward, reasons[:8]


def _find_header_row(raw: pd.DataFrame) -> int:
    for idx in range(min(len(raw), 30)):
        values = {_clean_header(value) for value in raw.iloc[idx].tolist()}
        if "ticker" in values and ("buyprice" in values or "buy price" in values):
            return idx
    raise ValueError("Could not find a report header row with Ticker and BuyPrice columns.")


def _canonical_columns(columns: list[Any]) -> list[str]:
    seen: dict[str, int] = {}
    output: list[str] = []
    for col in columns:
        cleaned = _clean_header(col)
        canonical = EXPECTED_COLUMNS.get(cleaned, cleaned.replace(" ", "_") or "unnamed")
        count = seen.get(canonical, 0)
        seen[canonical] = count + 1
        output.append(canonical if count == 0 else f"{canonical}_{count + 1}")
    return output


def parse_report_bytes(content: bytes, filename: str = "uploaded.xlsx", source_name: str | None = None) -> ParsedReport:
    excel = pd.ExcelFile(io.BytesIO(content))
    if not excel.sheet_names:
        raise ValueError("Workbook has no sheets.")
    sheet_name = excel.sheet_names[0]
    raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name, header=None, dtype=object)
    header_idx = _find_header_row(raw)
    headers = _canonical_columns(raw.iloc[header_idx].tolist())
    df = raw.iloc[header_idx + 1 :].copy()
    df.columns = headers
    df = df.dropna(how="all")

    rows: list[dict[str, Any]] = []
    for _, item in df.iterrows():
        ticker = _clean_text(item.get("ticker"))
        symbol = normalize_symbol(ticker)
        if not symbol or symbol == "TICKER":
            continue
        row = {
            "symbol": symbol,
            "ticker": ticker,
            "report_date": _date(item.get("report_date")),
            "buy_price": _num(item.get("buy_price")),
            "stop_loss": _num(item.get("stop_loss")),
            "target1": _num(item.get("target1")),
            "target2": _num(item.get("target2")),
            "status_text": _clean_text(item.get("status_text")),
            "short_term": _clean_text(item.get("short_term")),
            "medium_term": _clean_text(item.get("medium_term")),
            "performance": _clean_text(item.get("performance")),
            "weight": _clean_text(item.get("weight")),
            "mode": _clean_text(item.get("mode")),
            "signal": _clean_text(item.get("signal")),
            "week52_high": _num(item.get("week52_high")),
            "week52_low": _num(item.get("week52_low")),
            "final_arbitration": _clean_text(item.get("final_arbitration")),
        }
        score, recommendation, risk_reward, reasons = score_daily_row(row)
        row["report_score"] = score
        row["recommendation"] = recommendation
        row["risk_reward"] = risk_reward
        row["raw_json"] = {
            "filename": filename,
            "sheet": sheet_name,
            "score_reasons": reasons,
        }
        rows.append(row)

    dates = [row["report_date"] for row in rows if row.get("report_date")]
    report_date = max(dates) if dates else None
    return ParsedReport(rows=rows, report_date=report_date, sheet_name=sheet_name, source_name=source_name or REPORT_SOURCE)


def import_report_bytes(
    db: Session,
    content: bytes,
    filename: str = "uploaded.xlsx",
    source_name: str = REPORT_SOURCE,
    notes: str | None = None,
    create_missing_stocks: bool = True,
) -> dict[str, Any]:
    parsed = parse_report_bytes(content, filename=filename, source_name=source_name)
    digest = hashlib.sha256(content).hexdigest()

    def _write() -> dict[str, Any]:
        db.rollback()
        duplicate = db.scalar(select(DailyEGXReportUpload).where(DailyEGXReportUpload.file_sha256 == digest))
        upload = DailyEGXReportUpload(
            source_name=parsed.source_name,
            original_filename=Path(filename).name,
            report_date=parsed.report_date,
            rows_count=len(parsed.rows),
            inserted_count=len(parsed.rows),
            updated_count=0,
            status="success",
            file_sha256=digest,
            notes=(notes or "") + (" Duplicate file hash was already imported before." if duplicate else ""),
        )
        with sqlite_write_lock():
            db.add(upload)
            db.flush()
            for row in parsed.rows:
                if create_missing_stocks:
                    stock = db.scalar(select(Stock).where(Stock.symbol == row["symbol"]))
                    if stock is None:
                        db.add(
                            Stock(
                                symbol=row["symbol"],
                                tradingview_symbol=f"EGX:{row['symbol']}",
                                is_active=True,
                            )
                        )
                db.add(DailyEGXReportRow(upload_id=upload.id, **row))
            db.commit()
        return {
            "upload_id": upload.id,
            "source_name": upload.source_name,
            "filename": upload.original_filename,
            "report_date": upload.report_date,
            "rows_count": upload.rows_count,
            "inserted_count": upload.inserted_count,
            "updated_count": upload.updated_count,
            "duplicate_file": duplicate is not None,
            "sheet_name": parsed.sheet_name,
        }

    return run_with_db_retry(_write, attempts=8, delay_seconds=1.0)


def latest_upload(db: Session) -> DailyEGXReportUpload | None:
    return db.scalar(select(DailyEGXReportUpload).order_by(DailyEGXReportUpload.report_date.desc(), DailyEGXReportUpload.created_at.desc()))


def latest_report_row(db: Session, symbol: str) -> DailyEGXReportRow | None:
    symbol = normalize_symbol(symbol)
    return db.scalar(
        select(DailyEGXReportRow)
        .where(DailyEGXReportRow.symbol == symbol)
        .join(DailyEGXReportUpload, DailyEGXReportUpload.id == DailyEGXReportRow.upload_id)
        .order_by(DailyEGXReportUpload.report_date.desc(), DailyEGXReportUpload.created_at.desc(), DailyEGXReportRow.id.desc())
    )


def latest_report_rows(db: Session, limit: int = 500) -> list[DailyEGXReportRow]:
    upload = latest_upload(db)
    if not upload:
        return []
    return db.scalars(
        select(DailyEGXReportRow)
        .where(DailyEGXReportRow.upload_id == upload.id)
        .order_by(DailyEGXReportRow.report_score.desc(), DailyEGXReportRow.symbol.asc())
        .limit(limit)
    ).all()


def latest_report_component(db: Session, symbol: str) -> tuple[float | None, dict[str, Any]]:
    row = latest_report_row(db, symbol)
    if not row:
        return None, {}
    return row.report_score, {
        "upload_id": row.upload_id,
        "symbol": row.symbol,
        "ticker": row.ticker,
        "report_date": row.report_date.isoformat() if row.report_date else None,
        "recommendation": row.recommendation,
        "report_score": row.report_score,
        "signal": row.signal,
        "mode": row.mode,
        "status": row.status_text,
        "short_term": row.short_term,
        "medium_term": row.medium_term,
        "performance": row.performance,
        "weight": row.weight,
        "buy_price": row.buy_price,
        "stop_loss": row.stop_loss,
        "target1": row.target1,
        "target2": row.target2,
        "risk_reward": row.risk_reward,
        "final_arbitration": row.final_arbitration,
        "score_reasons": (row.raw_json or {}).get("score_reasons") if row.raw_json else [],
    }


def summarize_latest_report(db: Session) -> dict[str, Any]:
    upload = latest_upload(db)
    if not upload:
        return {"available": False}
    rows = latest_report_rows(db, limit=1000)
    by_rec: dict[str, int] = {}
    for row in rows:
        by_rec[row.recommendation or "UNKNOWN"] = by_rec.get(row.recommendation or "UNKNOWN", 0) + 1
    top = [
        {
            "symbol": row.symbol,
            "recommendation": row.recommendation,
            "report_score": row.report_score,
            "signal": row.signal,
            "buy_price": row.buy_price,
            "stop_loss": row.stop_loss,
            "target1": row.target1,
            "target2": row.target2,
        }
        for row in rows[:15]
    ]
    return {
        "available": True,
        "upload_id": upload.id,
        "filename": upload.original_filename,
        "report_date": upload.report_date,
        "created_at": upload.created_at,
        "rows_count": upload.rows_count,
        "recommendations": by_rec,
        "top": top,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import and inspect a daily EGX Excel report.")
    parser.add_argument("--file", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    init_db(seed=True)
    content = Path(args.file).read_bytes()
    with SessionLocal() as db:
        result = import_report_bytes(db, content, filename=Path(args.file).name)
        summary = summarize_latest_report(db)
    if args.json:
        print(json.dumps({"import": result, "summary": summary}, ensure_ascii=True, indent=2, default=str))
    else:
        print(
            f"Imported upload {result['upload_id']} | rows {result['rows_count']} | "
            f"date {result.get('report_date') or '-'} | duplicate={result['duplicate_file']}"
        )


if __name__ == "__main__":
    main()
