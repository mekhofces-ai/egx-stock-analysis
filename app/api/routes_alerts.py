from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from sqlalchemy import select

from app.models import AppSetting, BotUser, TelegramSubscriber
from app.services.alerts import alerts_configured, send_buy_recommendation_alerts, send_pending_buy_signal_alerts


router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/status")
def alerts_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    return {
        "enabled": settings.telegram_alert_enabled,
        "configured": alerts_configured(settings),
        "bot_token_configured": bool(settings.telegram_bot_token),
        "active_alert_subscribers": db.query(TelegramSubscriber)
        .filter(TelegramSubscriber.is_active.is_(True), TelegramSubscriber.can_receive_alerts.is_(True))
        .count(),
        "admin_chat_count": len(settings.allowed_chat_ids),
        "approved_user_count": db.query(BotUser).filter(BotUser.is_active.is_(True)).count(),
        "pending_user_count": db.query(BotUser).filter(BotUser.is_active.is_(False)).count(),
        "telegram_listener_status": db.scalar(select(AppSetting.value).where(AppSetting.key == "telegram_listener_status")) or "not run yet",
        "decisions": sorted(settings.alert_decision_set),
        "min_confidence": settings.telegram_alert_min_confidence,
        "recommendation_alerts_enabled": settings.telegram_alert_recommendations_enabled,
        "require_telegram_confirmation": settings.telegram_alert_require_telegram_confirmation,
        "scan_interval_minutes": settings.telegram_alert_scan_interval_minutes,
        "market_data_provider_priority": settings.market_data_provider_priority,
        "market_data_allow_mock": settings.market_data_allow_mock,
        "strategy_allow_mock_data": settings.strategy_allow_mock_data,
        "strategy_price_tolerance_percent": settings.strategy_price_tolerance_percent,
    }


@router.post("/send-buy-now")
def send_buy_alerts_now(
    include_signals: bool = Query(default=True),
    include_recommendations: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result: dict[str, Any] = {"signals": None, "recommendations": None}
    if include_signals:
        result["signals"] = send_pending_buy_signal_alerts(db)
    if include_recommendations:
        result["recommendations"] = send_buy_recommendation_alerts(db)
    return result
