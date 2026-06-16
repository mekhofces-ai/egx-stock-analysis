import type { MarketDataProvider } from "./MarketDataProvider.js";
import { unavailable } from "./MarketDataProvider.js";
import { prisma } from "../db.js";
import type { Candle, ProviderResult, Quote, ScannerRow, Timeframe } from "../types.js";
import { recommendation } from "../services/technicalAnalysis.js";

function candleFromDb(row: { symbolCode: string; timeframe: string; candleTime: Date; open: number; high: number; low: number; close: number; volume: number; source: string }): Candle {
  return { symbol: row.symbolCode, timeframe: row.timeframe as Timeframe, time: row.candleTime.toISOString(), open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume, source: row.source };
}

export class ManualCsvProvider implements MarketDataProvider {
  readonly name = "manual-csv";

  async getCandles(symbol: string, timeframe: Timeframe): Promise<ProviderResult<Candle[]>> {
    const rows = await prisma.candle.findMany({ where: { symbolCode: symbol.toUpperCase(), timeframe }, orderBy: { candleTime: "asc" }, take: 500 });
    if (!rows.length) return unavailable(this.name, "No real CSV/API/webhook candles stored for this symbol/timeframe");
    return { status: "available", source: this.name, data: rows.map(candleFromDb) };
  }

  async getQuote(symbol: string): Promise<ProviderResult<Quote>> {
    const quote = await prisma.quoteSnapshot.findFirst({ where: { symbolCode: symbol.toUpperCase() }, orderBy: { capturedAt: "desc" }, include: { symbol: true } });
    if (quote) {
      return {
        status: "available",
        source: this.name,
        data: {
          symbol: quote.symbolCode,
          price: quote.price,
          previousClose: quote.previousClose ?? undefined,
          changePercent: quote.changePercent ?? undefined,
          volume: quote.volume ?? undefined,
          marketCap: quote.marketCap ?? undefined,
          sector: quote.symbol.sector,
          industry: quote.symbol.industry,
          bid: quote.bid ?? undefined,
          ask: quote.ask ?? undefined,
          orderBookStatus: quote.orderBookStatus as Quote["orderBookStatus"],
          orderBookNote: quote.orderBookNote ?? undefined,
          capturedAt: quote.capturedAt.toISOString(),
        },
      };
    }
    const candle = await prisma.candle.findFirst({ where: { symbolCode: symbol.toUpperCase() }, orderBy: { candleTime: "desc" }, include: { symbol: true } });
    if (!candle) return unavailable(this.name, "No real quote or candle data stored for this symbol");
    return {
      status: "available",
      source: this.name,
      data: {
        symbol: candle.symbolCode,
        price: candle.close,
        volume: candle.volume,
        sector: candle.symbol.sector,
        industry: candle.symbol.industry,
        orderBookStatus: "unavailable",
        orderBookNote: "No real order book source configured.",
        capturedAt: candle.candleTime.toISOString(),
      },
    };
  }

  async getScanner(): Promise<ProviderResult<ScannerRow[]>> {
    const symbols = await prisma.egxSymbol.findMany({ where: { isActive: true }, orderBy: { symbolCode: "asc" } });
    const rows: ScannerRow[] = [];
    for (const symbol of symbols) {
      const candles = (await prisma.candle.findMany({ where: { symbolCode: symbol.symbolCode, timeframe: "1D" }, orderBy: { candleTime: "asc" }, take: 260 })).map(candleFromDb);
      const quote = await this.getQuote(symbol.symbolCode);
      if (!candles.length || quote.status !== "available" || !quote.data) {
        rows.push({ symbol: symbol.symbolCode, companyName: symbol.companyNameEn, sector: symbol.sector, dataQuality: "unavailable", reason: "No real candle/quote data stored." });
        continue;
      }
      const rec = recommendation(candles);
      rows.push({ symbol: symbol.symbolCode, companyName: symbol.companyNameEn, sector: symbol.sector, price: quote.data.price, changePercent: quote.data.changePercent, volume: quote.data.volume, marketCap: quote.data.marketCap, recommendation: rec.recommendation, confidence: rec.confidence, dataQuality: candles.length >= 50 ? "real" : "partial", reason: rec.reason });
    }
    return { status: "available", source: this.name, data: rows };
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
