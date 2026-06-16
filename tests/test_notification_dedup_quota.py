from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import NotificationLog
from app.services.notification_dedup import mark_sent, should_send


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_same_stock_same_recommendation_is_blocked_for_day():
    db = _session()
    mark_sent(db, "COMI", "CONDITIONAL BUY", "TRADING_BUY", source_module="test")
    db.commit()

    ok, reason = should_send(db, "COMI", "CONDITIONAL BUY", "TRADING_BUY")
    assert not ok
    assert "already sent today" in reason


def test_daily_buy_alert_limit_blocks_after_threshold():
    db = _session()
    cairo_now = datetime.now(ZoneInfo("Africa/Cairo")).replace(tzinfo=None)
    for idx, symbol in enumerate(["COMI", "HRHO", "TMGH"], start=1):
        db.add(
            NotificationLog(
                notification_hash=f"h{idx}",
                symbol=symbol,
                notification_type="TRADING_BUY",
                recommendation="CONDITIONAL BUY",
                source_module="test",
                delivery_status="sent",
                sent_at=cairo_now,
            )
        )
    db.commit()

    ok, reason = should_send(db, "ORAS", "CONDITIONAL BUY", "TRADING_BUY", max_buy_alerts_per_day=3)
    assert not ok
    assert "Daily BUY alert limit" in reason
