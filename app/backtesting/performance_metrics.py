from __future__ import annotations

from typing import Iterable


def total_return(returns: Iterable[float]) -> float:
    value = 1.0
    for ret in returns:
        value *= 1 + ret
    return round((value - 1) * 100, 4)


def max_drawdown(equity: list[float]) -> float:
    peak = None
    worst = 0.0
    for value in equity:
        peak = value if peak is None else max(peak, value)
        if peak:
            worst = min(worst, (value - peak) / peak)
    return round(abs(worst) * 100, 4)


def profit_factor(trade_returns: list[float]) -> float | None:
    wins = sum(ret for ret in trade_returns if ret > 0)
    losses = abs(sum(ret for ret in trade_returns if ret < 0))
    if losses == 0:
        return None if wins == 0 else 999.0
    return round(wins / losses, 4)


def win_rate(trade_returns: list[float]) -> float | None:
    if not trade_returns:
        return None
    return round(sum(1 for ret in trade_returns if ret > 0) / len(trade_returns) * 100, 2)

