import { config } from "../config.js";
import { prisma } from "../db.js";
import type { MarketDataProvider } from "../providers/MarketDataProvider.js";
import type { Candle, ProviderResult, Quote, ScannerRow, Timeframe } from "../types.js";
import { TtlCache } from "../utils/cache.js";
import { buildBidAskExpectation } from "./bidAskExpectation.js";
import { buildTimeframeAnalysis, calculateIndicators, recommendation } from "./technicalAnalysis.js";

function egyptDateKey(value: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Africa/Cairo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function storedHistorySources() {
  return config.MARKET_DATA_PROVIDER === "egx-ai-api"
    ? [config.MARKET_DATA_PROVIDER, "public-yahoo-chart"]
    : [config.MARKET_DATA_PROVIDER];
}

export class MarketDataService {
  private cache = new TtlCache<unknown>(config.CACHE_TTL_MS);
  private scannerPromise: Promise<ProviderResult<ScannerRow[]>> | null = null;
  private lastScannerResult: ProviderResult<ScannerRow[]> | null = null;

  constructor(private provider: MarketDataProvider) {}

  clearCache() {
    this.cache = new TtlCache<unknown>(config.CACHE_TTL_MS);
  }

  private async recordProviderStatus<T>(result: ProviderResult<T>) {
    await prisma.providerStatus.upsert({
      where: { provider: this.provider.name },
      update: { status: result.status, reason: result.reason, checkedAt: new Date() },
      create: { provider: this.provider.name, status: result.status, reason: result.reason },
    });
  }

  private async cached<T>(key: string, fn: () => Promise<ProviderResult<T>>): Promise<ProviderResult<T>> {
    const hit = this.cache.get(key) as ProviderResult<T> | undefined;
    if (hit) return hit;
    const result = await fn();
    this.cache.set(key, result);
    await this.recordProviderStatus(result);
    return result;
  }

  private async storedScannerSnapshot(reason: string): Promise<ProviderResult<ScannerRow[]> | null> {
    const [symbols, candles, quotes] = await Promise.all([
      prisma.egxSymbol.findMany({ where: { isActive: true }, orderBy: { symbolCode: "asc" } }),
      prisma.candle.findMany({
        where: { timeframe: "1D", source: { in: storedHistorySources() } },
        orderBy: [{ symbolCode: "asc" }, { candleTime: "asc" }],
      }),
      prisma.quoteSnapshot.findMany({
        where: { source: config.MARKET_DATA_PROVIDER },
        orderBy: [{ symbolCode: "asc" }, { capturedAt: "desc" }],
      }),
    ]);

    if (!symbols.length || (!candles.length && !quotes.length)) return null;

    const candlesBySymbolAndDay = new Map<string, typeof candles[number]>();
    for (const row of candles) {
      const key = `${row.symbolCode}:${egyptDateKey(row.candleTime)}`;
      const current = candlesBySymbolAndDay.get(key);
      const rowPriority = (row.source === config.MARKET_DATA_PROVIDER ? 100 : 0) + (row.quality === "real" ? 10 : 0) + row.importedAt.getTime() / 10_000_000_000_000;
      const currentPriority = current ? (current.source === config.MARKET_DATA_PROVIDER ? 100 : 0) + (current.quality === "real" ? 10 : 0) + current.importedAt.getTime() / 10_000_000_000_000 : -1;
      if (!current || rowPriority >= currentPriority) candlesBySymbolAndDay.set(key, row);
    }

    const candlesBySymbol = new Map<string, Candle[]>();
    for (const row of candlesBySymbolAndDay.values()) {
      const candle: Candle = {
        symbol: row.symbolCode,
        timeframe: "1D",
        time: row.candleTime.toISOString(),
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume,
        source: row.source,
      };
      candlesBySymbol.set(row.symbolCode, [...(candlesBySymbol.get(row.symbolCode) ?? []), candle].sort((a, b) => a.time.localeCompare(b.time)));
    }

    const latestQuoteBySymbol = new Map<string, typeof quotes[number]>();
    for (const quote of quotes) {
      if (!latestQuoteBySymbol.has(quote.symbolCode)) latestQuoteBySymbol.set(quote.symbolCode, quote);
    }

    const rows = symbols.map((symbol): ScannerRow => {
      const candleData = candlesBySymbol.get(symbol.symbolCode) ?? [];
      const latestCandle = candleData.at(-1);
      const quote = latestQuoteBySymbol.get(symbol.symbolCode);
      const analysis = candleData.length >= 20 ? buildTimeframeAnalysis(symbol.symbolCode, "1D", candleData, true) : null;
      const rec = candleData.length ? recommendation(candleData) : null;
      const price = quote?.price ?? latestCandle?.close;
      const orderBookStatus = quote?.orderBookStatus === "real" ? "real" : quote?.orderBookStatus === "estimated" ? "estimated" : "unavailable";

      if (!price) {
        return {
          symbol: symbol.symbolCode,
          companyName: symbol.companyNameEn,
          sector: symbol.sector,
          dataQuality: "unavailable",
          reason: "No stored provider quote or candle is available yet.",
        };
      }

      return {
        symbol: symbol.symbolCode,
        companyName: symbol.companyNameEn,
        sector: symbol.sector,
        price,
        changePercent: quote?.changePercent ?? undefined,
        volume: quote?.volume ?? latestCandle?.volume,
        capturedAt: quote?.capturedAt.toISOString(),
        marketCap: quote?.marketCap ?? undefined,
        bid: quote?.bid ?? undefined,
        ask: quote?.ask ?? undefined,
        spreadPercent: undefined,
        orderBookStatus,
        orderBookNote: quote?.orderBookNote ?? "Stored snapshot does not include real bid/ask depth.",
        bidAskExpectation: buildBidAskExpectation({ price, bid: quote?.bid ?? undefined, ask: quote?.ask ?? undefined, orderBookStatus }, analysis),
        recommendation: rec?.recommendation,
        confidence: rec?.confidence,
        dataQuality: candleData.length >= 20 ? "partial" : "partial",
        reason: rec ? `${rec.reason} ${reason}` : reason,
        analysis: analysis ?? undefined,
      };
    });

    return {
      status: "degraded",
      source: config.MARKET_DATA_PROVIDER,
      reason,
      data: rows,
    };
  }

  getQuote(symbol: string): Promise<ProviderResult<Quote>> {
    return this.cached(`quote:${symbol}`, () => this.provider.getQuote(symbol));
  }

  getCandles(symbol: string, timeframe: Timeframe): Promise<ProviderResult<Candle[]>> {
    return this.cached(`candles:${symbol}:${timeframe}`, () => this.provider.getCandles(symbol, timeframe));
  }

  private async loadScanner(refresh = false): Promise<ProviderResult<ScannerRow[]>> {
    if (!refresh) {
      const hit = this.cache.get("scanner") as ProviderResult<ScannerRow[]> | undefined;
      if (hit) return hit;
      if (this.scannerPromise && this.lastScannerResult) return this.lastScannerResult;
      if (this.scannerPromise) {
        const stored = await this.storedScannerSnapshot("Serving the last stored provider snapshot while a fresh auto-refresh is running.");
        if (stored) {
          this.cache.set("scanner", stored);
          this.lastScannerResult = stored;
          return stored;
        }
      }
    }

    if (this.scannerPromise) return this.scannerPromise;

    this.scannerPromise = (async () => {
      const result = refresh && this.provider.refreshScanner
        ? await this.provider.refreshScanner()
        : await this.provider.getScanner();
      if (refresh) this.cache = new TtlCache<unknown>(config.CACHE_TTL_MS);
      this.cache.set("scanner", result);
      this.lastScannerResult = result;
      await this.recordProviderStatus(result);
      return result;
    })().finally(() => {
      this.scannerPromise = null;
    });

    return this.scannerPromise;
  }

  getScanner(): Promise<ProviderResult<ScannerRow[]>> {
    return this.loadScanner(false);
  }

  async refreshScanner(): Promise<ProviderResult<ScannerRow[]>> {
    return this.loadScanner(true);
  }

  getTopGainers() {
    return this.cached("top-gainers", async () => {
      const scanner = await this.getScanner();
      if (!scanner.data) return scanner;
      return { ...scanner, data: scanner.data.filter((row) => row.changePercent !== undefined).sort((a, b) => (b.changePercent ?? 0) - (a.changePercent ?? 0)).slice(0, 20) };
    });
  }

  getTopLosers() {
    return this.cached("top-losers", async () => {
      const scanner = await this.getScanner();
      if (!scanner.data) return scanner;
      return { ...scanner, data: scanner.data.filter((row) => row.changePercent !== undefined).sort((a, b) => (a.changePercent ?? 0) - (b.changePercent ?? 0)).slice(0, 20) };
    });
  }

  async getMostActive(): Promise<ProviderResult<ScannerRow[]>> {
    const scanner = await this.getScanner();
    if (!scanner.data) return scanner;
    return { ...scanner, data: scanner.data.filter((row) => row.volume !== undefined).sort((a, b) => (b.volume ?? 0) - (a.volume ?? 0)).slice(0, 20) };
  }

  async getAnalysis(symbol: string, timeframe: Timeframe) {
    const candles = await this.getCandles(symbol, timeframe);
    if (!candles.data?.length) return { status: candles.status, reason: candles.reason, source: candles.source };
    const indicators = calculateIndicators(candles.data);
    const rec = recommendation(candles.data);
    const frame = timeframe === "15M" || timeframe === "30M" || timeframe === "1H" || timeframe === "4H" || timeframe === "1D" ? timeframe : "1D";
    return { status: "available", source: candles.source, indicators, analysis: buildTimeframeAnalysis(symbol, frame, candles.data, candles.status === "degraded"), ...rec };
  }
}
