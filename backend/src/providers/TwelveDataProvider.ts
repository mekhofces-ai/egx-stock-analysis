import { config } from "../config.js";
import { prisma } from "../db.js";
import { buildBidAskExpectation, spreadPercent } from "../services/bidAskExpectation.js";
import { buildTimeframeAnalysis, recommendation } from "../services/technicalAnalysis.js";
import type { Candle, ProviderResult, Quote, ScannerRow, Timeframe } from "../types.js";
import { withRetry } from "../utils/retry.js";
import type { MarketDataProvider } from "./MarketDataProvider.js";
import { unavailable } from "./MarketDataProvider.js";

type TwelveQuote = Record<string, string | number | undefined> & {
  status?: string;
  message?: string;
  symbol?: string;
  name?: string;
  close?: string;
  previous_close?: string;
  percent_change?: string;
  volume?: string;
  bid?: string;
  ask?: string;
  datetime?: string;
};

type TwelveTimeSeries = {
  status?: string;
  message?: string;
  meta?: { symbol?: string; interval?: string; exchange?: string };
  values?: Array<{ datetime: string; open: string; high: string; low: string; close: string; volume?: string }>;
};

type TwelveSymbolSearch = {
  status?: string;
  message?: string;
  data?: Array<{
    symbol?: string;
    instrument_name?: string;
    exchange?: string;
    mic_code?: string;
    country?: string;
    currency?: string;
    instrument_type?: string;
  }>;
};

const SOURCE_NOTE = "Twelve Data free/API-key provider. Bid/ask is used only when the endpoint returns real bid and ask fields.";

function normalizeInterval(timeframe: Timeframe) {
  if (timeframe === "1D") return "1day";
  if (timeframe === "1h" || timeframe === "1H") return "1h";
  if (timeframe === "15m" || timeframe === "15M") return "15min";
  if (timeframe === "30M") return "30min";
  if (timeframe === "5m") return "5min";
  return "1min";
}

function numberOrUndefined(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function toDate(value: string) {
  return new Date(value.includes("T") ? value : `${value}T00:00:00+02:00`).toISOString();
}

function round(value: number) {
  return Number(value.toFixed(2));
}

function parseConfiguredSymbolMap() {
  try {
    const parsed = JSON.parse(config.TWELVE_DATA_SYMBOL_MAP_JSON);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return new Map<string, string>();
    return new Map(
      Object.entries(parsed)
        .filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].trim().length > 0)
        .map(([key, value]) => [key.toUpperCase(), value.toUpperCase()])
    );
  } catch {
    return new Map<string, string>();
  }
}

export class TwelveDataProvider implements MarketDataProvider {
  readonly name = "twelve-data";
  private readonly configuredSymbolMap = parseConfiguredSymbolMap();
  private readonly resolvedSymbolCache = new Map<string, string>();

  private async request<T>(path: string, params: Record<string, string | number | undefined>): Promise<ProviderResult<T>> {
    if (!config.TWELVE_DATA_API_KEY) return unavailable(this.name, "TWELVE_DATA_API_KEY is not configured. Create a free Twelve Data API key and add it to .env.");
    const url = new URL(`https://api.twelvedata.com/${path}`);
    for (const [key, value] of Object.entries({ ...params, apikey: config.TWELVE_DATA_API_KEY })) {
      if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
    }
    try {
      const response = await withRetry(() => fetch(url, { headers: { "User-Agent": "EGX Smart Screener Twelve Data adapter" } }));
      if (!response.ok) return unavailable(this.name, `Twelve Data returned HTTP ${response.status}`);
      const payload = await response.json() as T & { status?: string; message?: string };
      if (payload.status === "error") return unavailable(this.name, payload.message ?? "Twelve Data returned an error");
      return { status: "available", source: this.name, reason: SOURCE_NOTE, data: payload as T };
    } catch (error) {
      return unavailable(this.name, error instanceof Error ? error.message : "Twelve Data request failed");
    }
  }

  private async searchProviderSymbol(query: string): Promise<string | null> {
    const url = new URL("https://api.twelvedata.com/symbol_search");
    url.searchParams.set("symbol", query);
    url.searchParams.set("exchange", config.TWELVE_DATA_EXCHANGE);
    if (config.TWELVE_DATA_API_KEY) url.searchParams.set("apikey", config.TWELVE_DATA_API_KEY);

    try {
      const response = await withRetry(() => fetch(url, { headers: { "User-Agent": "EGX Smart Screener Twelve Data symbol resolver" } }));
      if (!response.ok) return null;
      const payload = await response.json() as TwelveSymbolSearch;
      const candidates = payload.data ?? [];
      const match = candidates.find((item) =>
        item.symbol &&
        item.country?.toLowerCase() === "egypt" &&
        (item.exchange?.toUpperCase() === config.TWELVE_DATA_EXCHANGE.toUpperCase() || item.mic_code?.toUpperCase() === "XCAI")
      );
      return match?.symbol?.toUpperCase() ?? null;
    } catch {
      return null;
    }
  }

  private async resolveProviderSymbol(symbol: string): Promise<string> {
    const internalSymbol = symbol.toUpperCase();
    const configured = this.configuredSymbolMap.get(internalSymbol);
    if (configured) return configured;
    if (internalSymbol.startsWith("EGS")) return internalSymbol;

    const cached = this.resolvedSymbolCache.get(internalSymbol);
    if (cached) return cached;

    const stock = await prisma.egxSymbol.findUnique({ where: { symbolCode: internalSymbol } });
    const queries = [stock?.companyNameEn, internalSymbol].filter((query): query is string => Boolean(query));
    for (const query of queries) {
      const resolved = await this.searchProviderSymbol(query);
      if (resolved) {
        this.resolvedSymbolCache.set(internalSymbol, resolved);
        return resolved;
      }
    }

    return internalSymbol;
  }

  async getCandles(symbol: string, timeframe: Timeframe): Promise<ProviderResult<Candle[]>> {
    const providerSymbol = await this.resolveProviderSymbol(symbol);
    const response = await this.request<TwelveTimeSeries>("time_series", {
      symbol: providerSymbol,
      exchange: config.TWELVE_DATA_EXCHANGE,
      interval: normalizeInterval(timeframe),
      outputsize: 260,
      order: "ASC",
    });
    if (!response.data?.values?.length) return unavailable(this.name, response.reason ?? `No Twelve Data candles returned for ${symbol}`);
    const candles = response.data.values
      .map((row): Candle | null => {
        const open = numberOrUndefined(row.open);
        const high = numberOrUndefined(row.high);
        const low = numberOrUndefined(row.low);
        const close = numberOrUndefined(row.close);
        const volume = numberOrUndefined(row.volume) ?? 0;
        if ([open, high, low, close].some((value) => value === undefined)) return null;
        return {
          symbol: symbol.toUpperCase(),
          timeframe,
          time: toDate(row.datetime),
          open: round(open!),
          high: round(high!),
          low: round(low!),
          close: round(close!),
          volume,
          source: this.name,
        };
      })
      .filter((row): row is Candle => Boolean(row));
    return candles.length ? { status: "available", source: this.name, reason: SOURCE_NOTE, data: candles } : unavailable(this.name, `No usable Twelve Data candles returned for ${symbol}`);
  }

  async getQuote(symbol: string): Promise<ProviderResult<Quote>> {
    const providerSymbol = await this.resolveProviderSymbol(symbol);
    const response = await this.request<TwelveQuote>("quote", { symbol: providerSymbol, exchange: config.TWELVE_DATA_EXCHANGE });
    if (!response.data) return unavailable(this.name, response.reason ?? `No Twelve Data quote returned for ${symbol}`);
    const stock = await prisma.egxSymbol.findUnique({ where: { symbolCode: symbol.toUpperCase() } });
    const price = numberOrUndefined(response.data.close);
    if (!price) return unavailable(this.name, `Twelve Data quote has no close price for ${symbol}`);
    const previousClose = numberOrUndefined(response.data.previous_close);
    const bid = numberOrUndefined(response.data.bid);
    const ask = numberOrUndefined(response.data.ask);
    const orderBookStatus = bid && ask ? "real" as const : "unavailable" as const;
    const candles = await this.getCandles(symbol, "1D");
    const analysis = candles.data?.length ? buildTimeframeAnalysis(symbol.toUpperCase(), "1D", candles.data, false) : null;
    const quote: Quote = {
      symbol: symbol.toUpperCase(),
      price: round(price),
      previousClose,
      changePercent: numberOrUndefined(response.data.percent_change),
      volume: numberOrUndefined(response.data.volume),
      sector: stock?.sector,
      industry: stock?.industry,
      bid,
      ask,
      spreadPercent: spreadPercent(bid, ask),
      orderBookStatus,
      orderBookNote: orderBookStatus === "real" ? "Real top-of-book bid/ask returned by Twelve Data quote endpoint." : "Twelve Data quote did not include real bid/ask for this EGX symbol.",
      bidAskExpectation: buildBidAskExpectation({ price, bid, ask, orderBookStatus }, analysis),
      capturedAt: response.data.datetime ? toDate(String(response.data.datetime)) : new Date().toISOString(),
    };
    return { status: orderBookStatus === "real" ? "available" : "degraded", source: this.name, reason: quote.orderBookNote, data: quote };
  }

  async getScanner(): Promise<ProviderResult<ScannerRow[]>> {
    const symbols = await prisma.egxSymbol.findMany({ where: { isActive: true }, orderBy: { symbolCode: "asc" } });
    const rows: ScannerRow[] = [];
    for (const stock of symbols.slice(0, 80)) {
      const candles = await this.getCandles(stock.symbolCode, "1D");
      if (!candles.data?.length) {
        rows.push({ symbol: stock.symbolCode, companyName: stock.companyNameEn, sector: stock.sector, dataQuality: "unavailable", reason: candles.reason });
        continue;
      }
      const latest = candles.data[candles.data.length - 1];
      const previous = candles.data[candles.data.length - 2];
      const rec = recommendation(candles.data);
      const analysis = buildTimeframeAnalysis(stock.symbolCode, "1D", candles.data, false);
      const quote = await this.getQuote(stock.symbolCode);
      rows.push({
        symbol: stock.symbolCode,
        companyName: stock.companyNameEn,
        sector: stock.sector,
        price: latest.close,
        changePercent: previous ? round(((latest.close - previous.close) / previous.close) * 100) : undefined,
        volume: latest.volume,
        bid: quote.data?.bid,
        ask: quote.data?.ask,
        spreadPercent: quote.data?.spreadPercent,
        orderBookStatus: quote.data?.orderBookStatus ?? "unavailable",
        orderBookNote: quote.data?.orderBookNote,
        bidAskExpectation: quote.data?.bidAskExpectation ?? buildBidAskExpectation({ price: latest.close, orderBookStatus: "unavailable" }, analysis),
        recommendation: rec.recommendation,
        confidence: rec.confidence,
        dataQuality: quote.data?.orderBookStatus === "real" ? "real" : "partial",
        reason: `${rec.reason} (${SOURCE_NOTE})`,
        analysis: analysis ?? undefined,
      });
    }
    return { status: "degraded", source: this.name, reason: "Free API credits are limited; scanner fetch is capped. Use a paid/licensed provider for full-universe real-time scanning.", data: rows };
  }

  async getTopGainers(): Promise<ProviderResult<ScannerRow[]>> {
    const scanner = await this.getScanner();
    if (!scanner.data) return scanner;
    return { ...scanner, data: scanner.data.filter((row) => row.changePercent !== undefined).sort((a, b) => (b.changePercent ?? 0) - (a.changePercent ?? 0)).slice(0, 20) };
  }

  async getTopLosers(): Promise<ProviderResult<ScannerRow[]>> {
    const scanner = await this.getScanner();
    if (!scanner.data) return scanner;
    return { ...scanner, data: scanner.data.filter((row) => row.changePercent !== undefined).sort((a, b) => (a.changePercent ?? 0) - (b.changePercent ?? 0)).slice(0, 20) };
  }
}
