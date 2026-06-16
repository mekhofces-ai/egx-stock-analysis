import { prisma } from "../db.js";
import { config } from "../config.js";
import { buildTimeframeAnalysis, recommendation } from "../services/technicalAnalysis.js";
import { buildBidAskExpectation } from "../services/bidAskExpectation.js";
import type { Candle, ProviderResult, Quote, ScannerRow, Timeframe } from "../types.js";
import { withRetry } from "../utils/retry.js";
import type { MarketDataProvider } from "./MarketDataProvider.js";
import { unavailable } from "./MarketDataProvider.js";

type YahooChartResult = {
  chart?: {
    result?: Array<{
      meta?: {
        currency?: string;
        symbol?: string;
        longName?: string;
        regularMarketTime?: number;
      };
      timestamp?: number[];
      indicators?: {
        quote?: Array<{
          open?: Array<number | null>;
          high?: Array<number | null>;
          low?: Array<number | null>;
          close?: Array<number | null>;
          volume?: Array<number | null>;
        }>;
      };
    }>;
    error?: { code?: string; description?: string } | null;
  };
};

const SOURCE_NOTE = "Public delayed daily chart data. Not licensed real-time EGX data and not order book depth.";
type YahooChartPayloadResult = NonNullable<NonNullable<YahooChartResult["chart"]>["result"]>[number];
type LatestUpstreamRowStatus = {
  time: string;
  dateEgypt: string;
  status: "complete" | "incomplete";
  missingFields: string[];
};

function toYahooSymbol(symbol: string) {
  return `${symbol.replace(/^EGX:/i, "").toUpperCase()}.CA`;
}

function toDate(seconds: number) {
  return new Date(seconds * 1000);
}

function round(value: number) {
  return Number(value.toFixed(2));
}

function latest<T>(values: T[]) {
  return values[values.length - 1];
}

function isMissingMarketValue(value: number | null | undefined) {
  return value === null || value === undefined || Number.isNaN(value);
}

function egyptDateKey(date: Date) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Africa/Cairo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

async function mapLimit<T, R>(items: T[], limit: number, worker: (item: T, index: number) => Promise<R>): Promise<R[]> {
  const results: R[] = [];
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    for (;;) {
      const index = cursor;
      cursor += 1;
      if (index >= items.length) return;
      results[index] = await worker(items[index], index);
    }
  });
  await Promise.all(workers);
  return results;
}

async function safePersist(work: () => Promise<void>) {
  try {
    await work();
  } catch {
    // Market data should still be returned if local snapshot storage is temporarily busy.
  }
}

export class PublicYahooChartProvider implements MarketDataProvider {
  readonly name = "public-yahoo-chart";
  private latestFetchErrors = new Map<string, string>();

  private async fetchChart(symbol: string, range: string) {
    const yahooSymbol = toYahooSymbol(symbol);
    const endpoint = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(yahooSymbol)}?range=${range}&interval=1d`;
    try {
      const response = await withRetry(() => fetch(endpoint, { headers: { "User-Agent": "EGX Smart Screener local data adapter" } }));
      if (!response.ok) throw new Error(`Public chart endpoint for ${yahooSymbol} returned ${response.status}`);
      const payload = await response.json() as YahooChartResult;
      const result = payload.chart?.result?.[0] ?? null;
      if (payload.chart?.error) {
        const description = payload.chart.error.description ?? payload.chart.error.code ?? "Public chart returned an error";
        this.latestFetchErrors.set(symbol.toUpperCase(), `${description} (${yahooSymbol})`);
      } else {
        this.latestFetchErrors.delete(symbol.toUpperCase());
      }
      await prisma.rawDataSnapshot.create({
        data: {
          provider: this.name,
          endpoint,
          symbolCode: symbol.toUpperCase(),
          status: payload.chart?.error ? "unavailable" : "degraded",
          payload: {
            symbol: yahooSymbol,
            range,
            error: payload.chart?.error ?? null,
            rows: result?.timestamp?.length ?? 0,
            latestRow: this.getLatestUpstreamRow(result),
          },
        },
      });
      if (payload.chart?.error) return null;
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : "Public chart request failed";
      this.latestFetchErrors.set(symbol.toUpperCase(), message);
      await prisma.rawDataSnapshot.create({
        data: {
          provider: this.name,
          endpoint,
          symbolCode: symbol.toUpperCase(),
          status: "unavailable",
          error: message,
        },
      });
      return null;
    }
  }

  private unavailableReason(symbol: string, latestError?: string | null) {
    const yahooSymbol = toYahooSymbol(symbol);
    const diagnostic = latestError ?? this.latestFetchErrors.get(symbol.toUpperCase());
    return diagnostic
      ? `No public delayed daily chart data found for ${yahooSymbol}. Latest provider check: ${diagnostic}. The active free provider does not currently publish this EGX ticker; use a licensed/supplemental provider for this symbol.`
      : `No public delayed daily chart data found for ${yahooSymbol}. The active free provider may not publish this EGX ticker; use a licensed/supplemental provider for this symbol.`;
  }

  private getLatestUpstreamRow(result: YahooChartPayloadResult | null | undefined): LatestUpstreamRowStatus | undefined {
    const timestamps = result?.timestamp ?? [];
    const quote = result?.indicators?.quote?.[0];
    if (!timestamps.length || !quote) return undefined;

    const index = timestamps.length - 1;
    const rowDate = toDate(timestamps[index]);
    const fields: Record<"open" | "high" | "low" | "close" | "volume", number | null | undefined> = {
      open: quote.open?.[index],
      high: quote.high?.[index],
      low: quote.low?.[index],
      close: quote.close?.[index],
      volume: quote.volume?.[index],
    };
    const missingFields = Object.entries(fields)
      .filter(([, value]) => isMissingMarketValue(value))
      .map(([field]) => field);

    return {
      time: rowDate.toISOString(),
      dateEgypt: egyptDateKey(rowDate),
      status: missingFields.length ? "incomplete" : "complete",
      missingFields,
    };
  }

  private buildFreshnessMeta(candles: Candle[], upstreamLatestRow?: LatestUpstreamRowStatus, providerCheck: "cache" | "refresh" = "refresh") {
    const returned = candles.length ? latest(candles) : undefined;
    return {
      providerCheck,
      latestReturnedCandleAt: returned?.time ?? null,
      latestReturnedCandleDateEgypt: returned ? egyptDateKey(new Date(returned.time)) : null,
      upstreamLatestCandleAt: upstreamLatestRow?.time ?? null,
      upstreamLatestCandleDateEgypt: upstreamLatestRow?.dateEgypt ?? null,
      upstreamLatestCandleStatus: upstreamLatestRow?.status ?? (providerCheck === "cache" ? "not_checked_cache" : "missing"),
      upstreamMissingFields: upstreamLatestRow?.missingFields ?? [],
    };
  }

  private buildFreshnessReason(base: string, candles: Candle[], upstreamLatestRow?: LatestUpstreamRowStatus, providerCheck: "cache" | "refresh" = "refresh") {
    const returned = candles.length ? latest(candles) : undefined;
    const returnedDateEgypt = returned ? egyptDateKey(new Date(returned.time)) : undefined;
    const todayEgypt = egyptDateKey(new Date());

    if (upstreamLatestRow?.status === "incomplete") {
      const missing = upstreamLatestRow.missingFields.join(", ") || "OHLCV";
      return `${base} Latest upstream daily row for ${upstreamLatestRow.dateEgypt} is incomplete (${missing} missing), so the chart shows latest completed candle ${returnedDateEgypt ?? "none"}. Configure a licensed intraday/live EGX provider for open-session candles.`;
    }

    if (upstreamLatestRow?.status === "complete") {
      return `${base} Latest completed daily candle is ${returnedDateEgypt ?? upstreamLatestRow.dateEgypt}.`;
    }

    if (returnedDateEgypt && returnedDateEgypt < todayEgypt) {
      const checkText = providerCheck === "cache" ? "stored provider snapshot" : "active provider";
      return `${base} Latest completed candle from the ${checkText} is ${returnedDateEgypt}; no usable ${todayEgypt} candle is available from the active free provider yet.`;
    }

    return returnedDateEgypt ? `${base} Latest completed daily candle is ${returnedDateEgypt}.` : base;
  }

  private parseCandles(symbol: string, result: Awaited<ReturnType<PublicYahooChartProvider["fetchChart"]>>): Candle[] {
    const timestamps = result?.timestamp ?? [];
    const quote = result?.indicators?.quote?.[0];
    if (!timestamps.length || !quote) return [];

    const candles: Candle[] = [];
    for (let index = 0; index < timestamps.length; index += 1) {
      const open = quote.open?.[index];
      const high = quote.high?.[index];
      const low = quote.low?.[index];
      const close = quote.close?.[index];
      const volume = quote.volume?.[index];
      if ([open, high, low, close, volume].some(isMissingMarketValue)) continue;
      candles.push({
        symbol: symbol.toUpperCase(),
        timeframe: "1D",
        time: toDate(timestamps[index]).toISOString(),
        open: round(open as number),
        high: round(high as number),
        low: round(low as number),
        close: round(close as number),
        volume: volume as number,
        source: this.name,
      });
    }
    return candles;
  }

  private async persist(symbol: string, candles: Candle[], quote?: Quote, persistHistory = false) {
    const candlesToStore = persistHistory ? candles.slice(-260) : candles.slice(-1);
    for (const candle of candlesToStore) {
      await prisma.candle.upsert({
        where: { symbolCode_timeframe_candleTime: { symbolCode: symbol.toUpperCase(), timeframe: "1D", candleTime: new Date(candle.time) } },
        update: {
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
          volume: candle.volume,
          source: this.name,
          quality: "partial",
          importedAt: new Date(),
        },
        create: {
          symbolCode: symbol.toUpperCase(),
          timeframe: "1D",
          candleTime: new Date(candle.time),
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
          volume: candle.volume,
          source: this.name,
          quality: "partial",
        },
      });
    }

    if (quote) {
      await prisma.quoteSnapshot.create({
        data: {
          symbolCode: symbol.toUpperCase(),
          price: quote.price,
          previousClose: quote.previousClose,
          changePercent: quote.changePercent,
          volume: quote.volume,
          source: this.name,
          orderBookStatus: "unavailable",
          orderBookNote: SOURCE_NOTE,
          capturedAt: new Date(quote.capturedAt),
        },
      });
    }
  }

  private rowFromCandles(stock: { symbolCode: string; companyNameEn: string; sector?: string | null; industry?: string | null }, candles: Candle[], providerCheck: "cache" | "refresh" = "cache"): ScannerRow {
    const ordered = [...candles].sort((a, b) => a.time.localeCompare(b.time));
    const current = latest(ordered);
    const previous = ordered.length > 1 ? ordered[ordered.length - 2] : undefined;
    const previousGapDays = previous ? (new Date(current.time).getTime() - new Date(previous.time).getTime()) / 86_400_000 : Number.POSITIVE_INFINITY;
    const hasRecentPrevious = Boolean(previous && previousGapDays <= 10);
    const checkText = providerCheck === "cache" ? "served from stored provider snapshots" : "refreshed from active free provider";
    const rec = recommendation(ordered);
    const analysis = buildTimeframeAnalysis(stock.symbolCode, "1D", ordered, true);
    const bidAskExpectation = buildBidAskExpectation({ price: current.close, orderBookStatus: "unavailable" }, analysis);
    const changePercent = hasRecentPrevious && previous ? round(((current.close - previous.close) / previous.close) * 100) : undefined;
    return {
      symbol: stock.symbolCode,
      companyName: stock.companyNameEn,
      sector: stock.sector,
      price: current.close,
      changePercent,
      volume: current.volume,
      orderBookStatus: "unavailable",
      orderBookNote: SOURCE_NOTE,
      bidAskExpectation,
      recommendation: rec.recommendation,
      confidence: rec.confidence,
      dataQuality: "partial",
      reason: `${rec.reason} (${SOURCE_NOTE}; ${checkText}${hasRecentPrevious ? "" : "; previous close is stale, so change % is hidden"})`,
      analysis: analysis ?? undefined,
    };
  }

  private async getStoredScannerRows(symbols: Array<{ symbolCode: string; companyNameEn: string; sector?: string | null; industry?: string | null }>): Promise<ScannerRow[]> {
    const candles = await prisma.candle.findMany({
      where: { timeframe: "1D", source: this.name },
      orderBy: [{ symbolCode: "asc" }, { candleTime: "desc" }],
    });
    if (!candles.length) return [];

    const bySymbol = new Map<string, Candle[]>();
    for (const candle of candles) {
      const rows = bySymbol.get(candle.symbolCode) ?? [];
      if (rows.length >= 260) continue;
      rows.push({
        symbol: candle.symbolCode,
        timeframe: "1D",
        time: candle.candleTime.toISOString(),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume,
        source: candle.source,
      });
      bySymbol.set(candle.symbolCode, rows);
    }

    const unavailableSymbols = symbols.filter((stock) => !bySymbol.has(stock.symbolCode)).map((stock) => stock.symbolCode);
    const latestErrors = new Map<string, string>();
    if (unavailableSymbols.length) {
      const snapshots = await prisma.rawDataSnapshot.findMany({
        where: { provider: this.name, status: "unavailable", symbolCode: { in: unavailableSymbols } },
        orderBy: { capturedAt: "desc" },
        take: unavailableSymbols.length * 3,
      });
      for (const snapshot of snapshots) {
        if (snapshot.symbolCode && snapshot.error && !latestErrors.has(snapshot.symbolCode)) latestErrors.set(snapshot.symbolCode, snapshot.error);
      }
    }

    return symbols.map((stock) => {
      const stored = bySymbol.get(stock.symbolCode);
      if (!stored?.length) {
        return {
          symbol: stock.symbolCode,
          companyName: stock.companyNameEn,
          sector: stock.sector,
          dataQuality: "unavailable",
          reason: this.unavailableReason(stock.symbolCode, latestErrors.get(stock.symbolCode)),
        };
      }
      return this.rowFromCandles(stock, stored, "cache");
    });
  }

  private async fetchScannerRows(symbols: Array<{ symbolCode: string; companyNameEn: string; sector?: string | null; industry?: string | null }>): Promise<ScannerRow[]> {
    const storedRows = await this.getStoredScannerRows(symbols);
    const storedBySymbol = new Map(storedRows.filter((row) => row.dataQuality !== "unavailable").map((row) => [row.symbol, row]));

    return mapLimit(symbols, 4, async (stock): Promise<ScannerRow> => {
      const result = await this.fetchChart(stock.symbolCode, "1y");
      const candles = this.parseCandles(stock.symbolCode, result);
      if (!candles.length) {
        const stored = storedBySymbol.get(stock.symbolCode);
        if (stored) {
          return {
            ...stored,
            reason: `${stored.reason} Active provider refresh returned no usable completed candle, so the latest stored snapshot remains visible.`,
          };
        }
        return {
          symbol: stock.symbolCode,
          companyName: stock.companyNameEn,
          sector: stock.sector,
          dataQuality: "unavailable",
          reason: this.unavailableReason(stock.symbolCode),
        };
      }
      const current = latest(candles);
      const analysis = buildTimeframeAnalysis(stock.symbolCode, "1D", candles, true);
      const bidAskExpectation = buildBidAskExpectation({ price: current.close, orderBookStatus: "unavailable" }, analysis);
      await safePersist(() => this.persist(stock.symbolCode, candles, {
        symbol: stock.symbolCode,
        price: current.close,
        previousClose: candles.length > 1 ? candles[candles.length - 2].close : undefined,
        changePercent: candles.length > 1 ? round(((current.close - candles[candles.length - 2].close) / candles[candles.length - 2].close) * 100) : undefined,
        volume: current.volume,
        sector: stock.sector,
        industry: stock.industry,
        orderBookStatus: "unavailable",
        orderBookNote: SOURCE_NOTE,
        bidAskExpectation,
        capturedAt: current.time,
      }));
      return this.rowFromCandles(stock, candles, "refresh");
    });
  }

  private async getStoredCandles(symbol: string): Promise<{ candles: Candle[]; latestImportedAt?: Date }> {
    const rows = await prisma.candle.findMany({
      where: { symbolCode: symbol.toUpperCase(), timeframe: "1D", source: this.name },
      orderBy: { candleTime: "desc" },
      take: 260,
    });
    const candles = rows
      .map((row): Candle => ({
        symbol: row.symbolCode,
        timeframe: "1D",
        time: row.candleTime.toISOString(),
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume,
        source: row.source,
      }))
      .sort((a, b) => a.time.localeCompare(b.time));
    return { candles, latestImportedAt: rows[0]?.importedAt };
  }

  private shouldRefreshStoredCandles(candles: Candle[], latestImportedAt?: Date) {
    if (!candles.length) return true;
    if (latestImportedAt && Date.now() - latestImportedAt.getTime() < config.CACHE_TTL_MS) return false;
    const latestStoredDate = egyptDateKey(new Date(latest(candles).time));
    const todayEgypt = egyptDateKey(new Date());
    return latestStoredDate < todayEgypt;
  }

  async getCandles(symbol: string, timeframe: Timeframe): Promise<ProviderResult<Candle[]>> {
    if (timeframe !== "1D") return unavailable(this.name, "Public delayed fallback only provides daily candles. Configure a licensed provider for intraday/live EGX data.");
    const stored = await this.getStoredCandles(symbol);
    const storedCandles = stored.candles;
    if (storedCandles.length && !this.shouldRefreshStoredCandles(storedCandles, stored.latestImportedAt)) {
      const reason = this.buildFreshnessReason(`${SOURCE_NOTE} Served from stored provider snapshots to avoid hammering the public feed.`, storedCandles, undefined, "cache");
      return { status: "degraded", source: this.name, reason, data: storedCandles, meta: this.buildFreshnessMeta(storedCandles, undefined, "cache") };
    }

    const result = await this.fetchChart(symbol, "1y");
    const upstreamLatestRow = this.getLatestUpstreamRow(result);
    const candles = this.parseCandles(symbol, result);
    if (!candles.length) {
      if (storedCandles.length) {
        const reason = this.buildFreshnessReason(`${SOURCE_NOTE} Upstream refresh returned no usable candles, so stored candles are shown.`, storedCandles, upstreamLatestRow);
        return { status: "degraded", source: this.name, reason, data: storedCandles, meta: this.buildFreshnessMeta(storedCandles, upstreamLatestRow) };
      }
      return unavailable(this.name, this.unavailableReason(symbol));
    }
    await safePersist(() => this.persist(symbol, candles, undefined, true));
    return { status: "degraded", source: this.name, reason: this.buildFreshnessReason(SOURCE_NOTE, candles, upstreamLatestRow), data: candles, meta: this.buildFreshnessMeta(candles, upstreamLatestRow) };
  }

  async getQuote(symbol: string): Promise<ProviderResult<Quote>> {
    const stock = await prisma.egxSymbol.findUnique({ where: { symbolCode: symbol.toUpperCase() } });
    const result = await this.fetchChart(symbol, "1mo");
    const candles = this.parseCandles(symbol, result);
    if (!candles.length) return unavailable(this.name, this.unavailableReason(symbol));
    const current = latest(candles);
    const previous = candles.length > 1 ? candles[candles.length - 2] : undefined;
    const changePercent = previous ? round(((current.close - previous.close) / previous.close) * 100) : undefined;
    const quote: Quote = {
      symbol: symbol.toUpperCase(),
      price: current.close,
      previousClose: previous?.close,
      changePercent,
      volume: current.volume,
      sector: stock?.sector,
      industry: stock?.industry,
      orderBookStatus: "unavailable",
      orderBookNote: SOURCE_NOTE,
      bidAskExpectation: buildBidAskExpectation({ price: current.close, orderBookStatus: "unavailable" }, buildTimeframeAnalysis(symbol.toUpperCase(), "1D", candles, true)),
      capturedAt: current.time,
    };
    await safePersist(() => this.persist(symbol, candles, quote));
    return { status: "degraded", source: this.name, reason: SOURCE_NOTE, data: quote };
  }

  async getScanner(): Promise<ProviderResult<ScannerRow[]>> {
    const symbols = await prisma.egxSymbol.findMany({ where: { isActive: true }, orderBy: { symbolCode: "asc" } });
    const storedRows = await this.getStoredScannerRows(symbols);
    if (storedRows.length) {
      return { status: "degraded", source: this.name, reason: `${SOURCE_NOTE} Scanner served from stored provider snapshots to avoid hammering the public feed.`, data: storedRows };
    }

    const rows = await this.fetchScannerRows(symbols);
    return { status: "degraded", source: this.name, reason: SOURCE_NOTE, data: rows };
  }

  async refreshScanner(): Promise<ProviderResult<ScannerRow[]>> {
    const symbols = await prisma.egxSymbol.findMany({ where: { isActive: true }, orderBy: { symbolCode: "asc" } });
    const rows = await this.fetchScannerRows(symbols);
    return {
      status: "degraded",
      source: this.name,
      reason: `${SOURCE_NOTE} Manual refresh checked the active free provider for every active EGX symbol.`,
      data: rows,
    };
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
