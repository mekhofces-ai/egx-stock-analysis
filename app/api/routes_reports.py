from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import DISCLAIMER, get_settings
from app.database import get_db
from app.services.reports import (
    build_afternoon_report,
    build_daily_report,
    build_final_decision_report,
    build_night_opportunity_report,
    build_stock_brief,
    send_afternoon_report,
    send_daily_report,
    send_night_opportunity_report,
)
from app.services.morning_review import review_morning_recommendations, format_review_for_telegram, analyze_system_mistakes
from app.services.daily_file_report import build_daily_file_report, send_daily_file_report_to_telegram


router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/daily")
def daily_report_preview(db: Session = Depends(get_db)) -> dict[str, Any]:
    message = build_daily_report(db, settings=get_settings())
    return {"message": message, "length": len(message), "disclaimer": DISCLAIMER}


@router.post("/daily/send")
def daily_report_send() -> dict[str, Any]:
    return send_daily_report(settings=get_settings())


@router.get("/night-opportunities")
def night_opportunities_preview(db: Session = Depends(get_db)) -> dict[str, Any]:
    message = build_night_opportunity_report(db, settings=get_settings())
    return {"message": message, "length": len(message), "disclaimer": DISCLAIMER}


@router.post("/night-opportunities/send")
def night_opportunities_send() -> dict[str, Any]:
    return send_night_opportunity_report(settings=get_settings())


@router.get("/stock/{symbol}")
def stock_brief(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    message = build_stock_brief(db, symbol=symbol, settings=get_settings())
    return {"symbol": symbol.upper(), "message": message, "length": len(message), "disclaimer": DISCLAIMER}


@router.get("/decision/{symbol}")
def final_decision(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    message = build_final_decision_report(db, symbol=symbol, settings=get_settings())
    return {"symbol": symbol.upper(), "message": message, "length": len(message), "disclaimer": DISCLAIMER}


@router.get("/afternoon")
def afternoon_report_preview(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Preview the 3 PM Cairo afternoon report with morning review."""
    message = build_afternoon_report(db, settings=get_settings())
    return {"message": message, "length": len(message), "disclaimer": DISCLAIMER}


@router.post("/afternoon/send")
def afternoon_report_send() -> dict[str, Any]:
    """Send the 3 PM Cairo afternoon report to Telegram."""
    return send_afternoon_report(settings=get_settings())


@router.get("/morning-review")
def morning_review_preview(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Preview the morning recommendation review."""
    review = review_morning_recommendations(db, settings=get_settings())
    message = format_review_for_telegram(review)
    mistakes = analyze_system_mistakes(review)
    return {
        "review": review,
        "message": message,
        "mistakes": mistakes,
        "disclaimer": DISCLAIMER,
    }


@router.post("/daily-file-report")
def generate_daily_file_report(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Generate the Excel and PDF daily file report."""
    result = build_daily_file_report(db, settings=get_settings())
    return {**result, "disclaimer": DISCLAIMER}


@router.post("/daily-file-report/send")
def send_daily_file_report( db: Session = Depends(get_db)) -> dict[str, Any]:
    """Send the daily file report to Telegram."""
    from app.models import DailyFileReport
    from sqlalchemy import select

    latest = db.scalar(select(DailyFileReport).order_by(DailyFileReport.created_at.desc()))
    if not latest:
        return {"sent": False, "error": "No daily file report found. Generate one first."}

    ok = send_daily_file_report_to_telegram(db, latest, settings=get_settings())
    return {"sent": ok, "report_id": latest.id, "disclaimer": DISCLAIMER}
