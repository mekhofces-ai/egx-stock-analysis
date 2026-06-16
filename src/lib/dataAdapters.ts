import type { ImportedCandle, Timeframe } from "../types";
import type { Signal } from "../types";
import type { BackendDataStatus, BackendScannerRow, BackendSymbol } from "../data/mockData";

const validTimeframes = new Set(["1M", "5M", "15M", "30M", "1H", "4H", "1D"]);

function appHost() {
  if (typeof window === "undefined") return "localhost";
  return window.location.hostname || "localhost";
}

export function backendBaseUrl() {
  return `http://${appHost()}:8788`;
}

export function webhookBaseUrl() {
  return `http://${appHost()}:8787`;
}

export interface CsvImportResult {
  candles: ImportedCandle[];
  warnings: string[];
}

export interface TradingViewWebhookHealth {
  ok: boolean;
  source: string;
  port: number;
  candles: number;
  signals: number;
  events: number;
  lastEvent: string | null;
}

interface BackendCandleRow {
  symbol: string;
  timeframe: string;
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  source?: string;
}

export interface BackendCandlesResult {
  status?: "available" | "degraded" | "unavailable";
  source?: string;
  reason?: string;
  meta?: {
    providerCheck?: "cache" | "refresh";
    latestReturnedCandleAt?: string | null;
    latestReturnedCandleDateEgypt?: string | null;
    upstreamLatestCandleAt?: string | null;
    upstreamLatestCandleDateEgypt?: string | null;
    upstreamLatestCandleStatus?: "complete" | "incomplete" | "missing" | "not_checked_cache";
    upstreamMissingFields?: string[];
  };
  candles: ImportedCandle[];
}

export interface GoldMarketContext {
  status: "available" | "degraded" | "unavailable";
  source: string;
  reason?: string;
  data?: {
    symbol: string;
    label: string;
    price: number;
    previousClose?: number;
    changePercent?: number;
    currency: string;
    exchange: string;
    lastUpdate: string;
    volume?: number;
  };
}

export function parseCandleCsv(csv: string): CsvImportResult {
  const lines = csv.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const warnings: string[] = [];
  const candles: ImportedCandle[] = [];
  const startIndex = lines[0]?.toLowerCase().startsWith("symbol,timeframe") ? 1 : 0;

  lines.slice(startIndex).forEach((line, index) => {
    const rowNumber = index + startIndex + 1;
    const [symbol, timeframe, time, open, high, low, close, volume] = line.split(",").map((cell) => cell.trim());
    const numeric = [open, high, low, close, volume].map(Number);

    if (!symbol || !timeframe || !time || numeric.some((value) => Number.isNaN(value))) {
      warnings.push(`Row ${rowNumber}: missing or invalid OHLCV values.`);
      return;
    }

    const normalizedTimeframe = timeframe.toUpperCase();
    if (!validTimeframes.has(normalizedTimeframe)) {
      warnings.push(`Row ${rowNumber}: unsupported timeframe ${timeframe}.`);
      return;
    }

    candles.push({
      id: `csv-${symbol}-${timeframe}-${time}-${rowNumber}`,
      symbol: symbol.toUpperCase(),
      timeframe: normalizedTimeframe as Timeframe,
      candleTime: time,
      open: numeric[0],
      high: numeric[1],
      low: numeric[2],
      close: numeric[3],
      volume: numeric[4],
      source: "csv",
      importedAt: new Date().toISOString(),
    });
  });

  return { candles, warnings };
}

export async function fetchExternalCandles(endpoint: string): Promise<ImportedCandle[]> {
  const response = await fetch(endpoint, { cache: "no-store" });
  if (!response.ok) throw new Error(`Provider returned ${response.status}`);
  return response.json() as Promise<ImportedCandle[]>;
}

export async function fetchTradingViewWebhookHealth(endpoint = `${webhookBaseUrl()}/api/health`): Promise<TradingViewWebhookHealth> {
  const response = await fetch(endpoint, { cache: "no-store" });
  if (!response.ok) throw new Error(`Webhook server returned ${response.status}`);
  return response.json() as Promise<TradingViewWebhookHealth>;
}

export async function fetchTradingViewCandles(endpoint = `${webhookBaseUrl()}/api/tradingview/candles`): Promise<ImportedCandle[]> {
  const response = await fetch(endpoint, { cache: "no-store" });
  if (!response.ok) throw new Error(`Webhook candle endpoint returned ${response.status}`);
  return response.json() as Promise<ImportedCandle[]>;
}

export async function fetchTradingViewSignals(endpoint = `${webhookBaseUrl()}/api/tradingview/signals`): Promise<Signal[]> {
  const response = await fetch(endpoint, { cache: "no-store" });
  if (!response.ok) throw new Error(`Webhook signal endpoint returned ${response.status}`);
  return response.json() as Promise<Signal[]>;
}

export async function fetchBackendSymbols(endpoint = `${backendBaseUrl()}/api/symbols`): Promise<BackendSymbol[]> {
  const response = await fetch(endpoint, { cache: "no-store" });
  if (!response.ok) throw new Error(`Backend symbols endpoint returned ${response.status}`);
  return response.json() as Promise<BackendSymbol[]>;
}

export async function fetchBackendScanner(endpoint = `${backendBaseUrl()}/api/market/scanner`): Promise<BackendScannerRow[]> {
  const response = await fetch(endpoint, { cache: "no-store" });
  if (!response.ok) throw new Error(`Backend scanner endpoint returned ${response.status}`);
  const payload = await response.json() as { data?: BackendScannerRow[]; rows?: BackendScannerRow[] } | BackendScannerRow[];
  if (Array.isArray(payload)) return payload;
  return payload.data ?? payload.rows ?? [];
}

export async function refreshBackendMarket(endpoint = `${backendBaseUrl()}/api/market/refresh`): Promise<{ data?: BackendScannerRow[]; summary?: { total: number; available: number; priced?: number; unavailable: number; latestCompletedCandleAt?: string; unavailableSymbols?: string[] }; refreshedAt?: string; reason?: string }> {
  const response = await fetch(endpoint, { method: "POST", cache: "no-store" });
  if (!response.ok) throw new Error(`Backend refresh endpoint returned ${response.status}`);
  return response.json() as Promise<{ data?: BackendScannerRow[]; summary?: { total: number; available: number; priced?: number; unavailable: number; latestCompletedCandleAt?: string; unavailableSymbols?: string[] }; refreshedAt?: string; reason?: string }>;
}

export async function fetchBackendCandles(symbol: string, timeframe = "1D", endpointBase = `${backendBaseUrl()}/api/market/candles`): Promise<ImportedCandle[]> {
  const result = await fetchBackendCandlesResult(symbol, timeframe, endpointBase);
  return result.candles;
}

export async function fetchBackendCandlesResult(symbol: string, timeframe = "1D", endpointBase = `${backendBaseUrl()}/api/market/candles`): Promise<BackendCandlesResult> {
  const cleanSymbol = symbol.replace(/^EGX:/i, "").toUpperCase();
  const response = await fetch(`${endpointBase}/${encodeURIComponent(cleanSymbol)}?timeframe=${encodeURIComponent(timeframe)}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Backend candles endpoint returned ${response.status}`);
  const payload = await response.json() as {
    status?: BackendCandlesResult["status"];
    source?: string;
    reason?: string;
    meta?: BackendCandlesResult["meta"];
    data?: BackendCandleRow[];
  };
  const candles = (payload.data ?? []).map((row, index) => ({
      id: `${cleanSymbol}-${timeframe}-${row.time}-${index}`,
      symbol: cleanSymbol,
      timeframe: row.timeframe.toUpperCase() as ImportedCandle["timeframe"],
      candleTime: row.time,
      open: row.open,
      high: row.high,
      low: row.low,
      close: row.close,
      volume: row.volume,
      source: "api" as const,
      importedAt: new Date().toISOString(),
    }));
  return { status: payload.status, source: payload.source, reason: payload.reason, meta: payload.meta, candles };
}

export async function fetchBackendDataStatus(endpoint = `${backendBaseUrl()}/api/data-status`): Promise<BackendDataStatus> {
  const response = await fetch(endpoint, { cache: "no-store" });
  if (!response.ok) throw new Error(`Backend data-status endpoint returned ${response.status}`);
  return response.json() as Promise<BackendDataStatus>;
}

export async function fetchGoldMarketContext(endpoint = `${backendBaseUrl()}/api/market/context/gold`): Promise<GoldMarketContext> {
  const response = await fetch(endpoint, { cache: "no-store" });
  if (!response.ok) throw new Error(`Gold context endpoint returned ${response.status}`);
  return response.json() as Promise<GoldMarketContext>;
}
