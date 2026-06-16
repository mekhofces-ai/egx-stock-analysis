from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import DAILY_FILE_REPORT_TIME, DAILY_REPORT_TIMES, REPORT_TIMEZONE
from app.services.daily_file_report import build_daily_file_report as generate_daily_file_report  # noqa: N813
from app.database import SessionLocal, init_db
from app.services.daily_dynamic_refresh import run_daily_dynamic_refresh
from app.services.recommendation_performance import run_daily_re_evaluation, send_performance_report_to_telegram
from app.services.end_of_day_review import generate_end_of_day_review
from app.services.daily_stock_report import send_daily_stock_report
from app.services.dynamic_settings import get_bool, get_setting, seed_dynamic_settings
from app.services.learning_system import run_intraday_rescan


logger = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except Exception:  # pragma: no cover - optional dependency path
    BackgroundScheduler = None
    CronTrigger = None


def report_type_for_time(time_text: str) -> str:
    normalized = str(time_text or "").strip()
    if normalized.startswith("09:00"):
        return "morning"
    if normalized.startswith("21:00"):
        return "evening"
    hour = int(normalized.split(":", 1)[0])
    return "morning" if hour < 12 else "evening"


def _daily_report_times_from_db() -> list[str]:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        db.commit()
        value = get_setting(db, "daily_stock_report_times", ",".join(DAILY_REPORT_TIMES), value_type="string")
    times = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return times or list(DAILY_REPORT_TIMES)


def daily_reports_enabled() -> bool:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        db.commit()
        return get_bool(db, "daily_stock_report_enabled", True)


def _daily_file_report_time_from_db() -> str:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        db.commit()
        value = get_setting(db, "daily_file_report_time", DAILY_FILE_REPORT_TIME, value_type="string")
    value = str(value or DAILY_FILE_REPORT_TIME).strip()
    return value if ":" in value else DAILY_FILE_REPORT_TIME


def daily_file_report_enabled() -> bool:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        db.commit()
        return get_bool(db, "daily_file_report_enabled", True)


def _daily_dynamic_refresh_time_from_db() -> str:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        db.commit()
        value = get_setting(db, "daily_dynamic_refresh_time", "08:30", value_type="string")
    value = str(value or "08:30").strip()
    return value if ":" in value else "08:30"


def daily_dynamic_refresh_enabled() -> bool:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        db.commit()
        return get_bool(db, "daily_dynamic_refresh_enabled", True)


def _recommendation_re_evaluation_time_from_db() -> str:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        db.commit()
        value = get_setting(db, "recommendation_re_evaluation_time", "15:30", value_type="string")
    value = str(value or "15:30").strip()
    return value if ":" in value else "15:30"


def recommendation_re_evaluation_enabled() -> bool:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        db.commit()
        return get_bool(db, "recommendation_re_evaluation_enabled", True)


def end_of_day_review_enabled() -> bool:
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        db.commit()
        return get_bool(db, "end_of_day_review_enabled", True)


def _intraday_scan_times_from_db() -> dict[str, str]:
    defaults = {
        "after_open": "10:15",
        "mid_session": "11:30",
        "before_close": "14:00",
        "after_close": "15:05",
    }
    with SessionLocal() as db:
        seed_dynamic_settings(db)
        db.commit()
        enabled = get_bool(db, "intraday_rescan_enabled", True)
        value = get_setting(db, "intraday_rescan_times", "after_open=10:15,mid_session=11:30,before_close=14:00,after_close=15:05", value_type="string")
    if not enabled:
        return {}
    parsed = dict(defaults)
    for part in str(value or "").split(","):
        if "=" not in part:
            continue
        key, time_text = [item.strip() for item in part.split("=", 1)]
        if key and ":" in time_text:
            parsed[key] = time_text
    if parsed.get("after_close") == "15:00":
        parsed["after_close"] = "15:05"
    return parsed


def run_scheduled_intraday_scan(scan_type: str) -> dict:
    logger.info("Running scheduled intraday scan: %s.", scan_type)
    with SessionLocal() as db:
        result = run_intraday_rescan(db, scan_type=scan_type, persist=True)
        db.commit()
    return {"status": "success", "scan_type": scan_type, "items_count": len(result.get("items", [])), "run_id": result.get("run_id")}


def run_scheduled_report(report_type: str) -> dict:
    if not daily_reports_enabled():
        logger.info("Daily stock report skipped: disabled in settings.")
        return {"status": "disabled"}
    logger.info("Running scheduled %s daily stock report.", report_type)
    result = send_daily_stock_report(report_type=report_type, force=False)
    if report_type == "evening":
        try:
            perf = run_scheduled_re_evaluation(send_telegram=True)
            result["performance_report"] = perf
        except Exception as exc:
            logger.exception("Evening recommendation performance report failed.")
            result["performance_report"] = {"status": "failed", "error": str(exc)}
        try:
            eod = run_scheduled_end_of_day_review(send_telegram=True)
            result["end_of_day_review"] = eod
        except Exception as exc:
            logger.exception("Evening end-of-day review failed.")
            result["end_of_day_review"] = {"status": "failed", "error": str(exc)}
    return result


def run_scheduled_file_report() -> dict:
    if not daily_file_report_enabled():
        logger.info("Daily file report skipped: disabled in settings.")
        return {"status": "disabled"}
    from app.config import get_settings
    settings = get_settings()
    with SessionLocal() as db:
        send_telegram = get_bool(db, "daily_file_report_send_telegram", True)
    logger.info("Running scheduled daily file report at %s Cairo.", _daily_file_report_time_from_db())
    result = generate_daily_file_report(db=None, settings=settings)
    if send_telegram and result.get("excel_created"):
        from app.models import DailyFileReport
        from sqlalchemy import select
        with SessionLocal() as db2:
            report = db2.scalar(select(DailyFileReport).order_by(DailyFileReport.created_at.desc()))
            if report:
                from app.services.daily_file_report import send_daily_file_report_to_telegram
                send_daily_file_report_to_telegram(db2, report, settings)
    return result


def run_scheduled_dynamic_refresh() -> dict:
    if not daily_dynamic_refresh_enabled():
        logger.info("Daily dynamic refresh skipped: disabled in settings.")
        return {"status": "disabled"}
    logger.info("Running scheduled daily dynamic refresh at %s Cairo.", _daily_dynamic_refresh_time_from_db())
    return run_daily_dynamic_refresh(force=False, run_portfolio=True)


def run_scheduled_re_evaluation(*, send_telegram: bool = False) -> dict:
    if not recommendation_re_evaluation_enabled():
        logger.info("Recommendation re-evaluation skipped: disabled in settings.")
        return {"status": "disabled"}
    logger.info("Running recommendation re-evaluation at %s Cairo.", _recommendation_re_evaluation_time_from_db())
    result = run_daily_re_evaluation()
    telegram = None
    if send_telegram:
        telegram = send_performance_report_to_telegram(force=False)
    return {"status": "success", "evaluation": result, "telegram": telegram}


def run_scheduled_end_of_day_review(*, send_telegram: bool = False) -> dict:
    if not end_of_day_review_enabled():
        logger.info("End-of-day review skipped: disabled in settings.")
        return {"status": "disabled"}
    logger.info("Running end-of-day prediction review, timezone=%s.", REPORT_TIMEZONE)
    return generate_end_of_day_review(send_telegram=send_telegram, dry_run=False, persist=True)


def create_daily_report_scheduler():
    if BackgroundScheduler is None or CronTrigger is None:
        raise RuntimeError("APScheduler is not installed; install apscheduler or use the automation due-check.")
    timezone = ZoneInfo(REPORT_TIMEZONE)
    scheduler = BackgroundScheduler(timezone=timezone)
    for time_text in _daily_report_times_from_db():
        hour_text, minute_text = str(time_text).split(":", 1)
        report_type = report_type_for_time(time_text)
        scheduler.add_job(
            run_scheduled_report,
            trigger=CronTrigger(hour=int(hour_text), minute=int(minute_text), timezone=timezone),
            args=[report_type],
            id=f"daily_stock_report_{report_type}",
            name=f"EGX daily stock report {report_type}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )
    file_hour_text, file_minute_text = _daily_file_report_time_from_db().split(":", 1)
    scheduler.add_job(
        run_scheduled_file_report,
        trigger=CronTrigger(hour=int(file_hour_text), minute=int(file_minute_text), timezone=timezone),
        id="daily_file_report",
        name="EGX daily Excel/PDF file report",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=900,
    )
    refresh_hour_text, refresh_minute_text = _daily_dynamic_refresh_time_from_db().split(":", 1)
    scheduler.add_job(
        run_scheduled_dynamic_refresh,
        trigger=CronTrigger(hour=int(refresh_hour_text), minute=int(refresh_minute_text), timezone=timezone),
        id="daily_dynamic_refresh",
        name="EGX daily dynamic data refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )
    re_eval_hour_text, re_eval_minute_text = _recommendation_re_evaluation_time_from_db().split(":", 1)
    scheduler.add_job(
        run_scheduled_re_evaluation,
        trigger=CronTrigger(hour=int(re_eval_hour_text), minute=int(re_eval_minute_text), timezone=timezone),
        id="recommendation_re_evaluation",
        name="EGX recommendation performance re-evaluation",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )
    for scan_type, time_text in _intraday_scan_times_from_db().items():
        scan_hour_text, scan_minute_text = time_text.split(":", 1)
        scheduler.add_job(
            run_scheduled_intraday_scan,
            trigger=CronTrigger(hour=int(scan_hour_text), minute=int(scan_minute_text), timezone=timezone),
            args=[scan_type],
            id=f"intraday_rescan_{scan_type}",
            name=f"EGX intraday learning scan {scan_type}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=900,
        )
    return scheduler


def run_due_reports_once(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(ZoneInfo(REPORT_TIMEZONE))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(REPORT_TIMEZONE))
    current = now.strftime("%H:%M")
    results: list[dict] = []
    if daily_reports_enabled():
        for time_text in _daily_report_times_from_db():
            if current == time_text:
                report_type = report_type_for_time(time_text)
                results.append(run_scheduled_report(report_type))
    if daily_file_report_enabled() and current == _daily_file_report_time_from_db():
        results.append(run_scheduled_file_report())
    if daily_dynamic_refresh_enabled() and current == _daily_dynamic_refresh_time_from_db():
        results.append(run_scheduled_dynamic_refresh())
    if recommendation_re_evaluation_enabled() and current == _recommendation_re_evaluation_time_from_db():
        results.append(run_scheduled_re_evaluation(send_telegram=False))
    for scan_type, time_text in _intraday_scan_times_from_db().items():
        if current == time_text:
            results.append(run_scheduled_intraday_scan(scan_type))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EGX scheduled services.")
    parser.add_argument("--run-due-once", action="store_true", help="Run reports due at the current Cairo minute and exit.")
    parser.add_argument("--test", action="store_true", help="Print configured scheduler jobs and exit.")
    args = parser.parse_args()
    init_db(seed=True)
    logging.basicConfig(level=logging.INFO)
    if args.run_due_once:
        print(run_due_reports_once())
        return
    scheduler = create_daily_report_scheduler()
    if args.test:
        for job in scheduler.get_jobs():
            print(f"{job.id} | {job.trigger}")
        return
    scheduler.start()
    logger.info("EGX scheduler started with %s job(s), timezone=%s.", len(scheduler.get_jobs()), REPORT_TIMEZONE)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
