import { config } from "../config.js";
import type { Candle, ProviderResult, Quote, ScannerRow, Timeframe } from "../types.js";
import type { MarketDataProvider } from "./MarketDataProvider.js";
import { unavailable } from "./MarketDataProvider.js";
import { withRetry } from "../utils/retry.js";

export class LicensedApiProvider implements MarketDataProvider {
  readonly name = "licensed-api";

  private async fetchJson<T>(path: string): Promise<ProviderResult<T>> {
    if (!config.LICENSED_API_BASE_URL || !config.LICENSED_API_KEY) return unavailable(this.name);
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), config.LICENSED_API_TIMEOUT_MS);
      const response = await withRetry(() => fetch(`${config.LICENSED_API_BASE_URL}${path}`, {
        headers: { Authorization: `Bearer ${config.LICENSED_API_KEY}` },
        signal: controller.signal,
      }));
      clearTimeout(timer);
      if (!response.ok) return unavailable(this.name, `Licensed API returned ${response.status}`);
      return { status: "available", data: await response.json() as T, source: this.name };
    } catch (error) {
      return unavailable(this.name, error instanceof Error ? error.message : "Licensed API request failed");
    }
  }

  getQuote(symbol: string): Promise<ProviderResult<Quote>> { return this.fetchJson(`/quote/${encodeURIComponent(symbol)}`); }
  getCandles(symbol: string, timeframe: Timeframe): Promise<ProviderResult<Candle[]>> { return this.fetchJson(`/candles/${encodeURIComponent(symbol)}?timeframe=${timeframe}`); }
  getScanner(): Promise<ProviderResult<ScannerRow[]>> { return this.fetchJson("/scanner"); }
  getTopGainers(): Promise<ProviderResult<ScannerRow[]>> { return this.fetchJson("/top-gainers"); }
  getTopLosers(): Promise<ProviderResult<ScannerRow[]>> { return this.fetchJson("/top-losers"); }
}
