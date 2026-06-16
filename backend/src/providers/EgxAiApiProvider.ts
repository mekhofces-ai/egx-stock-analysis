import { config } from "../config.js";
import { prisma } from "../db.js";
import { buildBidAskExpectation } from "../services/bidAskExpectation.js";
import { buildTimeframeAnalysis, recommendation } from "../services/technicalAnalysis.js";
import type { Candle, ProviderResult, Quote, ScannerRow, Timeframe } from "../types.js";
import { withRetry } from "../utils/retry.js";
import type { MarketDataProvider } from "./MarketDataProvider.js";
import { unavailable } from "./MarketDataProvider.js";

type EgxAiPage<T> = {
  content?: T[];
  totalElements?: number;
  totalPages?: number;
  number?: number;
  size?: number;
};

type EgxAiEquity = {
  reutersCode?: string;
  name?: string;
  ISN?: string;
  isn?: string;
  sector?: string;
  listingDate?: string;
};

type EgxAiStock = {
  id?: number;
  currPrice?: number;
  rateOfChange?: number;
  percentageOfChange?: number;
  open?: number;
  prevClose?: number;
  highest?: number;
  lowest?: number;
  volume?: number;
  value?: number;
  time?: string;
  equity?: EgxAiEquity;
};

type EgxAiOhclv = {
  time?: string;
  currPrice?: number;
  open?: number;
  highest?: number;
  prevClose?: number;
  lowest?: number;
  volume?: number;
};

const SOURCE_NOTE = "EGX-AI-compatible API adapter. Uses the configured stock-service REST API; no bid/ask or market depth fields are returned by this API.";
const HISTORICAL_BACKFILL_SOURCE = "public-yahoo-chart";
const BACKFILL_NOTE = "Daily technical history may include stored public-yahoo-chart candles as historical backfill; the current EGX-AI-compatible session snapshot is preferred for overlapping dates.";

function cleanBaseUrl(value: string) {
  return value.replace(/\/+$/, "");
}

function numberOrUndefined(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function round(value: number) {
  return Number(value.toFixed(2));
}

function codeFromStock(row: EgxAiStock) {
  return row.equity?.reutersCode?.trim().toUpperCase();
}

function toIsoDate(value: string | undefined) {
  if (!value) return new Date().toISOString();
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString();
}

function intervalFor(timeframe: Timeframe) {
  if (timeframe === "1m") return { periodParam: "1 minute", intervalParam: "1 day" };
  if (timeframe === "5m") return { periodParam: "5 minutes", intervalParam: "1 day" };
  if (timeframe === "15m" || timeframe === "15M") return { periodParam: "15 minutes", intervalParam: "1 weeks" };
  if (timeframe === "30M") return { periodParam: "30 minutes", intervalParam: "1 weeks" };
  if (timeframe === "1h" || timeframe === "1H") return { periodParam: "1 hours", intervalParam: "1 months" };
  if (timeframe === "4H") return { periodParam: "4 hours", intervalParam: "3 months" };
  return { periodParam: "1 day", intervalParam: "1 years" };
}

function egyptDateKey(value: string | Date) {
  const date = typeof value === "string" ? new Date(value) : value;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Africa/Cairo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function candlePriority(candle: Candle & { quality?: string; importedAt?: Date }) {
  return (candle.source === "egx-ai-api" ? 100 : 0) + (candle.quality === "real" ? 10 : 0) + (candle.importedAt?.getTime() ?? 0) / 10_000_000_000_000;
}

function normalizeOhlcv(open: number, high: number, low: number, close: number, volume: number) {
  if (![open, high, low, close, volume].every(Number.isFinite)) return null;
  if (open <= 0 || high <= 0 || low <= 0 || close <= 0 || volume < 0) return null;
  if (high < low) return null;
  const tolerance = Math.max(close * 0.0025, 0.0001);
  if (close > high + tolerance || close < low - tolerance) return null;

  // The EGX-AI-compatible source can expose previous close in the `open` field.
  // Keep the quote, but clamp candle open for candle-shape calculations.
  const normalizedOpen = Math.min(Math.max(open, low), high);
  return { open: normalizedOpen, high, low, close, volume };
}

async function safePersist(work: () => Promise<unknown>) {
  try {
    await work();
  } catch {
    // Keep provider responses usable even if local snapshot persistence is temporarily unavailable.
  }
}

async function mapLimit<T, R>(items: T[], limit: number, worker: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = [];
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    for (;;) {
      const index = cursor;
      cursor += 1;
      if (index >= items.length) return;
      results[index] = await worker(items[index]);
    }
  });
  await Promise.all(workers);
  return results;
}

export class EgxAiApiProvider implements MarketDataProvider {
  readonly name = "egx-ai-api";

  private async fetchJson<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<ProviderResult<T>> {
    if (!config.EGX_AI_API_BASE_URL) return unavailable(this.name, "EGX_AI_API_BASE_URL is not configured.");
    const endpoint = new URL(`${cleanBaseUrl(config.EGX_AI_API_BASE_URL)}${path}`);
    for (const [key, value] of Object.entries(params ?? {})) {
      if (value !== undefined) endpoint.searchParams.set(key, String(value));
    }

    try {
      const response = await withRetry(async () => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), config.EGX_AI_API_TIMEOUT_MS);
        try {
          return await fetch(endpoint, {
            headers: { "User-Agent": "EGX Smart Screener EGX-AI API adapter" },
            signal: controller.signal,
          });
        } finally {
          clearTimeout(timer);
        }
      }, 2, 500);

      if (!response.ok) {
        const message = `EGX-AI API returned HTTP ${response.status} for ${endpoint.pathname}`;
        await safePersist(() => prisma.rawDataSnapshot.create({ data: { provider: this.name, endpoint: endpoint.toString(), status: "unavailable", error: message } }));
        return unavailable(this.name, message);
      }

      const payload = await response.json() as T;
      await safePersist(() => prisma.rawDataSnapshot.create({
        data: {
          provider: this.name,
          endpoint: endpoint.toString(),
          status: "available",
          payload: { path, params: params ?? {}, sampledAt: new Date().toISOString() },
        },
      }));
      return { status: "available", source: this.name, reason: SOURCE_NOTE, data: payload };
    } catch (error) {
      const message = error instanceof Error ? error.message : "EGX-AI API request failed";
      await safePersist(() => prisma.rawDataSnapshot.create({ data: { provider: this.name, endpoint: endpoint.toString(), status: "unavailable", error: message } }));
      return unavailable(this.name, `EGX-AI API is not reachable at ${cleanBaseUrl(config.EGX_AI_API_BASE_URL)}. ${message}`);
    }
  }

  private async fetchAllStocks(forceRefresh = false): Promise<ProviderResult<EgxAiStock[]>> {
    const first = await this.fetchJson<EgxAiPage<EgxAiStock>>("/api/v1/stocks", { page: 0, size: 500, forceRefresh });
    if (!first.data?.content?.length) return unavailable(this.name, first.reason ?? "EGX-AI API returned no stock rows.");
    const pages = first.data.totalPages ?? 1;
    if (pages <= 1) return { status: "available", source: this.name, reason: SOURCE_NOTE, data: first.data.content };

    const rest = await mapLimit(Array.from({ length: pages - 1 }, (_, index) => index + 1), 3, async (page) => {
      const result = await this.fetchJson<EgxAiPage<EgxAiStock>>("/api/v1/stocks", { page, size: 500, forceRefresh });
      return result.data?.content ?? [];
    });
    return { status: "available", source: this.name, reason: SOURCE_NOTE, data: [...first.data.content, ...rest.flat()] };
  }

  private async persistSymbolRows(rows: EgxAiStock[]) {
    for (const row of rows) {
      const symbol = codeFromStock(row);
      if (!symbol || !row.equity?.name) continue;
      await prisma.egxSymbol.upsert({
        where: { symbolCode: symbol },
        update: {
          companyNameEn: row.equity.name,
          sector: row.equity.sector,
          isActive: true,
          isPlaceholder: false,
        },
        create: {
          symbolCode: symbol,
          tradingviewSymbol: `EGX:${symbol}`,
          companyNameEn: row.equity.name,
          companyNameAr: null,
          sector: row.equity.sector,
          industry: null,
          isActive: true,
          isPlaceholder: false,
        },
      });
    }
  }

  private quoteFromStock(row: EgxAiStock, fallbackSymbol: string, stockMeta?: { sector?: string | null; industry?: string | null }): Quote | null {
    const symbol = codeFromStock(row) ?? fallbackSymbol.toUpperCase();
    const price = numberOrUndefined(row.currPrice);
    if (!price || price <= 0) return null;
    const open = numberOrUndefined(row.open) ?? price;
    const high = numberOrUndefined(row.highest) ?? price;
    const low = numberOrUndefined(row.lowest) ?? price;
    const volume = numberOrUndefined(row.volume) ?? 0;
    const normalized = normalizeOhlcv(open, high, low, price, volume);
    if (!normalized) return null;
    const analysisSeed: Candle[] = [{
      symbol,
      timeframe: "1D",
      time: toIsoDate(row.time),
      open: normalized.open,
      high: normalized.high,
      low: normalized.low,
      close: price,
      volume: normalized.volume,
      source: this.name,
    }];
    const analysis = buildTimeframeAnalysis(symbol, "1D", analysisSeed, false);
    return {
      symbol,
      price: round(price),
      previousClose: numberOrUndefined(row.prevClose),
      changePercent: numberOrUndefined(row.percentageOfChange),
      volume: normalized.volume,
      sector: row.equity?.sector ?? stockMeta?.sector,
      industry: stockMeta?.industry,
      orderBookStatus: "unavailable",
      orderBookNote: "EGX-AI-compatible stock-service does not expose true bid/ask or order book depth.",
      bidAskExpectation: buildBidAskExpectation({ price, orderBookStatus: "unavailable" }, analysis),
      capturedAt: toIsoDate(row.time),
    };
  }

  private async persistQuote(quote: Quote, raw: EgxAiStock) {
    await prisma.quoteSnapshot.create({
      data: {
        symbolCode: quote.symbol,
        price: quote.price,
        previousClose: quote.previousClose,
        changePercent: quote.changePercent,
        volume: quote.volume,
        marketCap: quote.marketCap,
        source: this.name,
        orderBookStatus: quote.orderBookStatus,
        orderBookNote: quote.orderBookNote,
        rawPayload: raw,
        capturedAt: new Date(quote.capturedAt),
      },
    });
  }

  async getQuote(symbol: string): Promise<ProviderResult<Quote>> {
    const normalized = symbol.toUpperCase();
    const result = await this.fetchJson<EgxAiStock>(`/api/v1/stocks/${encodeURIComponent(normalized)}`);
    if (!result.data) return unavailable(this.name, result.reason ?? `No EGX-AI quote returned for ${normalized}.`);
    const stockMeta = await prisma.egxSymbol.findUnique({ where: { symbolCode: normalized } });
    const quote = this.quoteFromStock(result.data, normalized, stockMeta ?? undefined);
    if (!quote) return unavailable(this.name, `EGX-AI quote for ${normalized} did not include a usable current price.`);
    await safePersist(() => this.persistQuote(quote, result.data!));
    return { status: "available", source: this.name, reason: SOURCE_NOTE, data: quote };
  }

  private candleFromOhclv(symbol: string, timeframe: Timeframe, row: EgxAiOhclv): Candle | null {
    const close = numberOrUndefined(row.currPrice);
    const open = numberOrUndefined(row.open);
    const high = numberOrUndefined(row.highest);
    const low = numberOrUndefined(row.lowest);
    const volume = numberOrUndefined(row.volume) ?? 0;
    if ([open, high, low, close].some((value) => value === undefined)) return null;
    const normalized = normalizeOhlcv(open!, high!, low!, close!, volume);
    if (!normalized) return null;
    return {
      symbol,
      timeframe,
      time: toIsoDate(row.time),
      open: round(normalized.open),
      high: round(normalized.high),
      low: round(normalized.low),
      close: round(normalized.close),
      volume: normalized.volume,
      source: this.name,
    };
  }

  private candleFromStockSnapshot(symbol: string, row: EgxAiStock): Candle | null {
    return this.candleFromOhclv(symbol, "1D", {
      time: row.time,
      currPrice: row.currPrice,
      open: row.open,
      highest: row.highest,
      prevClose: row.prevClose,
      lowest: row.lowest,
      volume: row.volume,
    });
  }

  private async persistCandles(symbol: string, candles: Candle[]) {
    for (const candle of candles.slice(-500)) {
      await prisma.candle.upsert({
        where: { symbolCode_timeframe_candleTime: { symbolCode: symbol, timeframe: candle.timeframe, candleTime: new Date(candle.time) } },
        update: {
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
          volume: candle.volume,
          source: this.name,
          quality: "real",
          importedAt: new Date(),
        },
        create: {
          symbolCode: symbol,
          timeframe: candle.timeframe,
          candleTime: new Date(candle.time),
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
          volume: candle.volume,
          source: this.name,
          quality: "real",
        },
      });
    }
  }

  private async storedDailyHistory(symbol: string): Promise<Candle[]> {
    const rows = await prisma.candle.findMany({
      where: { symbolCode: symbol, timeframe: "1D", source: { in: [this.name, HISTORICAL_BACKFILL_SOURCE] } },
      orderBy: [{ candleTime: "asc" }, { importedAt: "asc" }],
      take: 700,
    });
    const byDay = new Map<string, Candle & { quality?: string; importedAt?: Date }>();
    for (const row of rows) {
      const candle: Candle & { quality?: string; importedAt?: Date } = {
        symbol: row.symbolCode,
        timeframe: "1D",
        time: row.candleTime.toISOString(),
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume,
        source: row.source,
        quality: row.quality,
        importedAt: row.importedAt,
      };
      const key = egyptDateKey(row.candleTime);
      const existing = byDay.get(key);
      if (!existing || candlePriority(candle) >= candlePriority(existing)) byDay.set(key, candle);
    }
    return [...byDay.values()]
      .sort((a, b) => a.time.localeCompare(b.time))
      .slice(-260)
      .map(({ quality: _quality, importedAt: _importedAt, ...candle }) => candle);
  }

  async getCandles(symbol: string, timeframe: Timeframe): Promise<ProviderResult<Candle[]>> {
    const normalized = symbol.toUpperCase();
    if (timeframe !== "1D") {
      return unavailable(this.name, "Embedded EGX-AI-compatible source only exposes daily/current-session OHLCV snapshots. It does not provide true 15M, 30M, 1H, or 4H candles, so intraday strategy panels are not faked.");
    }
    const { periodParam, intervalParam } = intervalFor(timeframe);
    const result = await this.fetchJson<EgxAiPage<EgxAiOhclv>>(`/api/v1/historical-stocks/${encodeURIComponent(normalized)}`, {
      periodParam,
      intervalParam,
      page: 0,
      size: 500,
    });
    const rows = result.data?.content ?? [];
    const candles = rows
      .map((row) => this.candleFromOhclv(normalized, timeframe, row))
      .filter((row): row is Candle => Boolean(row))
      .sort((a, b) => a.time.localeCompare(b.time));
    if (candles.length) await safePersist(() => this.persistCandles(normalized, candles));
    const mergedCandles = await this.storedDailyHistory(normalized);
    if (!mergedCandles.length) return unavailable(this.name, result.reason ?? `EGX-AI API returned no usable ${timeframe} candles for ${normalized}.`);
    const latest = mergedCandles[mergedCandles.length - 1];
    const hasLiveSnapshot = candles.length > 0 && egyptDateKey(latest.time) === egyptDateKey(candles[candles.length - 1].time);
    return {
      status: hasLiveSnapshot && mergedCandles.length >= 20 ? "available" : "degraded",
      source: this.name,
      reason: `${SOURCE_NOTE} ${BACKFILL_NOTE} Omar Smart PRO V3 uses ${mergedCandles.length} daily candles, with the latest EGX-AI-compatible snapshot preferred for ${egyptDateKey(latest.time)}. Requested period=${periodParam}, interval=${intervalParam}.`,
      data: mergedCandles,
      meta: {
        latestReturnedCandleAt: latest.time,
        latestReturnedCandleDateEgypt: egyptDateKey(latest.time),
        historyCandles: mergedCandles.length,
        latestSnapshotFromActiveProvider: hasLiveSnapshot,
        historicalBackfillSource: HISTORICAL_BACKFILL_SOURCE,
      },
    };
  }

  private async rowFromStock(row: EgxAiStock, stockMeta?: { symbolCode: string; companyNameEn: string; sector?: string | null; industry?: string | null }): Promise<ScannerRow | null> {
    const symbol = codeFromStock(row) ?? stockMeta?.symbolCode;
    if (!symbol) return null;
    const quote = this.quoteFromStock(row, symbol, stockMeta);
    if (!quote) {
      return {
        symbol,
        companyName: row.equity?.name ?? stockMeta?.companyNameEn ?? symbol,
        sector: row.equity?.sector ?? stockMeta?.sector,
        dataQuality: "unavailable",
        reason: "EGX-AI row did not include a valid current OHLCV snapshot.",
      };
    }

    const snapshotCandle = this.candleFromStockSnapshot(symbol, row);
    if (snapshotCandle) await safePersist(() => this.persistCandles(symbol, [snapshotCandle]));
    const candles = await this.getCandles(symbol, "1D");
    const candleData = candles.data ?? [];
    const rec = candleData.length ? recommendation(candleData) : { recommendation: "WATCH" as const, confidence: 30, reason: "Quote is available, but historical candles are unavailable for technical confirmation." };
    const analysis = candleData.length >= 20 ? buildTimeframeAnalysis(symbol, "1D", candleData, false) : null;
    return {
      symbol,
      companyName: row.equity?.name ?? stockMeta?.companyNameEn ?? symbol,
      sector: row.equity?.sector ?? stockMeta?.sector,
      price: quote.price,
      changePercent: quote.changePercent,
      volume: quote.volume,
      capturedAt: quote.capturedAt,
      orderBookStatus: quote.orderBookStatus,
      orderBookNote: quote.orderBookNote,
      bidAskExpectation: buildBidAskExpectation({ price: quote.price, orderBookStatus: "unavailable" }, analysis),
      recommendation: rec.recommendation,
      confidence: rec.confidence,
      dataQuality: "partial",
      reason: `${rec.reason} (${SOURCE_NOTE} ${BACKFILL_NOTE})`,
      analysis: analysis ?? undefined,
    };
  }

  async getScanner(forceRefresh = false): Promise<ProviderResult<ScannerRow[]>> {
    const result = await this.fetchAllStocks(forceRefresh);
    if (!result.data?.length) return unavailable(this.name, result.reason ?? "No EGX-AI scanner rows returned.");
    await safePersist(() => this.persistSymbolRows(result.data!));

    const localSymbols = await prisma.egxSymbol.findMany({ where: { isActive: true }, orderBy: { symbolCode: "asc" } });
    const apiRowsBySymbol = new Map(result.data.map((row) => [codeFromStock(row), row]).filter((entry): entry is [string, EgxAiStock] => Boolean(entry[0])));
    const rows: ScannerRow[] = [];

    for (const stock of localSymbols) {
      const apiRow = apiRowsBySymbol.get(stock.symbolCode);
      if (!apiRow) {
        rows.push({
          symbol: stock.symbolCode,
          companyName: stock.companyNameEn,
          sector: stock.sector,
          dataQuality: "unavailable",
          reason: `EGX-AI API did not return ${stock.symbolCode} in /api/v1/stocks.`,
        });
        continue;
      }
      const row = await this.rowFromStock(apiRow, stock);
      if (row) rows.push(row);
    }

    for (const apiRow of result.data) {
      const symbol = codeFromStock(apiRow);
      if (!symbol || rows.some((row) => row.symbol === symbol)) continue;
      const row = await this.rowFromStock(apiRow);
      if (row) rows.push(row);
    }

    return { status: "available", source: this.name, reason: SOURCE_NOTE, data: rows };
  }

  async refreshScanner(): Promise<ProviderResult<ScannerRow[]>> {
    return this.getScanner(true);
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
