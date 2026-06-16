from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class TradingAdapter(Protocol):
    def get_account_balance(self) -> dict[str, Any]: ...
    def get_positions(self) -> list[dict[str, Any]]: ...
    def get_open_orders(self) -> list[dict[str, Any]]: ...
    def place_buy_order(self, symbol: str, quantity: int, price: float | None = None, **kwargs: Any) -> dict[str, Any]: ...
    def place_sell_order(self, symbol: str, quantity: int, price: float | None = None, **kwargs: Any) -> dict[str, Any]: ...
    def cancel_order(self, order_id: str) -> dict[str, Any]: ...
    def get_order_status(self, order_id: str) -> dict[str, Any]: ...


@dataclass
class PaperTradingAdapter:
    cash: float = 0.0

    def get_account_balance(self) -> dict[str, Any]:
        return {"mode": "paper", "cash": self.cash}

    def get_positions(self) -> list[dict[str, Any]]:
        return []

    def get_open_orders(self) -> list[dict[str, Any]]:
        return []

    def place_buy_order(self, symbol: str, quantity: int, price: float | None = None, **kwargs: Any) -> dict[str, Any]:
        return {"status": "simulated", "mode": "paper", "side": "BUY", "symbol": symbol, "quantity": quantity, "price": price, "kwargs": kwargs}

    def place_sell_order(self, symbol: str, quantity: int, price: float | None = None, **kwargs: Any) -> dict[str, Any]:
        return {"status": "simulated", "mode": "paper", "side": "SELL", "symbol": symbol, "quantity": quantity, "price": price, "kwargs": kwargs}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return {"status": "simulated_cancelled", "mode": "paper", "order_id": order_id}

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        return {"status": "simulated", "mode": "paper", "order_id": order_id}


class LiveTradingAdapter:
    """Placeholder for a real broker adapter.

    Real broker credentials must stay in environment variables. Until a broker
    API is explicitly connected, every live order returns a blocked response.
    """

    def get_account_balance(self) -> dict[str, Any]:
        return {"status": "not_configured", "mode": "live", "message": "No live broker adapter is configured."}

    def get_positions(self) -> list[dict[str, Any]]:
        return []

    def get_open_orders(self) -> list[dict[str, Any]]:
        return []

    def place_buy_order(self, symbol: str, quantity: int, price: float | None = None, **kwargs: Any) -> dict[str, Any]:
        return {"status": "blocked_not_configured", "mode": "live", "side": "BUY", "symbol": symbol, "quantity": quantity, "price": price}

    def place_sell_order(self, symbol: str, quantity: int, price: float | None = None, **kwargs: Any) -> dict[str, Any]:
        return {"status": "blocked_not_configured", "mode": "live", "side": "SELL", "symbol": symbol, "quantity": quantity, "price": price}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return {"status": "blocked_not_configured", "mode": "live", "order_id": order_id}

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        return {"status": "not_configured", "mode": "live", "order_id": order_id}
