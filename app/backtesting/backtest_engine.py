from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.backtesting.performance_metrics import max_drawdown, profit_factor, total_return, win_rate
from app.data.market_data import get_ohlcv
from app.models import StrategyPerformance
from app.strategies.base_strategy import BaseStrategy
from app.strategies.strategy_runner import default_strategies


def backtest_strategy(
    db: Session,
    symbol: str,
    strategy: BaseStrategy,
    *,
    commission: float = 0.001,
    slippage: float = 0.001,
    persist: bool = True,
) -> dict[str, Any]:
    df = get_ohlcv(db, symbol, timeframe="1D", limit=400)
    if df.empty or len(df) < 80:
        return {"symbol": symbol, "strategy_name": strategy.name, "status": "insufficient_data", "reason": "No sufficient OHLCV candles."}
    in_trade = False
    entry = 0.0
    trades: list[float] = []
    equity = [1.0]
    for end in range(60, len(df) - 1):
        window = df.iloc[: end + 1]
        signal = strategy.analyze(symbol, window)
        next_open = float(df.iloc[end + 1]["open"])
        if not in_trade and signal.signal == "BUY":
            entry = next_open * (1 + slippage)
            in_trade = True
        elif in_trade and signal.signal == "SELL":
            exit_price = next_open * (1 - slippage)
            ret = (exit_price - entry) / entry - commission * 2
            trades.append(ret)
            equity.append(equity[-1] * (1 + ret))
            in_trade = False
    if in_trade:
        exit_price = float(df.iloc[-1]["close"]) * (1 - slippage)
        trades.append((exit_price - entry) / entry - commission * 2)
        equity.append(equity[-1] * (1 + trades[-1]))
    summary = {
        "symbol": symbol,
        "strategy_name": strategy.name,
        "status": "ok",
        "total_trades": len(trades),
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "max_drawdown": max_drawdown(equity),
        "average_return": round(sum(trades) / len(trades) * 100, 4) if trades else 0.0,
        "total_return": total_return(trades),
    }
    if persist:
        row = StrategyPerformance(
            strategy_name=strategy.name,
            symbol=symbol,
            total_trades=summary["total_trades"],
            win_rate=summary["win_rate"],
            profit_factor=summary["profit_factor"],
            max_drawdown=summary["max_drawdown"],
            average_return=summary["average_return"],
        )
        db.add(row)
    return summary


def backtest_all_strategies(db: Session, symbol: str, *, persist: bool = True) -> list[dict[str, Any]]:
    return [backtest_strategy(db, symbol, strategy, persist=persist) for strategy in default_strategies()]

