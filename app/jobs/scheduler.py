from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import JobsLog
from app.services.alerts import send_buy_alerts_for_analyses
from app.services.analysis_runner import analyze_pending_signals
from app.services.backtest_engine import run_universe_backtests
from app.services.opportunity_engine import refresh_opportunities, send_buy_alerts as send_opportunity_buy_alerts
from app.services.performance_tracker import update_channel_performance
from app.services.daily_stock_report import send_daily_stock_report
from app.services.reports import send_daily_report, send_night_opportunity_report, send_afternoon_report
from app.services.telegram_listener import fetch_active_channels_once

logger = logging.getLogger(__name__)


def _run_logged(job_name: str, func) -> None:
    with SessionLocal() as db:
        log = JobsLog(job_name=job_name, status="running", started_at=datetime.utcnow())
        db.add(log)
        db.commit()
        try:
            details = func()
            log.status = "success"
            log.details = str(details)
        except Exception as exc:
            logger.exception("Job %s failed", job_name)
            log.status = "failed"
            log.details = str(exc)
        finally:
            log.finished_at = datetime.utcnow()
            db.commit()


def fetch_telegram_job() -> None:
    _run_logged("fetch_telegram_messages", lambda: {"inserted": fetch_active_channels_once()})


def analyze_pending_job() -> None:
    def _job():
        with SessionLocal() as db:
            analyses = analyze_pending_signals(db)
            alerts = send_buy_alerts_for_analyses(db, analyses)
            return {"analyzed": len(analyses), "signal_alerts": alerts}

    _run_logged("analyze_pending_messages", _job)


def opportunity_refresh_job() -> None:
    def _job():
        with SessionLocal() as db:
            return refresh_opportunities(db, limit=500, run_screening=True)

    _run_logged("refresh_opportunities", _job)


def reviewed_backtest_job() -> None:
    def _job():
        with SessionLocal() as db:
            result = run_universe_backtests(db, limit=get_settings().strategy_symbol_limit)
            return {"symbols": len(result.get("rows", []))}

    _run_logged("reviewed_backtests", _job)


def buy_recommendation_alert_job() -> None:
    def _job():
        with SessionLocal() as db:
            return send_opportunity_buy_alerts(db)

    _run_logged("buy_recommendation_alerts", _job)


def performance_job() -> None:
    def _job():
        with SessionLocal() as db:
            performances = update_channel_performance(db)
            return {"channels": len(performances)}

    _run_logged("update_channel_performance", _job)


def daily_report_job() -> None:
    _run_logged("daily_report", send_daily_report)


def night_opportunity_report_job() -> None:
    _run_logged("night_opportunity_report", send_night_opportunity_report)


def start_scheduler(settings: Settings | None = None) -> BackgroundScheduler | None:
    settings = settings or get_settings()
    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled.")
        return None
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    now = datetime.now(ZoneInfo(settings.timezone))
    job_defaults = {"max_instances": 1, "coalesce": True}
    scheduler.add_job(
        fetch_telegram_job,
        "interval",
        minutes=settings.telegram_fetch_interval_minutes,
        id="fetch_telegram",
        replace_existing=True,
        next_run_time=now,
        **job_defaults,
    )
    scheduler.add_job(
        analyze_pending_job,
        "interval",
        minutes=settings.analysis_interval_minutes,
        id="analyze_pending",
        replace_existing=True,
        next_run_time=now + timedelta(seconds=25),
        **job_defaults,
    )
    scheduler.add_job(
        opportunity_refresh_job,
        "interval",
        minutes=settings.telegram_alert_scan_interval_minutes,
        id="refresh_opportunities",
        replace_existing=True,
        next_run_time=now + timedelta(seconds=45),
        **job_defaults,
    )
    scheduler.add_job(
        buy_recommendation_alert_job,
        "interval",
        minutes=settings.telegram_alert_scan_interval_minutes,
        id="buy_recommendation_alerts",
        replace_existing=True,
        next_run_time=now + timedelta(seconds=75),
        **job_defaults,
    )
    scheduler.add_job(
        reviewed_backtest_job,
        "interval",
        minutes=settings.backtest_interval_minutes,
        id="reviewed_backtests",
        replace_existing=True,
        next_run_time=now + timedelta(seconds=130),
        **job_defaults,
    )
    scheduler.add_job(
        performance_job,
        "interval",
        minutes=settings.performance_interval_minutes,
        id="performance",
        replace_existing=True,
        next_run_time=now + timedelta(minutes=3),
        **job_defaults,
    )
    # 09:00 Cairo - Morning stock recommendation report (was MISSING - root cause of no morning report)
    scheduler.add_job(
        lambda: send_daily_stock_report(report_type="morning"),
        "cron",
        hour=9, minute=0,
        id="morning_stock_report",
        replace_existing=True,
        **job_defaults,
    )
    scheduler.add_job(daily_report_job, "cron", hour=settings.daily_report_hour, minute=0, id="daily_report", replace_existing=True, **job_defaults)
    if settings.night_opportunity_report_enabled:
        scheduler.add_job(
            night_opportunity_report_job,
            "cron",
            hour=settings.night_opportunity_report_hour,
            minute=0,
            id="night_opportunity_report",
            replace_existing=True,
            **job_defaults,
        )
    # 15:00 Cairo - Afternoon report with morning review (added by upgrade)
    scheduler.add_job(
        lambda: send_afternoon_report(),
        "cron",
        hour=15, minute=0,
        id="afternoon_report_15",
        replace_existing=True,
        **job_defaults,
    )
    # 21:00 Cairo - Evening stock recommendation report
    scheduler.add_job(
        lambda: send_daily_stock_report(report_type="evening"),
        "cron",
        hour=21, minute=0,
        id="evening_stock_report",
        replace_existing=True,
        **job_defaults,
    )
    scheduler.start()
    logger.info("Scheduler started with %s jobs.", len(scheduler.get_jobs()))
    return scheduler
