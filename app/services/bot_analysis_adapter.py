from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.config import DISCLAIMER, RISK_NOTE, Settings, get_settings
from app.database import SessionLocal
from app.models import FinalAnalysis, JobsLog, Opportunity, Stock, StrategyBacktest, TechnicalAnalysis, TradingViewScreeningRun
from app.services.automation_runner import get_automation_status, set_automation_enabled
from app.services.backtest_cli_v6 import format_cli_v6_backtest_report, get_latest_cli_v6_backtest_summary, run_cli_v6_backtest_symbol
from app.services.backtest_engine import format_backtest_report, get_latest_backtest_summary, run_symbol_backtest
from app.services.opportunity_engine import (
    calculate_opportunity,
    format_top_opportunities,
    get_top_opportunities as engine_get_top_opportunities,
    send_buy_alerts as send_opportunity_alerts,
    send_strategy_notifications,
)
from app.services.screener_recommendations import build_final_recommendations, tradingview_chart_url
from app.services.stock_analysis_engine import format_combined_analysis_report
from app.services.strategy_review import format_strategy_report
from app.services.strategies.cli_v6_egx import format_cli_v6_strategy_report, latest_cli_v6_result, run_cli_v6_for_symbol
from app.services.strategy_registry import latest_strategy_results, run_all_enabled_strategies
from app.services.backtest_queue import enqueue_backtest
from app.services.tradingview_screener import format_screening_report, latest_screening_summary, run_tradingview_screening


def _fmt(value: Any, digits: int = 0) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _compact(items: list[str] | None, limit: int = 2) -> str:
    if not items:
        return "-"
    return "; ".join(str(item) for item in items[:limit])


def _symbol(value: str) -> str:
    return value.strip().upper().replace("EGX:", "")


def _last_update_text(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "-"


def get_stock_analysis(symbol: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    symbol = _symbol(symbol)
    try:
        report = format_combined_analysis_report(symbol, settings=settings, refresh=True)
        if "Could not build combined analysis" not in report:
            return report
    except Exception:
        pass
    with SessionLocal() as db:
        stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
        try:
            rec_run = build_final_recommendations(db, settings=settings, limit=500)
            row = next((item for item in rec_run.rows if item.get("symbol") == symbol), None)
        except Exception as exc:
            rec_run = None
            row = None
            rec_warning = str(exc)
        else:
            rec_warning = rec_run.provider_warning

        latest_analysis = db.scalar(select(FinalAnalysis).where(FinalAnalysis.symbol == symbol).order_by(FinalAnalysis.created_at.desc()))
        latest_ta = db.scalar(select(TechnicalAnalysis).where(TechnicalAnalysis.symbol == symbol).order_by(TechnicalAnalysis.created_at.desc()))
        if row is None and latest_analysis is None:
            return f"No recent analysis found for {symbol}. Please run the analysis/update process first."

        opportunity = None
        if row:
            try:
                opportunity = calculate_opportunity(db, symbol=symbol, row=row, settings=settings)
            except Exception:
                opportunity = None
        cli_strategy = (((opportunity or {}).get("components_json") or {}).get("cli_v6_strategy") or latest_cli_v6_result(db, symbol))
        if cli_strategy is None:
            try:
                cli_strategy = run_cli_v6_for_symbol(db, symbol, settings=settings)
            except Exception:
                cli_strategy = {"recommendation": "INSUFFICIENT DATA", "confidence": 0.0}
        backtests = get_latest_backtest_summary(db, symbol=symbol, limit=4)
        cli_backtests = get_latest_cli_v6_backtest_summary(db, symbol=symbol, limit=4)
        screening = latest_screening_summary(db, symbol=symbol)
        screening_row = (screening.get("rows") or [{}])[0] if screening.get("available") and screening.get("rows") else {}

        company = (row or {}).get("name") or (stock.name_en if stock else None) or symbol
        latest_strategy = ((opportunity or {}).get("components_json") or {}).get("strategy") or {}
        best_backtest = max(cli_backtests or backtests, key=lambda item: item.get("score") or 0) if (cli_backtests or backtests) else {}
        support = _fmt(latest_ta.support, 2) if latest_ta else "-"
        resistance = _fmt(latest_ta.resistance, 2) if latest_ta else "-"
        last_update = None
        for candidate in [
            latest_analysis.created_at if latest_analysis else None,
            screening.get("completed_at") if screening.get("available") else None,
            best_backtest.get("updated_at") if best_backtest else None,
        ]:
            if candidate and (last_update is None or candidate > last_update):
                last_update = candidate

        recommendation = (opportunity or {}).get("recommendation") or (row or {}).get("final_recommendation") or (latest_analysis.final_decision if latest_analysis else "-")
        score = (opportunity or {}).get("final_score") or (row or {}).get("final_score") or (latest_analysis.confidence_score if latest_analysis else None)
        lines = [
            f"EGX Stock Analysis: {symbol}",
            f"Company: {company}",
            f"Recommendation: {recommendation}",
            f"Score/confidence: {_fmt(score)}%",
            f"Entry zone: {(row or {}).get('smart_buy_zone') or '-'}",
            f"Entry price: {_fmt((opportunity or {}).get('entry_price') or (row or {}).get('smart_suggested_entry'), 2)}",
            f"Target price: {_fmt((opportunity or {}).get('target_price') or (row or {}).get('smart_target_swing'), 2)}",
            f"Stop loss: {_fmt((opportunity or {}).get('stop_loss') or (row or {}).get('smart_suggested_stop'), 2)}",
            f"Support/resistance: {support} / {resistance}",
            f"Trend: {(row or {}).get('smart_main_trend') or (latest_ta.trend_direction if latest_ta else '-')}",
            f"CLI v6 strategy: {cli_strategy.get('recommendation') or '-'} | confidence {_fmt(cli_strategy.get('confidence'))}%",
            f"Latest strategy signal: {latest_strategy.get('action') or '-'} {_fmt(latest_strategy.get('score'))}%",
            f"TradingView screener: {screening_row.get('recommendation') or (row or {}).get('final_recommendation') or '-'} | TV {screening_row.get('tv_vote') or (row or {}).get('tv_vote') or '-'}",
            f"Backtest: {best_backtest.get('recommendation') or '-'} | {best_backtest.get('timeframe') or '-'} | score {_fmt(best_backtest.get('score'))}%",
            f"Last update: {_last_update_text(last_update)}",
            "",
            f"Explanation: {(opportunity or {}).get('reason') or _compact((row or {}).get('reasons'), 3)}",
            f"Risk Note: {RISK_NOTE}",
            f"Chart: {tradingview_chart_url(symbol)}",
        ]
        if rec_warning:
            lines.extend(["", f"Data warning: {rec_warning}"])
        lines.append(f"Disclaimer: {DISCLAIMER}")
        return "\n".join(lines)


def get_top_opportunities(limit: int = 5, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    with SessionLocal() as db:
        return format_top_opportunities(db, settings=settings, limit=limit)


def get_market_summary(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    with SessionLocal() as db:
        stock_count = db.query(Stock).filter(Stock.is_active.is_(True)).count()
        opportunities = get_top_opportunities_rows(db, settings=settings, limit=50)
        if opportunities:
            buy_count = sum(1 for row in opportunities if row.get("recommendation") == "BUY")
            watch_count = sum(1 for row in opportunities if row.get("recommendation") == "WATCH")
            avoid_count = sum(1 for row in opportunities if row.get("recommendation") == "AVOID")
            top = ", ".join(row["symbol"] for row in opportunities[:5])
            avg_score = sum(float(row.get("final_score") or 0) for row in opportunities) / len(opportunities)
            bias = "BULLISH" if buy_count >= watch_count and avg_score >= 65 else "SELECTIVE/WATCH" if buy_count + watch_count else "NEUTRAL"
        else:
            try:
                rec_run = build_final_recommendations(db, settings=settings, limit=500)
                rows = rec_run.rows
            except Exception:
                rows = []
            buy_count = sum(1 for row in rows if row.get("final_recommendation") == "BUY")
            watch_count = sum(1 for row in rows if row.get("final_recommendation") == "WATCH")
            avoid_count = sum(1 for row in rows if row.get("final_recommendation") in {"AVOID", "SELL", "HIGH_RISK"})
            top = ", ".join(row["symbol"] for row in rows[:5]) if rows else "-"
            bias = "BULLISH" if buy_count >= watch_count and buy_count > 0 else "SELECTIVE/WATCH" if watch_count else "NEUTRAL"
        latest = db.scalar(select(func.max(Opportunity.updated_at)))
        return (
            "EGX Market Summary\n"
            f"Tracked stocks: {stock_count}\n"
            f"BUY: {buy_count} | WATCH/HOLD: {watch_count} | AVOID/SELL: {avoid_count}\n"
            f"Best opportunities: {top or '-'}\n"
            f"Market bias: {bias}\n"
            f"Last update: {_last_update_text(latest)}\n"
            f"Disclaimer: {DISCLAIMER}"
        )


def get_top_opportunities_rows(db, settings: Settings | None = None, limit: int = 5) -> list[dict[str, Any]]:  # noqa: ANN001
    settings = settings or get_settings()
    return engine_get_top_opportunities(db, settings=settings, limit=limit, refresh_if_stale=True)


def get_latest_recommendations(limit: int = 10) -> str:
    with SessionLocal() as db:
        latest = db.scalars(select(FinalAnalysis).order_by(FinalAnalysis.created_at.desc()).limit(limit)).all()
        if not latest:
            rows = db.scalars(select(Opportunity).order_by(Opportunity.updated_at.desc()).limit(limit)).all()
            if not rows:
                return "No latest recommendations are stored yet. Please run the analysis/update process first."
            lines = ["Latest Opportunity Recommendations", ""]
            for row in rows:
                lines.append(f"- {row.symbol}: {row.recommendation} | score {row.final_score:.0f}% | {row.updated_at:%Y-%m-%d %H:%M}")
            lines.append(f"Disclaimer: {DISCLAIMER}")
            return "\n".join(lines)
        lines = ["Latest System Recommendations", ""]
        for item in latest:
            lines.append(f"- {item.symbol}: {item.final_decision} | confidence {item.confidence_score:.0f}% | {item.created_at:%Y-%m-%d %H:%M}")
        lines.append(f"Disclaimer: {DISCLAIMER}")
        return "\n".join(lines)


def get_strategy_summary(symbol: str) -> str:
    symbol = _symbol(symbol)
    with SessionLocal() as db:
        try:
            run_all_enabled_strategies(symbol=symbol, db=db)
        except Exception:
            pass
        rows = latest_strategy_results(db, symbol=symbol, limit=20)
        lines = [f"Strategy Comparison: {symbol}", ""]
        if rows:
            seen: set[str] = set()
            for row in rows:
                key = row["strategy_code"]
                if key in seen:
                    continue
                seen.add(key)
                lines.append(
                    f"- {row['strategy_name']}: {row.get('recommendation') or row.get('signal') or '-'} | "
                    f"score {_fmt(row.get('score'))}% | confidence {_fmt(row.get('confidence'))}%"
                )
        else:
            lines.append("No shared strategy results are stored yet.")
        lines.extend(["", format_cli_v6_strategy_report(db, symbol), "", "Legacy detail", format_strategy_report(db, symbol)])
        return "\n".join(lines)


def get_backtest_summary(symbol: str) -> str:
    symbol = _symbol(symbol)
    with SessionLocal() as db:
        enqueue_backtest(db, symbol, reason="Bot backtest summary refresh", priority=2, requested_by="bot_adapter")
        db.commit()
        rows = get_latest_cli_v6_backtest_summary(db, symbol=symbol, limit=4)
        if not rows:
            try:
                run_cli_v6_backtest_symbol(db, symbol=symbol, timeframe="1d")
            except Exception:
                pass
        return format_cli_v6_backtest_report(db, symbol)


def get_tradingview_screening_summary(symbol: str | None = None) -> str:
    with SessionLocal() as db:
        summary = latest_screening_summary(db, symbol=_symbol(symbol) if symbol else None)
        if not summary.get("available"):
            run_tradingview_screening(db)
        if symbol:
            refreshed = latest_screening_summary(db, symbol=_symbol(symbol))
            rows = refreshed.get("rows") or []
            if not rows:
                run_tradingview_screening(db, limit=500)
                refreshed = latest_screening_summary(db, symbol=_symbol(symbol))
                rows = refreshed.get("rows") or []
            if not rows:
                return f"No TradingView screening row found for {_symbol(symbol)} after a full screening refresh.\nDisclaimer: {DISCLAIMER}"
            row = rows[0]
            return (
                f"TradingView Screening: {row['symbol']}\n"
                f"Recommendation: {row.get('recommendation')}\n"
                f"Score: {_fmt(row.get('final_score'))}% | TV {row.get('tv_vote') or '-'} | Telegram {row.get('telegram_vote') or '-'}\n"
                f"Close: {_fmt(row.get('close'), 2)} | Change: {_fmt(row.get('change_percent'), 2)}% | RSI {_fmt(row.get('rsi'), 1)}\n"
                f"Technical: {_fmt(row.get('technical_rating'), 2)} | MA {_fmt(row.get('moving_averages_rating'), 2)} | Osc {_fmt(row.get('oscillators_rating'), 2)}\n"
                f"Disclaimer: {DISCLAIMER}"
            )
        return format_screening_report(db)


def get_combined_opportunity_score(symbol: str) -> str:
    symbol = _symbol(symbol)
    with SessionLocal() as db:
        try:
            payload = calculate_opportunity(db, symbol=symbol)
        except Exception as exc:
            return f"No recent analysis found for {symbol}. Please run the analysis/update process first. ({exc})"
        components = payload.get("components_json") or {}
        scores = components.get("components") or {}
        return (
            f"Combined Opportunity Score: {symbol}\n"
            f"Final score: {payload.get('final_score') or 0:.0f}%\n"
            f"Recommendation: {payload.get('recommendation')}\n"
            f"Recommendation source: {_fmt(scores.get('recommendation'))}%\n"
            f"Strategy: {_fmt(scores.get('strategy'))}%\n"
            f"Backtest: {_fmt(scores.get('backtest'))}%\n"
            f"TradingView: {_fmt(scores.get('tradingview'))}%\n"
            f"Telegram: {_fmt(scores.get('telegram'))}%\n"
            f"Risk score: {_fmt(components.get('risk_score'))}% | Freshness {_fmt(components.get('freshness_score'))}%\n"
            f"Disclaimer: {DISCLAIMER}"
        )


def send_buy_alerts() -> str:
    with SessionLocal() as db:
        strategy_result = send_strategy_notifications(db)
        result = send_opportunity_alerts(db)
    if not result.get("configured"):
        return "Telegram alerts are not configured. Add TELEGRAM_BOT_TOKEN and at least one active subscriber/admin chat."
    return (
        "Opportunity alerts completed.\n"
        f"Strategy alerts sent: {strategy_result.get('sent', 0)}\n"
        f"Eligible: {result.get('eligible', 0)}\n"
        f"Sent: {result.get('sent', 0)}\n"
        f"Duplicates skipped: {result.get('skipped_duplicate', 0)}\n"
        f"Risk Note: {RISK_NOTE}"
    )


def get_status_report(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    with SessionLocal() as db:
        try:
            db.scalar(select(Stock.id).limit(1))
            db_status = "ok"
        except Exception as exc:
            db_status = f"error: {exc}"
        last_job = db.scalar(select(JobsLog).order_by(JobsLog.finished_at.desc().nullslast(), JobsLog.started_at.desc()))
        last_screening = db.scalar(select(TradingViewScreeningRun).order_by(TradingViewScreeningRun.created_at.desc()))
        last_backtest = db.scalar(select(func.max(StrategyBacktest.completed_at)))
        last_opportunity = db.scalar(select(func.max(Opportunity.updated_at)))
        automation = get_automation_status(db, settings=settings)
        return (
            "EGX Bot Status\n"
            f"Bot status: {'configured' if settings.telegram_bot_token else 'token missing'}\n"
            f"Database connection: {db_status}\n"
            f"Last data update: {last_job.job_name + ' ' + _last_update_text(last_job.finished_at or last_job.started_at) if last_job else '-'}\n"
            f"Last TradingView screening: {_last_update_text((last_screening.completed_at or last_screening.created_at) if last_screening else None)}\n"
            f"Last backtest update: {_last_update_text(last_backtest)}\n"
            f"Last opportunity update: {_last_update_text(last_opportunity)}\n"
            f"Automation running: {'yes' if automation.get('running') else 'no'} | enabled: {'yes' if automation.get('enabled') else 'no'}\n"
            f"Automation interval: {automation.get('interval_seconds')} seconds\n"
            f"Last automation alerts: {automation.get('last_alert_count')}\n"
            f"Risk Note: {RISK_NOTE}"
        )


def get_watchlist(limit: int = 10) -> str:
    with SessionLocal() as db:
        rows = get_top_opportunities_rows(db, limit=limit)
    if not rows:
        return "No watchlist is available yet. Please run the analysis/update process first."
    lines = ["EGX Watchlist", ""]
    for row in rows[:limit]:
        if row.get("recommendation") in {"BUY", "WATCH", "NEUTRAL"}:
            lines.append(f"- {row['symbol']}: {row.get('recommendation')} | score {row.get('final_score') or 0:.0f}% | entry {row.get('entry_price') or '-'}")
    lines.append(f"Disclaimer: {DISCLAIMER}")
    return "\n".join(lines)


def get_automation_status_report(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    with SessionLocal() as db:
        status = get_automation_status(db, settings=settings)
    return (
        "Automation Status\n"
        f"Enabled: {'yes' if status.get('enabled') else 'no'}\n"
        f"Running: {'yes' if status.get('running') else 'no'}\n"
        f"Interval: {status.get('interval_seconds')} seconds\n"
        f"Last run: {status.get('last_run_time') or '-'}\n"
        f"Last finished: {status.get('last_finished_at') or '-'}\n"
        f"Next run: {status.get('next_run_time') or '-'}\n"
        f"Last status: {status.get('last_status') or '-'}\n"
        f"Symbols processed: {status.get('symbols_processed') or 0}\n"
        f"Opportunities saved: {status.get('opportunities_count') or 0}\n"
        f"Last Telegram alert count: {status.get('last_alert_count') or 0}\n"
        f"Last error: {status.get('last_error') or '-'}\n"
        f"Risk Note: {RISK_NOTE}"
    )


def automation_start_report() -> str:
    set_automation_enabled(True)
    return "Automation enabled. Start the runner with python app/services/automation_runner.py if it is not already running.\n" f"Risk Note: {RISK_NOTE}"


def automation_stop_report() -> str:
    set_automation_enabled(False)
    return "Automation disabled. Any current cycle will finish, then the runner will idle.\n" f"Risk Note: {RISK_NOTE}"
