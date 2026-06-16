import { Router } from "express";
import { z } from "zod";
import { config } from "./config.js";
import { prisma } from "./db.js";
import type { MarketDataService } from "./services/marketDataService.js";
import { AIAnalysisService } from "./services/aiAnalysisService.js";
import type { Timeframe } from "./types.js";

const symbolSchema = z.string().regex(/^[A-Z0-9]{2,12}$/);
const timeframeSchema = z.enum(["1m", "5m", "15m", "15M", "30M", "1h", "1H", "4H", "1D"]).default("1D");
const candleImportSchema = z.object({
  symbol: symbolSchema,
  timeframe: timeframeSchema,
  time: z.coerce.date(),
  open: z.coerce.number(),
  high: z.coerce.number(),
  low: z.coerce.number(),
  close: z.coerce.number(),
  volume: z.coerce.number(),
  source: z.string().default("manual-csv"),
});

function parseCsvCandles(csv: string) {
  const lines = csv.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const [header, ...rows] = lines;
  const normalized = header?.split(",").map((part) => part.trim().toLowerCase()) ?? [];
  const required = ["symbol", "timeframe", "time", "open", "high", "low", "close", "volume"];
  if (!required.every((field) => normalized.includes(field))) {
    throw new Error("CSV header must include Symbol,Timeframe,Time,Open,High,Low,Close,Volume");
  }
  return rows.map((line) => {
    const values = line.split(",").map((part) => part.trim());
    const record = Object.fromEntries(normalized.map((field, index) => [field, values[index]]));
    return {
      symbol: String(record.symbol ?? "").toUpperCase(),
      timeframe: record.timeframe,
      time: record.time,
      open: record.open,
      high: record.high,
      low: record.low,
      close: record.close,
      volume: record.volume,
      source: "manual-csv",
    };
  });
}

function round(value: number, digits = 2) {
  return Number(value.toFixed(digits));
}

async function fetchGoldUsdContext() {
  const endpoint = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range=5d&interval=1d";
  try {
    const response = await fetch(endpoint, { headers: { "User-Agent": "EGX Smart Screener market context" } });
    if (!response.ok) throw new Error(`Gold context endpoint returned ${response.status}`);
    const payload = await response.json() as {
      chart?: {
        result?: Array<{
          meta?: { currency?: string; exchangeName?: string; regularMarketPrice?: number };
          timestamp?: number[];
          indicators?: { quote?: Array<{ close?: Array<number | null>; volume?: Array<number | null> }> };
        }>;
        error?: { description?: string } | null;
      };
    };
    if (payload.chart?.error) throw new Error(payload.chart.error.description ?? "Gold context provider error");
    const result = payload.chart?.result?.[0];
    const timestamps = result?.timestamp ?? [];
    const quote = result?.indicators?.quote?.[0];
    if (!timestamps.length || !quote?.close?.length) throw new Error("Gold context provider returned no usable rows");

    const validIndexes = timestamps
      .map((_, index) => index)
      .filter((index) => quote.close?.[index] !== null && quote.close?.[index] !== undefined && !Number.isNaN(quote.close[index]));
    const latestIndex = validIndexes[validIndexes.length - 1];
    const previousIndex = validIndexes[validIndexes.length - 2];
    if (latestIndex === undefined) throw new Error("Gold context provider returned no valid close");

    const price = Number(quote.close?.[latestIndex]);
    const previousClose = previousIndex !== undefined ? Number(quote.close?.[previousIndex]) : undefined;
    const changePercent = previousClose ? round(((price - previousClose) / previousClose) * 100, 2) : undefined;

    return {
      status: "degraded",
      source: "public-yahoo-chart",
      reason: "Public delayed COMEX gold futures context. Not spot XAU/USD and not a live licensed feed.",
      data: {
        symbol: "GC=F",
        label: "Gold Futures",
        price: round(price, 2),
        previousClose: previousClose ? round(previousClose, 2) : undefined,
        changePercent,
        currency: result?.meta?.currency ?? "USD",
        exchange: result?.meta?.exchangeName ?? "CMX",
        lastUpdate: new Date(timestamps[latestIndex] * 1000).toISOString(),
        volume: quote.volume?.[latestIndex] ?? undefined,
      },
    };
  } catch (error) {
    return {
      status: "unavailable",
      source: "public-yahoo-chart",
      reason: error instanceof Error ? error.message : "Gold context unavailable",
    };
  }
}

export function createRoutes(marketData: MarketDataService) {
  const router = Router();
  const ai = new AIAnalysisService();

  router.get("/symbols", async (_req, res) => {
    const symbols = await prisma.egxSymbol.findMany({ orderBy: { symbolCode: "asc" } });
    res.json(symbols);
  });

  router.get("/market/quote/:symbol", async (req, res) => {
    const parsed = symbolSchema.safeParse(req.params.symbol.toUpperCase());
    if (!parsed.success) return res.status(400).json({ status: "unavailable", reason: "Invalid symbol" });
    res.json(await marketData.getQuote(parsed.data));
  });

  router.get("/market/candles/:symbol", async (req, res) => {
    const symbol = symbolSchema.safeParse(req.params.symbol.toUpperCase());
    const timeframe = timeframeSchema.safeParse(req.query.timeframe ?? "1D");
    if (!symbol.success || !timeframe.success) return res.status(400).json({ status: "unavailable", reason: "Invalid symbol or timeframe" });
    res.json(await marketData.getCandles(symbol.data, timeframe.data as Timeframe));
  });

  router.get("/market/scanner", async (_req, res) => res.json(await marketData.getScanner()));
  router.post("/market/refresh", async (_req, res) => {
    const startedAt = new Date();
    const scanner = await marketData.refreshScanner();
    const rows = scanner.data ?? [];
    const available = rows.filter((row) => row.dataQuality !== "unavailable").length;
    const unavailable = rows.filter((row) => row.dataQuality === "unavailable").length;
    const priced = rows.filter((row) => typeof row.price === "number").length;
    const latestCompletedCandleAt = rows
      .map((row) => row.analysis?.candleTimeEgypt)
      .filter((time): time is string => Boolean(time))
      .sort()
      .at(-1) ?? null;
    const unavailableSymbols = rows
      .filter((row) => row.dataQuality === "unavailable")
      .map((row) => row.symbol);
    await prisma.rawDataSnapshot.create({
      data: {
        provider: scanner.source,
        endpoint: "/api/market/refresh",
        status: scanner.status,
        payload: { total: rows.length, available, unavailable, priced, latestCompletedCandleAt, unavailableSymbols, reason: scanner.reason },
      },
    });
    res.json({
      ...scanner,
      refreshedAt: new Date().toISOString(),
      durationMs: Date.now() - startedAt.getTime(),
      summary: { total: rows.length, available, unavailable, priced, latestCompletedCandleAt, unavailableSymbols },
    });
  });
  router.get("/market/top-gainers", async (_req, res) => res.json(await marketData.getTopGainers()));
  router.get("/market/top-losers", async (_req, res) => res.json(await marketData.getTopLosers()));
  router.get("/market/most-active", async (_req, res) => res.json(await marketData.getMostActive()));
  router.get("/market/context/gold", async (_req, res) => res.json(await fetchGoldUsdContext()));

  router.get("/market/analysis/:symbol", async (req, res) => {
    const symbol = symbolSchema.safeParse(req.params.symbol.toUpperCase());
    const timeframe = timeframeSchema.safeParse(req.query.timeframe ?? "1D");
    if (!symbol.success || !timeframe.success) return res.status(400).json({ status: "unavailable", reason: "Invalid symbol or timeframe" });
    res.json(await marketData.getAnalysis(symbol.data, timeframe.data as Timeframe));
  });

  router.get("/market/ai-analysis/:symbol", async (req, res) => {
    const symbol = symbolSchema.safeParse(req.params.symbol.toUpperCase());
    const timeframe = timeframeSchema.safeParse(req.query.timeframe ?? "1D");
    if (!symbol.success || !timeframe.success) return res.status(400).json({ status: "unavailable", reason: "Invalid symbol or timeframe" });
    const candles = await marketData.getCandles(symbol.data, timeframe.data as Timeframe);
    res.json(candles.data ? { status: "available", source: candles.source, data: ai.analyze(symbol.data, candles.data) } : candles);
  });

  router.post("/import/candles", async (req, res) => {
    try {
      const rawRows = typeof req.body === "string" ? parseCsvCandles(req.body) : z.object({ candles: z.array(z.unknown()) }).parse(req.body).candles;
      const rows = rawRows.map((row) => {
        const record = row as Record<string, unknown>;
        return candleImportSchema.parse({ ...record, symbol: String(record.symbol ?? "").toUpperCase() });
      });
      let imported = 0;
      const errors: string[] = [];

      for (const row of rows) {
        const symbol = await prisma.egxSymbol.findUnique({ where: { symbolCode: row.symbol } });
        if (!symbol) {
          errors.push(`${row.symbol}: symbol is not in EGX universe`);
          continue;
        }
        await prisma.candle.upsert({
          where: { symbolCode_timeframe_candleTime: { symbolCode: row.symbol, timeframe: row.timeframe, candleTime: row.time } },
          update: {
            open: row.open,
            high: row.high,
            low: row.low,
            close: row.close,
            volume: row.volume,
            source: row.source,
            rawPayload: row,
            importedAt: new Date(),
          },
          create: {
            symbolCode: row.symbol,
            timeframe: row.timeframe,
            candleTime: row.time,
            open: row.open,
            high: row.high,
            low: row.low,
            close: row.close,
            volume: row.volume,
            source: row.source,
            rawPayload: row,
          },
        });
        await prisma.quoteSnapshot.create({
          data: {
            symbolCode: row.symbol,
            price: row.close,
            volume: row.volume,
            source: row.source,
            orderBookStatus: "unavailable",
            orderBookNote: "No real order book source configured.",
            rawPayload: row,
          },
        });
        imported += 1;
      }

      await prisma.rawDataSnapshot.create({
        data: {
          provider: "manual-csv",
          endpoint: "/api/import/candles",
          status: errors.length ? "degraded" : "available",
          payload: { attempted: rows.length, imported, errors },
          error: errors.length ? errors.join("; ") : null,
        },
      });

      res.status(errors.length ? 207 : 201).json({ status: errors.length ? "degraded" : "available", imported, errors });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Invalid import payload";
      await prisma.rawDataSnapshot.create({ data: { provider: "manual-csv", endpoint: "/api/import/candles", status: "unavailable", error: message } });
      res.status(400).json({ status: "unavailable", reason: message });
    }
  });

  router.get("/watchlist", async (_req, res) => {
    const items = await prisma.watchlistItem.findMany({ include: { symbol: true }, orderBy: { createdAt: "desc" } });
    res.json(items);
  });

  router.post("/watchlist", async (req, res) => {
    const body = z.object({ symbol: symbolSchema, notes: z.string().optional(), alertEnabled: z.boolean().optional() }).safeParse({ ...req.body, symbol: String(req.body?.symbol ?? "").toUpperCase() });
    if (!body.success) return res.status(400).json({ error: "Invalid watchlist payload" });
    const symbol = await prisma.egxSymbol.findUnique({ where: { symbolCode: body.data.symbol } });
    if (!symbol) return res.status(404).json({ error: "Symbol not found" });
    const item = await prisma.watchlistItem.upsert({
      where: { symbolCode: body.data.symbol },
      update: { notes: body.data.notes, alertEnabled: body.data.alertEnabled ?? true },
      create: { symbolCode: body.data.symbol, notes: body.data.notes, alertEnabled: body.data.alertEnabled ?? true },
    });
    res.status(201).json(item);
  });

  router.delete("/watchlist/:symbol", async (req, res) => {
    const symbol = symbolSchema.safeParse(req.params.symbol.toUpperCase());
    if (!symbol.success) return res.status(400).json({ error: "Invalid symbol" });
    await prisma.watchlistItem.deleteMany({ where: { symbolCode: symbol.data } });
    res.status(204).end();
  });

  router.get("/data-status", async (_req, res) => {
    const [
      providers,
      totalSymbols,
      symbolsWithCandles,
      symbolsWithRealCandles,
      symbolsWithPartialCandles,
      latestCandle,
      realBidAskSnapshots,
      scanner,
    ] = await Promise.all([
      prisma.providerStatus.findMany(),
      prisma.egxSymbol.count({ where: { isActive: true } }),
      prisma.candle.groupBy({ by: ["symbolCode"], where: { source: config.MARKET_DATA_PROVIDER }, _count: true }),
      prisma.candle.groupBy({ by: ["symbolCode"], where: { source: config.MARKET_DATA_PROVIDER, quality: "real" }, _count: true }),
      prisma.candle.groupBy({ by: ["symbolCode"], where: { source: config.MARKET_DATA_PROVIDER, quality: "partial" }, _count: true }),
      prisma.candle.findFirst({ where: { source: config.MARKET_DATA_PROVIDER }, orderBy: { candleTime: "desc" } }),
      prisma.quoteSnapshot.count({ where: { source: config.MARKET_DATA_PROVIDER, orderBookStatus: "real" } }),
      marketData.getScanner(),
    ]);
    const visibleProviders = providers.filter((provider) => provider.provider !== "manual-csv" || config.MARKET_DATA_PROVIDER === "manual-csv");
    const scannerRows = scanner.data ?? [];
    const latestScannerAt = scannerRows.reduce<string | null>((latest, row) => {
      if (!row.capturedAt) return latest;
      return !latest || row.capturedAt > latest ? row.capturedAt : latest;
    }, null);
    res.json({
      providers: visibleProviders,
      activeProvider: config.MARKET_DATA_PROVIDER,
      totalSymbols,
      scannerStatus: scanner.status,
      scannerReason: scanner.reason,
      currentScannerRows: scannerRows.length,
      symbolsWithCurrentPrices: scannerRows.filter((row) => typeof row.price === "number").length,
      symbolsWithProviderData: scannerRows.filter((row) => row.dataQuality !== "unavailable").length,
      symbolsWithoutProviderData: scannerRows.filter((row) => row.dataQuality === "unavailable").length,
      symbolsWithStrategyAnalysis: scannerRows.filter((row) => row.analysis).length,
      symbolsWithCandles: symbolsWithCandles.length,
      symbolsWithRealCandles: symbolsWithRealCandles.length,
      symbolsWithPartialCandles: symbolsWithPartialCandles.length,
      latestCandleAt: latestCandle?.candleTime ?? null,
      latestCompletedCandleAt: latestCandle?.candleTime ?? null,
      latestScannerAt,
      latestDataRefreshAt: latestCandle?.importedAt ?? null,
      realBidAskSnapshots,
      bidAskStatus: realBidAskSnapshots > 0 ? "real" : "unavailable",
      autoRefreshEnabled: config.AUTO_REFRESH_ENABLED,
      autoRefreshIntervalMs: Math.max(120000, config.AUTO_REFRESH_INTERVAL_MS),
    });
  });

  return router;
}
