import type { BestStock, ImportedCandle, Settings, Signal, SmartEarlyAlert, Stock, Timeframe, TimeframeAnalysis, VolumeDirectionAlert, WatchlistItem } from "../types";
import { analyzeCandles } from "../lib/analysis";
import { egxStocks } from "./egxUniverse";

export const timeframes: Timeframe[] = ["15M", "30M", "1H", "4H", "1D"];

export const stocks: Stock[] = egxStocks;

const bases: Record<string, number> = {
  COMI: 82.5,
  EGTS: 18.1,
  FWRY: 9.3,
  HRHO: 23.5,
  TMGH: 58,
  SWDY: 45.4,
  ABUK: 61.7,
  EAST: 27.8,
  ETEL: 39.2,
  ORAS: 278,
  EKHO: 31.6,
  CIRA: 14.8,
  CLHO: 7.4,
  JUFO: 19.5,
  MASR: 5.2,
  MNHD: 6.5,
  AUTO: 7.9,
  SKPC: 29.7,
  MFPC: 89.5,
  ESRS: 94.2,
};

function baseFor(symbol: string, index: number): number {
  if (bases[symbol]) return bases[symbol];
  const seed = [...symbol].reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return Number((4 + (seed % 180) * 0.72 + index * 0.18).toFixed(2));
}

const profile = (index: number) => {
  const group = index % 5;
  return {
    drift: [0.22, 0.11, -0.05, 0.04, 0.16][group],
    volatility: [0.9, 0.55, 0.7, 0.45, 0.75][group],
    pressureBoost: [1.4, 1.18, 0.72, 0.92, 1.28][group],
  };
};

function makeCandleTime(tf: Timeframe, offset: number): string {
  const now = new Date("2026-05-18T14:30:00+03:00");
  const minutes = tf === "15M" ? 15 : tf === "30M" ? 30 : tf === "4H" ? 240 : 1440;
  now.setMinutes(now.getMinutes() - offset * minutes);
  return now.toISOString().slice(0, 16).replace("T", " ");
}

function generateCandles(stock: Stock, tf: Timeframe, stockIndex: number): ImportedCandle[] {
  const count = 230;
  const tfFactor = tf === "15M" ? 0.34 : tf === "30M" ? 0.48 : tf === "4H" ? 0.86 : 1.22;
  const p = profile(stockIndex + timeframes.indexOf(tf));
  let close = baseFor(stock.symbol, stockIndex) * (0.88 + (stockIndex % 23) * 0.006);

  return Array.from({ length: count }, (_, rawIndex) => {
    const i = count - rawIndex;
    const wave = Math.sin((rawIndex + stockIndex) / 7) * p.volatility * tfFactor;
    const move = p.drift * tfFactor + wave * 0.18 + Math.cos(rawIndex / 11) * 0.08;
    const open = close;
    close = Math.max(0.8, close + move);
    const spread = Math.max(close * 0.006, Math.abs(move) * 1.4 + p.volatility * 0.12);
    const bullish = close >= open;
    const high = Math.max(open, close) + spread * (bullish ? 0.62 : 0.34);
    const low = Math.min(open, close) - spread * (bullish ? 0.28 : 0.74);
    const breakoutPulse = rawIndex > count - 10 && (stockIndex + timeframes.indexOf(tf)) % 4 === 0 ? 1.7 : 1;
    const volume = Math.round((180000 + stockIndex * 47000) * tfFactor * p.pressureBoost * breakoutPulse * (1 + Math.abs(Math.sin(rawIndex / 5)) * 0.35));

    return {
      id: `${stock.symbol}-${tf}-${rawIndex}`,
      symbol: stock.symbol,
      timeframe: tf,
      candleTime: makeCandleTime(tf, i),
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close: Number(close.toFixed(2)),
      volume,
      source: "sample",
      importedAt: "2026-05-18 14:35",
    };
  });
}

export function buildSampleCandles(): ImportedCandle[] {
  return stocks.flatMap((stock, stockIndex) => timeframes.flatMap((tf) => generateCandles(stock, tf, stockIndex)));
}

const sampleCandles: ImportedCandle[] = [];

const candleKey = (symbol: string, timeframe: Timeframe) => `${symbol}-${timeframe}`;
const sampleCandlesByKey = new Map<string, ImportedCandle[]>();
for (const candle of sampleCandles) {
  const key = candleKey(candle.symbol, candle.timeframe);
  sampleCandlesByKey.set(key, [...(sampleCandlesByKey.get(key) ?? []), candle]);
}

const actionWeight: Record<string, number> = {
  "BREAKOUT BUY": 6,
  "BUY NOW": 5,
  "PULLBACK BUY AREA": 4,
  "WATCH EARLY BUY": 3,
  HOLD: 2,
  "WAIT PULLBACK": 2,
  WAIT: 0,
  "REDUCE / TAKE PROFIT": -1,
  "DO NOT BUY NOW": -3,
  "SELL NOW": -5,
};

const backendActionMap: Record<NonNullable<BackendScannerRow["recommendation"]>, BestStock["bestAction"]> = {
  BUY: "WATCH EARLY BUY",
  WATCH: "WAIT",
  SELL: "DO NOT BUY NOW",
  AVOID: "DO NOT BUY NOW",
};

function egyptTimestamp() {
  return new Date().toLocaleString("en-GB", { timeZone: "Africa/Cairo", hour12: false }).replace(",", "");
}

function normalizeBackendStocks(backendSymbols?: BackendSymbol[]): Stock[] {
  if (!backendSymbols?.length) return stocks;
  return backendSymbols.map((symbol) => ({
    id: symbol.symbolCode,
    symbol: symbol.symbolCode,
    companyName: symbol.companyNameEn,
    sector: symbol.sector ?? "EGX Listed",
    market: "EGX",
    isActive: symbol.isActive,
    notes: symbol.isPlaceholder ? "Placeholder row pending official enrichment." : "",
  }));
}

function unavailableRow(stock: Stock, index: number, reason?: string): BestStock {
  return {
    id: `unavailable-${stock.symbol}`,
    rank: index + 1,
    symbol: stock.symbol,
    companyName: stock.companyName,
    bestAction: "WAIT",
    bestFrame: "1D",
    overallScore: 0,
    plan: "WAIT",
    entry: 0,
    target: 0,
    stop: 0,
    riskReward: 0,
    pressure: "Neutral",
    volumeStatus: "Weak",
    reason: reason ?? "No real quote or OHLCV candle data is stored for this symbol yet.",
    lastUpdateEgypt: "Unavailable",
    sector: stock.sector,
    orderBookStatus: "unavailable",
    orderBookNote: "No real bid/ask source returned data for this symbol.",
    bidAskExpectation: "Real bid/ask is unavailable, so no bid/ask-based expectation is shown.",
    dataQuality: "unavailable",
  };
}

function scannerToBestRow(row: BackendScannerRow, stock: Stock, index: number): BestStock {
  const analysis = row.analysis;
  const score = analysis?.score ?? (row.confidence ? Math.min(10, Number((row.confidence / 10).toFixed(1))) : 0);
  return {
    id: `backend-${row.symbol}`,
    rank: index + 1,
    symbol: row.symbol,
    companyName: row.companyName || stock.companyName,
    bestAction: analysis?.actionNow ?? (analysis || row.recommendation === "BUY" ? backendActionMap[row.recommendation ?? "WATCH"] : "WAIT"),
    bestFrame: analysis?.timeframe ?? "1D",
    overallScore: score,
    plan: analysis?.plan ?? "WAIT",
    entry: analysis?.suggestedEntry ?? 0,
    target: analysis?.suggestedTarget ?? 0,
    stop: analysis?.suggestedStop ?? 0,
    riskReward: analysis?.riskReward ?? 0,
    pressure: analysis?.pressure ?? "Neutral",
    volumeStatus: analysis?.volumeStatus ?? (row.volume ? "Normal" : "Weak"),
    reason: row.reason ?? (analysis ? "Backend provider analysis loaded." : "Backend provider quote loaded, but no validated technical analysis is available yet."),
    lastUpdateEgypt: analysis?.lastUpdateEgypt ?? row.capturedAt ?? "Provider timestamp unavailable",
    currentPrice: row.price,
    changePercent: row.changePercent,
    volume: row.volume,
    providerUpdatedAt: row.capturedAt,
    sector: row.sector ?? stock.sector,
    bid: row.bid,
    ask: row.ask,
    spreadPercent: row.spreadPercent,
    orderBookStatus: row.orderBookStatus,
    orderBookNote: row.orderBookNote,
    bidAskExpectation: row.bidAskExpectation,
    dataQuality: row.dataQuality,
  };
}

export interface MarketDataSnapshot {
  stocks: Stock[];
  importedCandles: ImportedCandle[];
  timeframeAnalyses: TimeframeAnalysis[];
  bestStocks: BestStock[];
  screenerRows: BestStock[];
  watchlist: WatchlistItem[];
  volumeAlerts: VolumeDirectionAlert[];
  smartAlerts: SmartEarlyAlert[];
  signals: Signal[];
  settings: Settings;
  liveCandleCount: number;
  sourceLabel: string;
  realCoverageCount: number;
  backendStatus?: {
    providers?: Array<{
      provider: string;
      status: string;
      reason?: string | null;
      checkedAt?: string;
    }>;
    activeProvider?: string;
    totalSymbols: number;
    scannerStatus?: string;
    scannerReason?: string | null;
    currentScannerRows?: number;
    symbolsWithCurrentPrices?: number;
    symbolsWithProviderData?: number;
    symbolsWithoutProviderData?: number;
    symbolsWithStrategyAnalysis?: number;
    symbolsWithCandles?: number;
    symbolsWithRealCandles: number;
    symbolsWithPartialCandles?: number;
    latestCandleAt: string | null;
    latestCompletedCandleAt?: string | null;
    latestScannerAt?: string | null;
    latestDataRefreshAt?: string | null;
    realBidAskSnapshots?: number;
    bidAskStatus?: "real" | "unavailable";
    autoRefreshEnabled?: boolean;
    autoRefreshIntervalMs?: number;
  };
  moverContext?: {
    latestCompletedDate: string | null;
    currentSessionDate: string;
    latestSessionRows: number;
    staleMoverCount: number;
    isTodaySession: boolean;
    warning: string;
  };
}

export interface BackendSymbol {
  symbolCode: string;
  tradingviewSymbol: string;
  companyNameEn: string;
  companyNameAr: string | null;
  sector: string | null;
  industry: string | null;
  isActive: boolean;
  isPlaceholder: boolean;
}

export interface BackendScannerRow {
  symbol: string;
  companyName: string;
  sector?: string | null;
  price?: number;
  changePercent?: number;
  volume?: number;
  capturedAt?: string;
  marketCap?: number;
  bid?: number;
  ask?: number;
  spreadPercent?: number;
  orderBookStatus?: "real" | "estimated" | "unavailable";
  orderBookNote?: string;
  bidAskExpectation?: string;
  recommendation?: "BUY" | "WATCH" | "SELL" | "AVOID";
  confidence?: number;
  dataQuality: "real" | "partial" | "unavailable";
  reason?: string;
  analysis?: TimeframeAnalysis;
}

export interface BackendDataStatus {
  providers?: Array<{
    provider: string;
    status: string;
    reason?: string | null;
    checkedAt?: string;
  }>;
  activeProvider?: string;
  totalSymbols: number;
  scannerStatus?: string;
  scannerReason?: string | null;
  currentScannerRows?: number;
  symbolsWithCurrentPrices?: number;
  symbolsWithProviderData?: number;
  symbolsWithoutProviderData?: number;
  symbolsWithStrategyAnalysis?: number;
  symbolsWithCandles?: number;
  symbolsWithRealCandles: number;
  symbolsWithPartialCandles?: number;
  latestCandleAt: string | null;
  latestCompletedCandleAt?: string | null;
  latestScannerAt?: string | null;
  latestDataRefreshAt?: string | null;
  realBidAskSnapshots?: number;
  bidAskStatus?: "real" | "unavailable";
  autoRefreshEnabled?: boolean;
  autoRefreshIntervalMs?: number;
}

function buildGroupedCandles(liveCandles: ImportedCandle[]): Map<string, ImportedCandle[]> {
  if (!liveCandles.length) return sampleCandlesByKey;
  const grouped = new Map(sampleCandlesByKey);
  const clonedKeys = new Set<string>();

  for (const candle of liveCandles) {
    if (!timeframes.includes(candle.timeframe)) continue;
    const normalized = { ...candle, symbol: candle.symbol.replace(/^EGX:/i, "").toUpperCase() };
    const key = candleKey(normalized.symbol, normalized.timeframe);
    if (!clonedKeys.has(key)) {
      grouped.set(key, [...(grouped.get(key) ?? [])]);
      clonedKeys.add(key);
    }
    const rows = grouped.get(key)!;
    const existingIndex = rows.findIndex((row) => row.candleTime === normalized.candleTime);
    if (existingIndex >= 0) rows[existingIndex] = normalized;
    else rows.push(normalized);
  }

  return grouped;
}

function buildLiveGroupedCandles(liveCandles: ImportedCandle[]): Map<string, ImportedCandle[]> {
  const grouped = new Map<string, ImportedCandle[]>();
  for (const candle of liveCandles) {
    if (!timeframes.includes(candle.timeframe)) continue;
    const normalized = { ...candle, symbol: candle.symbol.replace(/^EGX:/i, "").toUpperCase() };
    const key = candleKey(normalized.symbol, normalized.timeframe);
    grouped.set(key, [...(grouped.get(key) ?? []), normalized]);
  }
  return grouped;
}

function formatDateEgypt(value?: string | null) {
  if (!value) return "Unavailable";
  return new Date(value).toLocaleDateString("en-GB", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit" });
}

function providerFreshnessNote(backendStatus?: BackendDataStatus) {
  const latest = backendStatus?.latestCompletedCandleAt ?? backendStatus?.latestCandleAt;
  if (!latest) return "No provider candle timestamp is available.";
  const latestDate = formatDateEgypt(latest);
  const today = formatDateEgypt(new Date().toISOString());
  if (latestDate !== today) return `Based on latest completed candle ${latestDate}; no usable ${today} session candle is available from the active provider.`;
  return `Based on latest completed candle ${latestDate}.`;
}

function isLatestCompletedSession(value?: string | null, backendStatus?: BackendDataStatus) {
  const latest = backendStatus?.latestCompletedCandleAt ?? backendStatus?.latestCandleAt;
  if (!value || !latest) return false;
  return formatDateEgypt(value) === formatDateEgypt(latest);
}

function severityFor(score: number): SmartEarlyAlert["severity"] {
  if (score >= 82) return "High";
  if (score >= 66) return "Medium";
  return "Low";
}

function buildSmartEarlyAlerts(
  analyses: TimeframeAnalysis[],
  stockUniverse: Stock[],
  backendScannerBySymbol: Map<string, BackendScannerRow>,
  backendStatus?: BackendDataStatus,
): SmartEarlyAlert[] {
  const stockNameBySymbol = new Map(stockUniverse.map((stock) => [stock.symbol, stock.companyName]));
  const freshness = providerFreshnessNote(backendStatus);
  const alerts: SmartEarlyAlert[] = [];

  for (const row of analyses) {
    const scanner = backendScannerBySymbol.get(row.symbol);
    const price = row.currentPrice;
    const aboveZonePct = price > row.buyZoneHigh ? ((price - row.buyZoneHigh) / Math.max(price, 0.0001)) * 100 : 0;
    const belowZonePct = price < row.buyZoneLow ? ((row.buyZoneLow - price) / Math.max(price, 0.0001)) * 100 : 0;
    const nearZone = aboveZonePct <= 2.2 && belowZonePct <= 1.6;
    const strongVolume = row.volumeStatus === "Strong" || row.volumeStatus === "Very Strong";
    const bullishTrend = row.mainTrend.includes("BULLISH");
    const hasData = scanner?.dataQuality !== "unavailable";

    if (!hasData) continue;

    const addAlert = (alert: Omit<SmartEarlyAlert, "id" | "companyName" | "dataFreshness" | "createdAtEgypt">) => {
      alerts.push({
        ...alert,
        id: `smart-${alert.symbol}-${alert.timeframe}-${alert.alertType}`.replace(/\s+/g, "-").toLowerCase(),
        companyName: stockNameBySymbol.get(alert.symbol) ?? alert.symbol,
        dataFreshness: freshness,
        createdAtEgypt: row.lastUpdateEgypt,
      });
    };

    if (row.breakoutStatus && strongVolume && row.pressure === "Buy Pressure") {
      const urgencyScore = Math.min(100, Math.round(76 + row.score * 2.4 + row.riskReward * 2));
      addAlert({
        symbol: row.symbol,
        timeframe: row.timeframe,
        alertType: "Breakout Watch",
        side: "Bullish",
        severity: severityFor(urgencyScore),
        urgencyScore,
        action: "BREAKOUT BUY",
        price,
        entryZone: `${row.buyZoneLow} - ${row.buyZoneHigh}`,
        trigger: `Hold above ${row.currentPrice} with ${row.volumeStatus.toLowerCase()} volume`,
        invalidation: `Break below ${row.suggestedStop}`,
        reason: `${row.symbol} is breaking out with ${row.pressure.toLowerCase()}, ${row.volumeStatus.toLowerCase()} volume, and ${row.score}/10 setup score.`,
      });
    }

    if (row.earlyAccumulationStatus && row.pressure === "Buy Pressure" && row.score >= 4) {
      const urgencyScore = Math.min(100, Math.round(58 + row.score * 3 + (strongVolume ? 9 : 0) + (bullishTrend ? 8 : 0) - aboveZonePct * 1.5));
      addAlert({
        symbol: row.symbol,
        timeframe: row.timeframe,
        alertType: "Accumulation Watch",
        side: "Bullish",
        severity: severityFor(urgencyScore),
        urgencyScore,
        action: "WATCH EARLY BUY",
        price,
        entryZone: `${row.buyZoneLow} - ${row.buyZoneHigh}`,
        trigger: `Close stays above ${row.fastRangeFilter} and pressure remains buy-side`,
        invalidation: `Lose ${row.suggestedStop} or flip to sell pressure`,
        reason: `${row.symbol} shows early accumulation: buy pressure, ${row.volumeStatus.toLowerCase()} volume, and ${row.mainTrend.toLowerCase()} trend.`,
      });
    }

    if (nearZone && bullishTrend && row.pressure !== "Sell Pressure" && row.score >= 5) {
      const urgencyScore = Math.min(100, Math.round(62 + row.score * 2.5 + (row.pressure === "Buy Pressure" ? 8 : 0) + Math.min(row.riskReward * 4, 10)));
      addAlert({
        symbol: row.symbol,
        timeframe: row.timeframe,
        alertType: "Pullback Near Buy Zone",
        side: "Bullish",
        severity: severityFor(urgencyScore),
        urgencyScore,
        action: row.pressure === "Buy Pressure" ? "PULLBACK BUY AREA" : "WAIT PULLBACK",
        price,
        entryZone: `${row.buyZoneLow} - ${row.buyZoneHigh}`,
        trigger: `Reaction inside ${row.buyZoneLow} - ${row.buyZoneHigh}`,
        invalidation: `Daily close below ${row.suggestedStop}`,
        reason: `${row.symbol} is close to the ATR buy zone with ${row.mainTrend.toLowerCase()} trend and ${row.score}/10 score.`,
      });
    }

    if (strongVolume && row.pressure !== "Neutral") {
      const bearish = row.pressure === "Sell Pressure";
      const urgencyScore = Math.min(100, Math.round(54 + row.score * 2 + (row.volumeStatus === "Very Strong" ? 14 : 8) + (bearish ? 10 : 4)));
      addAlert({
        symbol: row.symbol,
        timeframe: row.timeframe,
        alertType: bearish ? "Distribution Risk" : "Volume Spike",
        side: bearish ? "Bearish" : "Bullish",
        severity: severityFor(urgencyScore),
        urgencyScore,
        action: bearish ? "DO NOT BUY NOW" : "WATCH EARLY BUY",
        price,
        entryZone: `${row.buyZoneLow} - ${row.buyZoneHigh}`,
        trigger: bearish ? `Wait until sell pressure cools below ${row.currentPrice}` : `Confirm follow-through above ${row.currentPrice}`,
        invalidation: bearish ? `Do not chase while pressure remains sell-side` : `Reject below ${row.fastRangeFilter}`,
        reason: `${row.symbol} has ${row.pressure.toLowerCase()} with ${row.volumeStatus.toLowerCase()} volume, which deserves early attention.`,
      });
    }
  }

  const unique = new Map<string, SmartEarlyAlert>();
  for (const alert of alerts.sort((a, b) => b.urgencyScore - a.urgencyScore)) {
    const existing = unique.get(alert.symbol);
    if (!existing || alert.urgencyScore > existing.urgencyScore) unique.set(alert.symbol, alert);
  }
  return [...unique.values()].sort((a, b) => b.urgencyScore - a.urgencyScore).slice(0, 40);
}

export const settings: Settings = {
  id: "settings-1",
  dataSourceType: "Public delayed API",
  apiEndpoint: "",
  defaultMode: "Balanced",
  defaultRisk: "1.0% per trade",
  egyptTimezone: "Africa/Cairo",
  minScore: 6,
};

export function buildMarketData(
  liveCandles: ImportedCandle[] = [],
  liveSignals: Signal[] = [],
  realOnly = true,
  backendSymbols?: BackendSymbol[],
  backendScanner: BackendScannerRow[] = [],
  backendStatus?: BackendDataStatus,
): MarketDataSnapshot {
  const stockUniverse = normalizeBackendStocks(backendSymbols);
  const usableLiveCandles = liveCandles.filter((candle) => timeframes.includes(candle.timeframe));
  const groupedCandles = realOnly ? buildLiveGroupedCandles(usableLiveCandles) : buildGroupedCandles(usableLiveCandles);
  const importedCandles = realOnly ? usableLiveCandles : usableLiveCandles.length ? [...sampleCandles, ...usableLiveCandles] : sampleCandles;
  const liveTimeframeAnalyses = stockUniverse.flatMap((stock) =>
    timeframes.flatMap((tf) => {
      const candles = groupedCandles.get(candleKey(stock.symbol, tf)) ?? [];
      return candles.length ? [analyzeCandles(stock.symbol, tf, candles)] : [];
    }),
  );
  const liveAnalysisKeys = new Set(liveTimeframeAnalyses.map((row) => `${row.symbol}-${row.timeframe}`));
  const backendScannerBySymbol = new Map(backendScanner.map((row) => [row.symbol, row]));
  const backendTimeframeAnalyses = backendScanner
    .map((row) => row.analysis)
    .filter((row): row is TimeframeAnalysis => {
      if (!row) return false;
      return timeframes.includes(row.timeframe) && !liveAnalysisKeys.has(`${row.symbol}-${row.timeframe}`);
    });
  const timeframeAnalyses = [...liveTimeframeAnalyses, ...backendTimeframeAnalyses];
  const analysisBestStocks: BestStock[] = stockUniverse
    .filter((stock) => timeframeAnalyses.some((row) => row.symbol === stock.symbol))
    .map((stock) => {
      const rows = timeframeAnalyses.filter((row) => row.symbol === stock.symbol);
      const backendRow = backendScannerBySymbol.get(stock.symbol);
      const best = [...rows].sort((a, b) => b.score + actionWeight[b.actionNow] + b.riskReward - (a.score + actionWeight[a.actionNow] + a.riskReward))[0];
      const aligned = rows.filter((row) => row.mainTrend.includes("BULLISH")).length;
      const overallScore = Math.min(10, Number(((rows.reduce((sum, row) => sum + row.score, 0) / rows.length) + aligned * 0.55 + best.riskReward * 0.25).toFixed(1)));
      return {
        id: `best-${stock.symbol}`,
        rank: 0,
        symbol: stock.symbol,
        companyName: stock.companyName,
        bestAction: best.actionNow,
        bestFrame: best.timeframe,
        overallScore,
        plan: best.plan,
        entry: best.suggestedEntry,
        target: best.suggestedTarget,
        stop: best.suggestedStop,
        riskReward: best.riskReward,
        pressure: best.pressure,
        volumeStatus: best.volumeStatus,
        reason: `${rows.length}/${timeframes.length} priced frames received, ${best.pressure.toLowerCase()}, ${best.volumeStatus.toLowerCase()} volume, ${best.timeframe} score ${best.score}/10.`,
        lastUpdateEgypt: best.lastUpdateEgypt,
        currentPrice: best.currentPrice,
        changePercent: backendRow?.changePercent,
        volume: backendRow?.volume,
        providerUpdatedAt: backendRow?.capturedAt,
        sector: backendRow?.sector ?? stock.sector,
        bid: backendRow?.bid,
        ask: backendRow?.ask,
        spreadPercent: backendRow?.spreadPercent,
        orderBookStatus: backendRow?.orderBookStatus,
        orderBookNote: backendRow?.orderBookNote,
        bidAskExpectation: backendRow?.bidAskExpectation,
        dataQuality: backendRow?.dataQuality ?? (rows.length >= 4 ? "real" as const : "partial" as const),
      };
    })
    .sort((a, b) => b.overallScore + actionWeight[b.bestAction] - (a.overallScore + actionWeight[a.bestAction]));

  const analysisSymbols = new Set(analysisBestStocks.map((row) => row.symbol));
  const backendBestStocks = backendScanner
    .filter((row) => row.dataQuality !== "unavailable" && Boolean(row.analysis) && !analysisSymbols.has(row.symbol))
    .map((row, index) => {
      const stock = stockUniverse.find((item) => item.symbol === row.symbol) ?? {
        id: row.symbol,
        symbol: row.symbol,
        companyName: row.companyName,
        sector: row.sector ?? "EGX Listed",
        market: "EGX",
        isActive: true,
        notes: "",
      };
      return scannerToBestRow(row, stock, index);
    });

  const bestStocks: BestStock[] = [...analysisBestStocks, ...backendBestStocks]
    .sort((a, b) => b.overallScore + actionWeight[b.bestAction] - (a.overallScore + actionWeight[a.bestAction]))
    .map((row, index) => ({ ...row, rank: index + 1 }));

  const bestBySymbol = new Map(bestStocks.map((row) => [row.symbol, row]));
  const screenerRows = stockUniverse.map((stock, index) => {
    const ranked = bestBySymbol.get(stock.symbol);
    if (ranked) return { ...ranked, rank: index + 1 };
    const scanner = backendScannerBySymbol.get(stock.symbol);
    if (scanner && scanner.dataQuality !== "unavailable") return scannerToBestRow(scanner, stock, index);
    return unavailableRow(stock, index, scanner?.reason);
  });
  const latestCompleted = backendStatus?.latestCompletedCandleAt ?? backendStatus?.latestCandleAt;
  const latestCompletedDate = latestCompleted ? formatDateEgypt(latestCompleted) : null;
  const currentSessionDate = formatDateEgypt(new Date().toISOString());
  const latestSessionRows = screenerRows.filter((row) => row.dataQuality !== "unavailable" && isLatestCompletedSession(row.lastUpdateEgypt, backendStatus)).length;
  const staleMoverCount = screenerRows.filter((row) => row.changePercent !== undefined && row.dataQuality !== "unavailable" && !isLatestCompletedSession(row.lastUpdateEgypt, backendStatus)).length;

  const watchlist: WatchlistItem[] = bestStocks.slice(0, 7).map((row, index) => ({
    id: `watch-${row.symbol}`,
    symbol: row.symbol,
    companyName: row.companyName,
    userNotes: index % 2 === 0 ? "Track pullback into ATR buy zone." : "Watch volume confirmation before entry.",
    alertEnabled: index < 5,
  }));

  const stockNameBySymbol = new Map(stockUniverse.map((stock) => [stock.symbol, stock.companyName]));
  const volumeAlerts: VolumeDirectionAlert[] = timeframeAnalyses
    .filter((row) => row.volumeStatus === "Strong" || row.volumeStatus === "Very Strong" || row.pressure !== "Neutral")
    .map((row) => {
      const direction: VolumeDirectionAlert["direction"] = row.pressure === "Buy Pressure" ? "Accumulation" : row.pressure === "Sell Pressure" ? "Distribution" : "Neutral";
      const severity: VolumeDirectionAlert["severity"] = row.volumeStatus === "Very Strong" ? "High" : row.volumeStatus === "Strong" ? "Medium" : "Low";
      return {
        id: `volume-${row.symbol}-${row.timeframe}`,
        symbol: row.symbol,
        companyName: stockNameBySymbol.get(row.symbol) ?? row.symbol,
        timeframe: row.timeframe,
        direction,
        severity,
        pressure: row.pressure,
        volumeStatus: row.volumeStatus,
        score: row.score,
        message: `${direction} read: ${row.pressure.toLowerCase()} with ${row.volumeStatus.toLowerCase()} volume and score ${row.score}/10.`,
      };
    })
    .sort((a, b) => {
      const severityWeight: Record<VolumeDirectionAlert["severity"], number> = { High: 3, Medium: 2, Low: 1 };
      const directionWeight: Record<VolumeDirectionAlert["direction"], number> = { Accumulation: 2, Distribution: 2, Neutral: 0 };
      return severityWeight[b.severity] + directionWeight[b.direction] + b.score / 10 - (severityWeight[a.severity] + directionWeight[a.direction] + a.score / 10);
    });

  const smartAlerts = buildSmartEarlyAlerts(timeframeAnalyses, stockUniverse, backendScannerBySymbol, backendStatus);

  const generatedSignals: Signal[] = timeframeAnalyses
    .filter((row) => ["BUY NOW", "BREAKOUT BUY", "PULLBACK BUY AREA", "WATCH EARLY BUY", "SELL NOW", "DO NOT BUY NOW"].includes(row.actionNow))
    .slice(0, 38)
    .map((row, index) => ({
      id: `signal-${index + 1}`,
      symbol: row.symbol,
      timeframe: row.timeframe,
      signalType: row.breakoutStatus ? "Breakout" : row.pullbackStatus ? "Pullback" : row.pressure,
      action: row.actionNow,
      price: row.currentPrice,
      score: row.score,
      message: `${row.symbol} ${row.timeframe}: ${row.actionNow} with ${row.pressure.toLowerCase()} and ${row.volumeStatus.toLowerCase()} volume.`,
      createdAtEgypt: row.lastUpdateEgypt,
    }));
  const volumeSignals: Signal[] = volumeAlerts.slice(0, 28).map((alert, index) => {
    const related = timeframeAnalyses.find((row) => row.symbol === alert.symbol && row.timeframe === alert.timeframe);
    return {
      id: `volume-signal-${index + 1}`,
      symbol: alert.symbol,
      timeframe: alert.timeframe,
      signalType: `Volume ${alert.direction}`,
      action: related?.actionNow ?? "WAIT",
      price: related?.currentPrice ?? 0,
      score: alert.score,
      message: alert.message,
      createdAtEgypt: related?.lastUpdateEgypt ?? egyptTimestamp(),
    };
  });
  const smartAlertSignals: Signal[] = smartAlerts.slice(0, 30).map((alert, index) => ({
    id: `smart-alert-signal-${index + 1}`,
    symbol: alert.symbol,
    timeframe: alert.timeframe,
    signalType: `Smart ${alert.alertType}`,
    action: alert.action,
    price: alert.price,
    score: Math.round(alert.urgencyScore / 10),
    message: `${alert.severity} urgency ${alert.urgencyScore}/100. ${alert.reason} ${alert.dataFreshness}`,
    createdAtEgypt: alert.createdAtEgypt,
  }));

  return {
    stocks: stockUniverse,
    importedCandles,
    timeframeAnalyses,
    bestStocks,
    screenerRows,
    watchlist,
    volumeAlerts,
    smartAlerts,
    signals: [...smartAlertSignals, ...liveSignals, ...generatedSignals, ...volumeSignals],
    settings: { ...settings, dataSourceType: usableLiveCandles.length ? "TradingView Webhook" : "External API" },
    liveCandleCount: usableLiveCandles.length,
    sourceLabel: backendScanner.some((row) => row.dataQuality !== "unavailable")
      ? usableLiveCandles.length
        ? `${backendStatus?.activeProvider ?? "Backend provider"} + TradingView webhook`
        : backendStatus?.activeProvider === "egx-ai-api"
          ? "Embedded EGX-AI-compatible provider"
          : "Public delayed daily provider"
      : usableLiveCandles.length
        ? "TradingView webhook data only"
        : "Waiting for provider data",
    realCoverageCount: Math.max(
      new Set(usableLiveCandles.map((candle) => candle.symbol.replace(/^EGX:/i, "").toUpperCase())).size,
      backendScanner.filter((row) => row.dataQuality !== "unavailable").length,
    ),
    backendStatus,
    moverContext: {
      latestCompletedDate,
      currentSessionDate,
      latestSessionRows,
      staleMoverCount,
      isTodaySession: Boolean(latestCompletedDate && latestCompletedDate === currentSessionDate),
      warning: latestCompletedDate && latestCompletedDate !== currentSessionDate
        ? `Mover lists are based on latest completed provider candle ${latestCompletedDate}, not today's live session ${currentSessionDate}.`
        : "Mover lists use the latest completed provider candle.",
    },
  };
}

export const defaultMarketData = buildMarketData();
export const importedCandles = defaultMarketData.importedCandles;
export const timeframeAnalyses = defaultMarketData.timeframeAnalyses;
export const bestStocks = defaultMarketData.bestStocks;
export const watchlist = defaultMarketData.watchlist;
export const volumeAlerts = defaultMarketData.volumeAlerts;
export const signals = defaultMarketData.signals;
