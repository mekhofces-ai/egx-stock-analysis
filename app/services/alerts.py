from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import DISCLAIMER, Settings, get_settings
from app.models import AppSetting, FinalAnalysis
from app.services.analysis_runner import format_alert
from app.services.reports import build_final_decision_report, combined_final_decision
from app.services.screener_recommendations import build_final_recommendations
from app.services.strategy import run_strategy_for_symbol


def alerts_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.telegram_alert_enabled or not settings.telegram_bot_token:
        return False
    try:
        from app.database import SessionLocal
        from app.services.subscribers import active_alert_chat_ids

        with SessionLocal() as db:
            return bool(active_alert_chat_ids(db, settings=settings))
    except Exception:
        return bool(settings.allowed_chat_ids)


def _already_sent(db: Session, key: str) -> bool:
    return db.scalar(select(AppSetting.id).where(AppSetting.key == key)) is not None


def _mark_sent(db: Session, key: str, value: str = "sent") -> None:
    existing = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def _analysis_alert_key(final: FinalAnalysis) -> str:
    return f"telegram_alert:final_analysis:{final.id}"


def _recommendation_alert_key(row: dict[str, Any], generated_at: datetime | None = None) -> str:
    day = (generated_at or datetime.utcnow()).strftime("%Y%m%d")
    return f"telegram_alert:recommendation:{day}:{row.get('symbol')}:{row.get('final_recommendation')}"


def analysis_is_alertable(final: FinalAnalysis, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return (
        settings.telegram_alert_enabled
        and str(final.final_decision).upper() in settings.alert_decision_set
        and float(final.confidence_score or 0) >= settings.telegram_alert_min_confidence
    )


def recommendation_is_alertable(row: dict[str, Any], settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.telegram_alert_enabled or not settings.telegram_alert_recommendations_enabled:
        return False
    if str(row.get("final_recommendation", "")).upper() not in settings.alert_decision_set:
        return False
    if float(row.get("final_score") or 0) < settings.telegram_alert_min_confidence:
        return False
    if settings.telegram_alert_require_telegram_confirmation and int(row.get("telegram_signals") or 0) <= 0:
        return False
    return True


def format_recommendation_alert(row: dict[str, Any]) -> str:
    reasons = "\n".join(f"{idx}. {reason}" for idx, reason in enumerate(row.get("reasons") or [], start=1))
    warnings = "\n".join(f"* {warning}" for warning in (row.get("warnings") or []))
    return (
        "EGX BUY Notification\n"
        f"Stock: {row.get('symbol')}\n"
        f"Name: {row.get('name') or '-'}\n"
        f"Decision: {row.get('final_recommendation')}\n"
        f"Final Score: {float(row.get('final_score') or 0):.0f}%\n"
        f"Smart Action: {row.get('smart_action_now') or '-'}\n"
        f"Plan: {row.get('smart_plan') or '-'}\n"
        f"Trend: {row.get('smart_main_trend') or '-'}\n\n"
        f"Last Price: {row.get('last_price') or '-'}\n"
        f"RSI: {row.get('rsi') or '-'}\n"
        f"Buy Zone: {row.get('smart_buy_zone') or '-'}\n"
        f"Entry: {row.get('smart_suggested_entry') or '-'}\n"
        f"Stop: {row.get('smart_suggested_stop') or '-'}\n"
        f"Targets: {row.get('smart_target_scalp') or '-'} / {row.get('smart_target_swing') or '-'} / {row.get('smart_target_long') or '-'}\n\n"
        f"Telegram Signals: {row.get('telegram_signals') or 0} | Buy: {row.get('telegram_buy') or 0}\n"
        f"TradingView Vote: {row.get('tv_vote') or '-'}\n\n"
        f"Reasons:\n{reasons or '1. Screener and Telegram comparison.'}\n\n"
        f"Warnings:\n{warnings or '* None'}\n\n"
        f"Chart: {row.get('tradingview_chart_url') or '-'}\n\n"
        f"Disclaimer: {DISCLAIMER}"
    )


def send_buy_alerts_for_analyses(db: Session, analyses: Iterable[FinalAnalysis], settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    result = {"configured": alerts_configured(settings), "eligible": 0, "sent": 0, "skipped_duplicate": 0}
    if not result["configured"]:
        return result

    from app.services.telegram_bot import send_private_message_sync

    for final in analyses:
        if not analysis_is_alertable(final, settings):
            continue
        result["eligible"] += 1
        key = _analysis_alert_key(final)
        if _already_sent(db, key):
            result["skipped_duplicate"] += 1
            continue
        source_username = final.extracted_signal.source.username if final.extracted_signal and final.extracted_signal.source else None
        send_private_message_sync(format_alert(final, final.extracted_signal, source_username=source_username), settings=settings)
        _mark_sent(db, key, value=f"sent:{datetime.utcnow().isoformat()}")
        result["sent"] += 1
    return result


def send_pending_buy_signal_alerts(db: Session, settings: Settings | None = None, limit: int = 200) -> dict[str, Any]:
    analyses = db.scalars(
        select(FinalAnalysis)
        .options(joinedload(FinalAnalysis.extracted_signal))
        .order_by(FinalAnalysis.created_at.desc())
        .limit(limit)
    ).all()
    return send_buy_alerts_for_analyses(db, analyses, settings=settings)


async def send_buy_alerts_for_analyses_async(db: Session, analyses: Iterable[FinalAnalysis], settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    result = {"configured": alerts_configured(settings), "eligible": 0, "sent": 0, "skipped_duplicate": 0}
    if not result["configured"]:
        return result

    from app.services.telegram_bot import send_private_message

    for final in analyses:
        if not analysis_is_alertable(final, settings):
            continue
        result["eligible"] += 1
        key = _analysis_alert_key(final)
        if _already_sent(db, key):
            result["skipped_duplicate"] += 1
            continue
        source_username = final.extracted_signal.source.username if final.extracted_signal and final.extracted_signal.source else None
        await send_private_message(format_alert(final, final.extracted_signal, source_username=source_username), settings=settings)
        _mark_sent(db, key, value=f"sent:{datetime.utcnow().isoformat()}")
        result["sent"] += 1
    return result


def send_buy_recommendation_alerts(db: Session, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    result = {"configured": alerts_configured(settings), "eligible": 0, "sent": 0, "skipped_duplicate": 0, "skipped_strategy": 0, "provider_status": None}
    if not result["configured"]:
        return result

    from app.services.telegram_bot import send_private_message_sync

    run = build_final_recommendations(db, settings=settings, limit=500)
    result["provider_status"] = run.provider_status
    for row in run.rows:
        if not recommendation_is_alertable(row, settings):
            continue
        result["eligible"] += 1
        key = _recommendation_alert_key(row, run.generated_at)
        if _already_sent(db, key):
            result["skipped_duplicate"] += 1
            continue
        strategy = run_strategy_for_symbol(db, row["symbol"], settings=settings)
        if combined_final_decision(row, strategy) != "BUY":
            result["skipped_strategy"] += 1
            continue
        send_private_message_sync(build_final_decision_report(db, row["symbol"], settings=settings), settings=settings)
        _mark_sent(db, key, value=f"sent:{datetime.utcnow().isoformat()}")
        result["sent"] += 1
    return result


async def send_buy_recommendation_alerts_async(db: Session, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    result = {"configured": alerts_configured(settings), "eligible": 0, "sent": 0, "skipped_duplicate": 0, "skipped_strategy": 0, "provider_status": None}
    if not result["configured"]:
        return result

    from app.services.telegram_bot import send_private_message

    run = build_final_recommendations(db, settings=settings, limit=500)
    result["provider_status"] = run.provider_status
    for row in run.rows:
        if not recommendation_is_alertable(row, settings):
            continue
        result["eligible"] += 1
        key = _recommendation_alert_key(row, run.generated_at)
        if _already_sent(db, key):
            result["skipped_duplicate"] += 1
            continue
        strategy = run_strategy_for_symbol(db, row["symbol"], settings=settings)
        if combined_final_decision(row, strategy) != "BUY":
            result["skipped_strategy"] += 1
            continue
        await send_private_message(build_final_decision_report(db, row["symbol"], settings=settings), settings=settings)
        _mark_sent(db, key, value=f"sent:{datetime.utcnow().isoformat()}")
        result["sent"] += 1
    return result
