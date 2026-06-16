import type { MarketDataProvider } from "./MarketDataProvider.js";
import { unavailable } from "./MarketDataProvider.js";
import type { Candle, ProviderResult, Quote, ScannerRow, Timeframe } from "../types.js";

export class TradingViewWidgetProvider implements MarketDataProvider {
  readonly name = "tradingview-widget";

  async getQuote(_symbol: string): Promise<ProviderResult<Quote>> {
    return unavailable(this.name, "TradingView widgets are embeddable display components, not an authorized backend market-data API.");
  }

  async getCandles(_symbol: string, _timeframe: Timeframe): Promise<ProviderResult<Candle[]>> {
    return unavailable(this.name, "TradingView chart widgets do not provide a licensed backend OHLCV feed.");
  }

  async getScanner(): Promise<ProviderResult<ScannerRow[]>> {
    return unavailable(this.name, "TradingView scanner data is not pulled. Unofficial screener wrappers use TradingView scanner endpoints and real-time access can require session cookies; this app only supports TradingView alerts/webhooks or embeddable display widgets.");
  }

  async getTopGainers(): Promise<ProviderResult<ScannerRow[]>> {
    return unavailable(this.name, "TradingView top movers are unavailable without an authorized provider feed.");
  }

  async getTopLosers(): Promise<ProviderResult<ScannerRow[]>> {
    return unavailable(this.name, "TradingView top movers are unavailable without an authorized provider feed.");
  }
}
