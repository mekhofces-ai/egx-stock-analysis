from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from app.data.market_data import get_ohlcv
from app.models import StrategySignal
from app.strategies.base_strategy import BaseStrategy, StrategyResultDTO
from app.strategies.breakout_strategy import BreakoutStrategy
from app.strategies.macd_crossover import MACDCrossoverStrategy
from app.strategies.pullback_ema import PullbackEMAStrategy
from app.strategies.rsi_reversal import RSIReversalStrategy
from app.strategies.trend_following import TrendFollowingStrategy
from app.strategies.volume_breakout import VolumeBreakoutStrategy


def default_strategies() -> list[BaseStrategy]:
    return [
        TrendFollowingStrategy(),
        RSIReversalStrategy(),
        MACDCrossoverStrategy(),
        BreakoutStrategy(),
        PullbackEMAStrategy(),
        VolumeBreakoutStrategy(),
    ]


def persist_strategy_signal(db: Session, result: StrategyResultDTO) -> None:
    db.add(
        StrategySignal(
            symbol=result.symbol,
            strategy_name=result.strategy_name,
            signal=result.signal,
            score=result.score,
            entry_price=result.entry_price,
            stop_loss=result.stop_loss,
            take_profit_1=result.take_profit_1,
            take_profit_2=result.take_profit_2,
            reason=result.reason,
        )
    )


def run_strategies_for_symbol(
    db: Session,
    symbol: str,
    *,
    strategies: Iterable[BaseStrategy] | None = None,
    persist: bool = True,
) -> list[StrategyResultDTO]:
    df = get_ohlcv(db, symbol, timeframe="1D", limit=300)
    results = [strategy.analyze(symbol, df) for strategy in (strategies or default_strategies())]
    if persist:
        for result in results:
            persist_strategy_signal(db, result)
    return results


def aggregate_strategy_score(results: list[StrategyResultDTO]) -> dict[str, object]:
    if not results:
        return {"strategy_score": None, "best_strategy": None, "signal": "HOLD"}
    buy_results = [row for row in results if row.signal == "BUY"]
    sell_results = [row for row in results if row.signal == "SELL"]
    best = max(results, key=lambda row: row.score)
    score = sum(row.score for row in results) / len(results)
    signal = "BUY" if buy_results and len(buy_results) >= max(1, len(results) // 3) else "SELL" if sell_results and len(sell_results) >= 2 else "HOLD"
    return {
        "strategy_score": round(score, 2),
        "best_strategy": best.strategy_name,
        "signal": signal,
        "results": [row.__dict__ for row in results],
    }

