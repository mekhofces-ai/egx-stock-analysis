import pandas as pd

from app.services.market_depth import normalize_market_depth


def test_market_depth_normalizes_bid_ask_aliases() -> None:
    frame = pd.DataFrame(
        [
            {"ticker": "EGX:COMI", "bid_ask": "buy", "price": 10.0, "qty": 100, "orders": 2},
            {"ticker": "EGX:COMI", "bid_ask": "sell", "price": 10.2, "qty": 80, "orders": 1},
        ]
    )

    normalized = normalize_market_depth(frame)

    assert normalized["symbol"].tolist() == ["COMI", "COMI"]
    assert normalized["side"].tolist() == ["bid", "ask"]
    assert normalized["notional"].tolist() == [1000.0, 816.0]
