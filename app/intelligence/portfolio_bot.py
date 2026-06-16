from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.egx_symbols import list_active_symbols
from app.data.market_data import latest_price
from app.intelligence.final_decision_engine import build_final_decision, latest_final_decision
from app.intelligence.trade_approval import create_trade_approval
from app.models import FinalStockDecision, Opportunity, PortfolioPosition, PortfolioSetting, PortfolioTrade
from app.services.trading_safety import guard_trade_execution, journal_trade_event


CAIRO_TZ = ZoneInfo("Africa/Cairo")


def cairo_now() -> datetime:
    return datetime.now(CAIRO_TZ)


def get_portfolio_settings(db: Session) -> PortfolioSetting:
    row = db.scalar(select(PortfolioSetting).order_by(PortfolioSetting.id.asc()))
    if not row:
        row = PortfolioSetting()
        db.add(row)
        db.flush()
    return row


def portfolio_value(db: Session, settings: PortfolioSetting | None = None) -> dict[str, float]:
    settings = settings or get_portfolio_settings(db)
    positions = db.scalars(select(PortfolioPosition).where(PortfolioPosition.status == "open")).all()
    invested = 0.0
    for pos in positions:
        price = latest_price(db, pos.symbol) or pos.current_price or pos.buy_price
        pos.current_price = price
        pos.unrealized_profit = (price - pos.buy_price) * pos.quantity
        pos.unrealized_profit_pct = (price - pos.buy_price) / pos.buy_price * 100 if pos.buy_price else 0
        invested += price * pos.quantity
    total = settings.current_cash + invested
    pnl = total - settings.initial_cash
    return {
        "cash": round(settings.current_cash, 2),
        "invested": round(invested, 2),
        "total_value": round(total, 2),
        "profit_loss": round(pnl, 2),
        "profit_loss_pct": round(pnl / settings.initial_cash * 100, 2) if settings.initial_cash else 0.0,
    }


def _open_position(db: Session, symbol: str) -> PortfolioPosition | None:
    return db.scalar(select(PortfolioPosition).where(PortfolioPosition.symbol == symbol, PortfolioPosition.status == "open"))


def portfolio_scan_universe(db: Session, *, symbols: list[str] | None = None, limit: int = 50) -> list[str]:
    if symbols:
        raw = symbols
    else:
        raw = []
        raw.extend(
            row.symbol
            for row in db.scalars(
                select(Opportunity).order_by(Opportunity.final_score.desc(), Opportunity.updated_at.desc()).limit(limit)
            ).all()
        )
        raw.extend(
            row.symbol
            for row in db.scalars(
                select(FinalStockDecision)
                .where(FinalStockDecision.final_signal.in_(["STRONG BUY", "BUY"]))
                .order_by(FinalStockDecision.final_score.desc(), FinalStockDecision.decision_date.desc())
                .limit(limit)
            ).all()
        )
        raw.extend(list_active_symbols(db, limit=limit))
    universe: list[str] = []
    seen: set[str] = set()
    for symbol in raw:
        normalized = str(symbol or "").upper().replace("EGX:", "").replace(".CA", "").strip()
        if not normalized or normalized in seen:
            continue
        universe.append(normalized)
        seen.add(normalized)
        if len(universe) >= limit:
            break
    return universe


def calculate_quantity(settings: PortfolioSetting, entry: float, stop_loss: float | None, total_value: float) -> int:
    if entry <= 0:
        return 0
    max_position_value = total_value * (settings.max_position_size_pct / 100)
    max_by_position = math.floor(max_position_value / entry)
    if stop_loss and entry > stop_loss:
        risk_amount = total_value * (settings.max_risk_per_trade_pct / 100)
        risk_per_share = max(entry - stop_loss, 0.0001)
        max_by_risk = math.floor(risk_amount / risk_per_share)
        return max(0, min(max_by_position, max_by_risk))
    return max(0, max_by_position)


def _trade_components(decision: FinalStockDecision | dict[str, Any]) -> dict[str, float | None]:
    if isinstance(decision, dict):
        components = decision.get("components", {})
        scores = components.get("scores", {}) if isinstance(components, dict) else {}
        return {
            "technical_score": scores.get("technical"),
            "financial_score": scores.get("financial"),
            "news_score": scores.get("news"),
            "telegram_score": scores.get("telegram"),
            "strategy_score": scores.get("strategy"),
        }
    return {
        "technical_score": decision.technical_score,
        "financial_score": decision.financial_score,
        "news_score": decision.news_score,
        "telegram_score": decision.telegram_score,
        "strategy_score": decision.strategy_score,
    }


def execute_paper_buy(
    db: Session,
    decision: FinalStockDecision | dict[str, Any],
    *,
    force: bool = False,
    notify: bool = True,
) -> dict[str, Any]:
    settings = get_portfolio_settings(db)
    blocked = guard_trade_execution(db, execution_type="paper_buy")
    if blocked:
        blocked["symbol"] = decision["symbol"] if isinstance(decision, dict) else decision.symbol
        return blocked
    symbol = decision["symbol"] if isinstance(decision, dict) else decision.symbol
    final_signal = decision["final_signal"] if isinstance(decision, dict) else decision.final_signal
    final_score = float((decision["final_score"] if isinstance(decision, dict) else decision.final_score) or 0)
    risk_level = decision["risk_level"] if isinstance(decision, dict) else decision.risk_level
    try:
        from app.intelligence.risk_guard import risk_guard_status

        guard = risk_guard_status(db, settings)
        if not guard.get("allowed") and not force:
            return {"status": "blocked_by_risk_guard", "symbol": symbol, "reason": ", ".join(guard.get("reasons") or [])}
    except Exception:
        guard = {"allowed": True, "reasons": []}
    if settings.trading_mode != "paper_trading":
        return {"status": "skipped", "reason": "Only paper_trading mode is supported."}
    if settings.portfolio_bot_enabled is False and not force:
        return {"status": "skipped", "reason": "Portfolio bot is disabled."}
    if final_signal not in {"STRONG BUY", "BUY"} or final_score < settings.minimum_final_score_to_buy:
        return {"status": "skipped", "reason": "Final signal/score does not meet buy threshold."}
    if risk_level == "HIGH" and not settings.allow_high_risk_trades:
        return {"status": "skipped", "reason": "High risk trades are disabled."}
    if _open_position(db, symbol):
        return {"status": "skipped", "reason": "Position already open."}
    open_count = db.query(PortfolioPosition).filter(PortfolioPosition.status == "open").count()
    if open_count >= settings.max_open_positions:
        return {"status": "skipped", "reason": "Maximum open positions reached."}
    entry = float((decision["entry_price"] if isinstance(decision, dict) else decision.entry_price) or latest_price(db, symbol) or 0)
    stop = decision["stop_loss"] if isinstance(decision, dict) else decision.stop_loss
    tp1 = decision["take_profit_1"] if isinstance(decision, dict) else decision.take_profit_1
    tp2 = decision["take_profit_2"] if isinstance(decision, dict) else decision.take_profit_2
    values = portfolio_value(db, settings)
    quantity = calculate_quantity(settings, entry, stop, values["total_value"])
    total_cost = quantity * entry
    if quantity <= 0 or total_cost > settings.current_cash:
        return {"status": "skipped", "reason": "Insufficient cash or risk sizing produced zero quantity."}
    if settings.require_manual_buy_confirmation and not force:
        approval = create_trade_approval(
            db,
            {
                "symbol": symbol,
                "side": "BUY",
                "price": entry,
                "quantity": quantity,
                "total_value": total_cost,
                "final_score": final_score,
                "final_signal": final_signal,
                "reason": decision["reason"] if isinstance(decision, dict) else decision.reason,
            },
            requested_by="portfolio_bot",
        )
        db.flush()
        return {
            "status": "pending_approval",
            "symbol": symbol,
            "approval_id": approval.id,
            "price": entry,
            "quantity": quantity,
            "total_cost": total_cost,
            "final_score": final_score,
            "final_signal": final_signal,
            "reason": decision["reason"] if isinstance(decision, dict) else decision.reason,
        }
    now = cairo_now()
    settings.current_cash -= total_cost
    pos = PortfolioPosition(
        symbol=symbol,
        buy_date=now.replace(tzinfo=None),
        buy_price=entry,
        quantity=quantity,
        total_cost=total_cost,
        stop_loss=stop,
        take_profit_1=tp1,
        take_profit_2=tp2,
        status="open",
        current_price=entry,
        unrealized_profit=0,
        unrealized_profit_pct=0,
    )
    db.add(pos)
    comps = _trade_components(decision)
    db.add(
        PortfolioTrade(
            symbol=symbol,
            trade_type="BUY",
            trade_date=now.replace(tzinfo=None),
            price=entry,
            quantity=quantity,
            total_value=total_cost,
            reason=decision["reason"] if isinstance(decision, dict) else decision.reason,
            final_score=final_score,
            profit_loss=0,
            profit_loss_pct=0,
            cairo_timestamp=now.strftime("%Y-%m-%d %H:%M Cairo Time"),
            **comps,
        )
    )
    journal_trade_event(
        db,
        {
            "symbol": symbol,
            "signal": final_signal,
            "entry_zone": f"{entry:.4f}",
            "actual_entry": entry,
            "stop_loss": stop,
            "targets": {"take_profit_1": tp1, "take_profit_2": tp2},
            "result": "OPEN",
            "pnl": 0,
            "pnl_pct": 0,
            "reason_for_entry": decision["reason"] if isinstance(decision, dict) else decision.reason,
        },
    )
    action = {
        "status": "bought",
        "symbol": symbol,
        "price": entry,
        "quantity": quantity,
        "total_cost": total_cost,
        "cash_after": settings.current_cash,
        "final_score": final_score,
        "final_signal": final_signal,
        "reason": decision["reason"] if isinstance(decision, dict) else decision.reason,
        "stop_loss": stop,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        **comps,
    }
    if notify:
        try:
            notify_portfolio_action(action)
        except Exception:
            pass
    return action


def execute_paper_sell(
    db: Session,
    position: PortfolioPosition,
    *,
    reason: str,
    price: float | None = None,
    force: bool = False,
    notify: bool = True,
) -> dict[str, Any]:
    settings = get_portfolio_settings(db)
    blocked = guard_trade_execution(db, execution_type="paper_sell")
    if blocked:
        blocked["symbol"] = position.symbol
        return blocked
    if settings.require_manual_sell_confirmation and not force:
        return {"status": "pending_confirmation", "reason": "Manual sell confirmation is enabled."}
    sell_price = float(price or latest_price(db, position.symbol) or position.current_price or position.buy_price)
    now = cairo_now()
    total_value = sell_price * position.quantity
    pnl = total_value - position.total_cost
    pnl_pct = pnl / position.total_cost * 100 if position.total_cost else 0
    position.status = "closed"
    position.current_price = sell_price
    position.unrealized_profit = pnl
    position.unrealized_profit_pct = pnl_pct
    settings.current_cash += total_value
    db.add(
        PortfolioTrade(
            symbol=position.symbol,
            trade_type="SELL",
            trade_date=now.replace(tzinfo=None),
            price=sell_price,
            quantity=position.quantity,
            total_value=total_value,
            reason=reason,
            profit_loss=pnl,
            profit_loss_pct=pnl_pct,
            cairo_timestamp=now.strftime("%Y-%m-%d %H:%M Cairo Time"),
        )
    )
    journal_trade_event(
        db,
        {
            "symbol": position.symbol,
            "signal": "SELL",
            "actual_entry": position.buy_price,
            "stop_loss": position.stop_loss,
            "targets": {"take_profit_1": position.take_profit_1, "take_profit_2": position.take_profit_2},
            "exit_price": sell_price,
            "result": "CLOSED",
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason_for_exit": reason,
            "mistake_type": "LOSS_REVIEW_REQUIRED" if pnl < 0 else None,
        },
    )
    action = {
        "status": "sold",
        "symbol": position.symbol,
        "price": sell_price,
        "quantity": position.quantity,
        "total_value": total_value,
        "buy_price": position.buy_price,
        "profit_loss": pnl,
        "profit_loss_pct": pnl_pct,
        "cash_after": settings.current_cash,
        "reason": reason,
    }
    if notify:
        try:
            notify_portfolio_action(action)
        except Exception:
            pass
    return action


def scan_portfolio(db: Session, *, symbols: list[str] | None = None, execute: bool = False, force: bool = False, limit: int = 50) -> dict[str, Any]:
    settings = get_portfolio_settings(db)
    universe = portfolio_scan_universe(db, symbols=symbols, limit=limit)
    actions: list[dict[str, Any]] = []
    for pos in db.scalars(select(PortfolioPosition).where(PortfolioPosition.status == "open")).all():
        price = latest_price(db, pos.symbol) or pos.current_price or pos.buy_price
        pos.current_price = price
        sell_reason = None
        decision = latest_final_decision(db, pos.symbol)
        if pos.stop_loss and price <= pos.stop_loss:
            sell_reason = "Stop loss hit."
        elif pos.take_profit_1 and price >= pos.take_profit_1:
            sell_reason = "Take profit reached."
        elif decision and (decision.final_signal in {"AVOID / SELL", "SELL"} or (decision.final_score or 0) < settings.minimum_score_to_hold):
            sell_reason = "Final score/signal no longer supports holding."
        if sell_reason:
            actions.append(execute_paper_sell(db, pos, reason=sell_reason, price=price, force=force) if execute else {"status": "sell_candidate", "symbol": pos.symbol, "reason": sell_reason})

    for symbol in universe:
        if _open_position(db, symbol):
            continue
        decision = latest_final_decision(db, symbol)
        if not decision:
            built = build_final_decision(db, symbol, run_sources=True, persist=True)
            if execute:
                actions.append(execute_paper_buy(db, built, force=force))
            elif built["final_signal"] in {"STRONG BUY", "BUY"}:
                actions.append({"status": "buy_candidate", "symbol": symbol, "final_score": built["final_score"], "reason": built["reason"]})
            continue
        if decision.final_signal in {"STRONG BUY", "BUY"} and (decision.final_score or 0) >= settings.minimum_final_score_to_buy:
            actions.append(execute_paper_buy(db, decision, force=force) if execute else {"status": "buy_candidate", "symbol": symbol, "final_score": decision.final_score, "reason": decision.reason})
    db.flush()
    return {"actions": actions, "portfolio": portfolio_value(db, settings)}


def _fmt_money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "-"


def notify_portfolio_action(action: dict[str, Any]) -> None:
    if action.get("status") not in {"bought", "sold"}:
        return
    try:
        from app.services.telegram_bot import send_private_message_sync
    except Exception:
        return
    if action["status"] == "bought":
        text = (
            "EGX Portfolio Bot - BUY Executed\n\n"
            f"Symbol: {action['symbol']}\n"
            f"Buy Price: {_fmt_money(action.get('price'))}\n"
            f"Quantity: {action['quantity']}\n"
            f"Total Cost: {_fmt_money(action.get('total_cost'))} EGP\n\n"
            f"Final Score: {_fmt_money(action.get('final_score'))}%\n"
            f"Signal: {action.get('final_signal') or '-'}\n"
            f"Technical: {_fmt_money(action.get('technical_score'))}%\n"
            f"Financial: {_fmt_money(action.get('financial_score'))}%\n"
            f"News: {_fmt_money(action.get('news_score'))}%\n"
            f"Telegram: {_fmt_money(action.get('telegram_score'))}%\n"
            f"Strategy: {_fmt_money(action.get('strategy_score'))}%\n\n"
            f"Stop Loss: {_fmt_money(action.get('stop_loss'))}\n"
            f"TP1: {_fmt_money(action.get('take_profit_1'))}\n"
            f"TP2: {_fmt_money(action.get('take_profit_2'))}\n\n"
            f"Entry Reason:\n{action.get('reason') or '-'}\n\n"
            f"Portfolio Cash After Trade: {_fmt_money(action.get('cash_after'))} EGP\n"
            f"Time: {cairo_now().strftime('%Y-%m-%d %H:%M Cairo Time')}"
        )
    else:
        text = (
            "EGX Portfolio Bot - SELL Executed\n\n"
            f"Symbol: {action['symbol']}\n"
            f"Sell Price: {_fmt_money(action.get('price'))}\n"
            f"Quantity: {action['quantity']}\n"
            f"Total Value: {_fmt_money(action.get('total_value'))} EGP\n\n"
            f"Buy Price: {_fmt_money(action.get('buy_price'))}\n"
            f"Profit/Loss: {_fmt_money(action.get('profit_loss'))} EGP\n"
            f"Profit/Loss %: {_fmt_money(action.get('profit_loss_pct'))}%\n\n"
            f"Sell Reason:\n{action.get('reason') or '-'}\n\n"
            f"Portfolio Cash After Trade: {_fmt_money(action.get('cash_after'))} EGP\n"
            f"Time: {cairo_now().strftime('%Y-%m-%d %H:%M Cairo Time')}"
        )
    send_private_message_sync(text)


def run_daily_portfolio_bot(db: Session, *, execute: bool = False, force: bool = False, limit: int = 50) -> dict[str, Any]:
    result = scan_portfolio(db, execute=execute, force=force, limit=limit)
    return result
