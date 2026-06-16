from app.config import get_settings
from app.services.market_data.base import ProviderChain
from app.services.market_data.mock_provider import MockProvider
from app.services.strategy import run_strategy_backtest


def test_strategy_runs_requested_timeframes_with_mock_provider() -> None:
    settings = get_settings().model_copy(update={"strategy_allow_mock_data": True, "market_data_allow_mock": True})
    chain = ProviderChain([MockProvider(settings)])

    results = [
        run_strategy_backtest("COMI", timeframe, provider_chain=chain, settings=settings)
        for timeframe in ["15m", "1h", "4h", "1D"]
    ]

    assert {result.timeframe for result in results} == {"15m", "1h", "4h", "1D"}
    assert all(result.action in {"BUY", "WATCH", "NEUTRAL", "AVOID"} for result in results)
    assert all(result.provider == "mock" for result in results)
    assert all(result.trades >= 0 for result in results)
