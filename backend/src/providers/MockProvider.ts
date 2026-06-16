import { config } from "../config.js";
import type { MarketDataProvider } from "./MarketDataProvider.js";
import { unavailable } from "./MarketDataProvider.js";
import type { Candle, ProviderResult, Quote, ScannerRow, Timeframe } from "../types.js";

export class MockProvider implements MarketDataProvider {
  readonly name = "mock";

  async getQuote(_symbol: string): Promise<ProviderResult<Quote>> {
    return unavailable(this.name, config.ENABLE_MOCK_PROVIDER ? "Mock provider is intentionally not used for real prices." : "Mock provider disabled.");
  }

  async getCandles(_symbol: string, _timeframe: Timeframe): Promise<ProviderResult<Candle[]>> {
    return unavailable(this.name, config.ENABLE_MOCK_PROVIDER ? "Mock provider is intentionally not used for real candles." : "Mock provider disabled.");
  }

  async getScanner(): Promise<ProviderResult<ScannerRow[]>> {
    return unavailable(this.name, "Mock scanner disabled for production correctness.");
  }

  async getTopGainers(): Promise<ProviderResult<ScannerRow[]>> {
    return unavailable(this.name, "Mock top gainers disabled for production correctness.");
  }

  async getTopLosers(): Promise<ProviderResult<ScannerRow[]>> {
    return unavailable(this.name, "Mock top losers disabled for production correctness.");
  }
}
