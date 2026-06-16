from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class StrategyResultDTO:
    strategy_name: str
    symbol: str
    signal: str
    score: float
    entry_price: float | None
    stop_loss: float | None
    take_profit_1: float | None
    take_profit_2: float | None
    reason: str
    details: dict[str, Any]


class BaseStrategy(ABC):
    name = "Base Strategy"

    def _result(
        self,
        symbol: str,
        signal: str,
        score: float,
        entry: float | None,
        stop: float | None,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> StrategyResultDTO:
        tp1 = tp2 = None
        if entry is not None and stop is not None and entry > stop:
            risk = entry - stop
            tp1 = entry + risk * 1.5
            tp2 = entry + risk * 2.5
        return StrategyResultDTO(
            strategy_name=self.name,
            symbol=symbol,
            signal=signal,
            score=round(max(0, min(100, score)), 2),
            entry_price=round(entry, 4) if entry is not None else None,
            stop_loss=round(stop, 4) if stop is not None else None,
            take_profit_1=round(tp1, 4) if tp1 is not None else None,
            take_profit_2=round(tp2, 4) if tp2 is not None else None,
            reason=reason,
            details=details or {},
        )

    @abstractmethod
    def analyze(self, symbol: str, df: pd.DataFrame) -> StrategyResultDTO:
        raise NotImplementedError

