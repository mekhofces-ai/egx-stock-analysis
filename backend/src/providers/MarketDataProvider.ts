import type { Candle, ProviderResult, Quote, ScannerRow, Timeframe } from "../types.js";

export interface MarketDataProvider {
  readonly name: string;
  getQuote(symbol: string): Promise<ProviderResult<Quote>>;
  getCandles(symbol: string, timeframe: Timeframe): Promise<ProviderResult<Candle[]>>;
  getScanner(): Promise<ProviderResult<ScannerRow[]>>;
  refreshScanner?(): Promise<ProviderResult<ScannerRow[]>>;
  getTopGainers(): Promise<ProviderResult<ScannerRow[]>>;
  getTopLosers(): Promise<ProviderResult<ScannerRow[]>>;
}

export const unavailable = <T>(source: string, reason = "No licensed real-time data provider configured"): ProviderResult<T> => ({
  status: "unavailable",
  reason,
  source,
});
