import WebSocket from "ws";
import { config } from "../config.js";
import { prisma } from "../db.js";
import { buildBidAskExpectation, spreadPercent } from "../services/bidAskExpectation.js";
import { buildTimeframeAnalysis, recommendation } from "../services/technicalAnalysis.js";
import type { Candle, ProviderResult, Quote, ScannerRow, Timeframe } from "../types.js";
import type { MarketDataProvider } from "./MarketDataProvider.js";
import { unavailable } from "./MarketDataProvider.js";

type RefinitivFields = Record<string, unknown>;

type RefinitivMessage = {
  ID?: number;
  Type?: string;
  Domain?: string;
  State?: {
    Stream?: string;
    Data?: string;
    Code?: string;
    Text?: string;
  };
  Key?: {
    Name?: string;
    Service?: string;
  };
  Fields?: RefinitivFields;
};

type RefinitivSnapshot = {
  ric: string;
  fields: RefinitivFields;
  stateText?: string;
  raw: RefinitivMessage;
};

type RtoToken = {
  access_token?: string;
  refresh_token?: string;
  expires_in?: string | number;
  error?: string;
  error_description?: string;
};

type RtoDiscoveryResponse = {
  services?: Array<{
    endpoint?: string;
    port?: number;
    location?: string[];
  }>;
};

type ConnectionInfo = {
  url: string;
  authenticationToken?: string;
};

const SOURCE_NOTE = "LSEG/Refinitiv WebSocket API provider. Requires licensed LSEG real-time access, EGX entitlements, and a WebSocket endpoint such as ADS/RTDS WebSocket.";
const DEFAULT_VIEW = [
  "TRDPRC_1",
  "BID",
  "ASK",
  "BIDSIZE",
  "ASKSIZE",
  "OPEN_PRC",
  "HST_CLOSE",
  "HIGH_1",
  "LOW_1",
  "ACVOL_1",
  "NETCHNG_1",
  "PCTCHNG",
  "TRADE_DATE",
  "TRDTIM_1",
];

function round(value: number, digits = 2) {
  return Number(value.toFixed(digits));
}

function numberOrUndefined(value: unknown) {
  if (value === null || value === undefined || value === "") return undefined;
  const parsed = Number(String(value).replace(/,/g, ""));
  return Number.isFinite(parsed) ? parsed : undefined;
}

function firstNumber(fields: RefinitivFields, names: string[]) {
  for (const name of names) {
    const value = numberOrUndefined(fields[name]);
    if (value !== undefined) return value;
  }
  return undefined;
}

function dateForSnapshot(fields: RefinitivFields) {
  const tradeDate = String(fields.TRADE_DATE ?? "").trim();
  const tradeTime = String(fields.TRDTIM_1 ?? "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(tradeDate)) {
    const time = /^\d{2}:\d{2}/.test(tradeTime) ? tradeTime : "12:00:00";
    return new Date(`${tradeDate}T${time}+02:00`).toISOString();
  }
  return new Date().toISOString();
}

function cairoCandleTime(value: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Africa/Cairo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(value));
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return new Date(`${byType.year}-${byType.month}-${byType.day}T10:00:00+02:00`);
}

function isOkLogin(message: RefinitivMessage) {
  if (message.Type !== "Refresh" || message.Domain !== "Login") return false;
  return message.State?.Stream !== "Closed" && message.State?.Data !== "Suspect";
}

function parseMessages(data: WebSocket.RawData): RefinitivMessage[] {
  const parsed = JSON.parse(data.toString()) as RefinitivMessage | RefinitivMessage[];
  return Array.isArray(parsed) ? parsed : [parsed];
}

async function safePersist(work: () => Promise<unknown>) {
  try {
    await work();
  } catch {
    // Provider data should remain usable even if local persistence is temporarily unavailable.
  }
}

export class RefinitivWebSocketProvider implements MarketDataProvider {
  readonly name = "refinitiv-websocket";

  private configured() {
    if (config.REFINITIV_AUTH_MODE === "ads") return Boolean(config.REFINITIV_WS_URL && config.REFINITIV_WS_USERNAME);
    if (config.REFINITIV_AUTH_MODE === "rto-password") {
      return Boolean(config.REFINITIV_WS_USERNAME && config.REFINITIV_WS_PASSWORD && config.REFINITIV_WS_CLIENT_ID);
    }
    return Boolean(config.REFINITIV_WS_CLIENT_ID && config.REFINITIV_WS_CLIENT_SECRET);
  }

  private ricForSymbol(symbol: string) {
    const normalized = symbol.trim().toUpperCase();
    if (normalized.includes(".")) return normalized;
    return `${normalized}${config.REFINITIV_RIC_SUFFIX}`;
  }

  private symbolFromRic(ric: string) {
    const suffix = config.REFINITIV_RIC_SUFFIX.toUpperCase();
    return ric.toUpperCase().endsWith(suffix) ? ric.slice(0, -suffix.length).toUpperCase() : ric.toUpperCase();
  }

  private view() {
    const configured = config.REFINITIV_WS_VIEW_FIELDS.split(",").map((field) => field.trim()).filter(Boolean);
    return configured.length ? configured : DEFAULT_VIEW;
  }

  private async fetchRtoToken(): Promise<ProviderResult<string>> {
    const params = new URLSearchParams();
    params.set("scope", config.REFINITIV_SCOPE);

    let endpoint = config.REFINITIV_AUTH_URL_V1;
    if (config.REFINITIV_AUTH_MODE === "rto-password") {
      params.set("grant_type", "password");
      params.set("username", config.REFINITIV_WS_USERNAME);
      params.set("password", config.REFINITIV_WS_PASSWORD);
      params.set("client_id", config.REFINITIV_WS_CLIENT_ID);
      params.set("takeExclusiveSignOnControl", "true");
      if (config.REFINITIV_WS_CLIENT_SECRET) params.set("client_secret", config.REFINITIV_WS_CLIENT_SECRET);
    } else {
      endpoint = config.REFINITIV_AUTH_URL_V2;
      params.set("grant_type", "client_credentials");
      params.set("client_id", config.REFINITIV_WS_CLIENT_ID);
      params.set("client_secret", config.REFINITIV_WS_CLIENT_SECRET);
    }

    const headers: Record<string, string> = {
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
    };
    if (config.REFINITIV_WS_CLIENT_SECRET && config.REFINITIV_AUTH_MODE === "rto-password") {
      headers.Authorization = `Basic ${Buffer.from(`${config.REFINITIV_WS_CLIENT_ID}:${config.REFINITIV_WS_CLIENT_SECRET}`).toString("base64")}`;
    }

    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers,
        body: params,
      });
      const payload = await response.json().catch(() => ({})) as RtoToken;
      if (!response.ok || !payload.access_token) {
        return unavailable(this.name, `Refinitiv RTO authentication failed with HTTP ${response.status}: ${payload.error_description ?? payload.error ?? response.statusText}`);
      }
      return { status: "available", source: this.name, reason: "Authenticated with LSEG Delivery Platform for Refinitiv Real-Time Optimized.", data: payload.access_token };
    } catch (error) {
      return unavailable(this.name, error instanceof Error ? error.message : "Refinitiv RTO authentication request failed.");
    }
  }

  private async discoverRtoWebSocketUrl(token: string): Promise<ProviderResult<string>> {
    try {
      const endpoint = new URL(config.REFINITIV_DISCOVERY_URL);
      endpoint.searchParams.set("transport", "websocket");
      const response = await fetch(endpoint, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const payload = await response.json().catch(() => ({})) as RtoDiscoveryResponse;
      if (!response.ok) return unavailable(this.name, `Refinitiv RTO service discovery failed with HTTP ${response.status}.`);
      const service = payload.services?.find((item) => item.location?.some((location) => location.startsWith(config.REFINITIV_REGION)))
        ?? payload.services?.[0];
      if (!service?.endpoint || !service.port) {
        return unavailable(this.name, `Refinitiv RTO service discovery returned no endpoint for region ${config.REFINITIV_REGION}.`);
      }
      return {
        status: "available",
        source: this.name,
        reason: `Discovered Refinitiv RTO endpoint in region ${config.REFINITIV_REGION}.`,
        data: `wss://${service.endpoint}:${service.port}/WebSocket`,
      };
    } catch (error) {
      return unavailable(this.name, error instanceof Error ? error.message : "Refinitiv RTO service discovery failed.");
    }
  }

  private async connectionInfo(): Promise<ProviderResult<ConnectionInfo>> {
    if (config.REFINITIV_AUTH_MODE === "ads") {
      if (!config.REFINITIV_WS_URL || !config.REFINITIV_WS_USERNAME) {
        return unavailable(this.name, "ADS mode requires REFINITIV_WS_URL and REFINITIV_WS_USERNAME.");
      }
      return { status: "available", source: this.name, reason: "Using direct ADS/RTDS WebSocket login.", data: { url: config.REFINITIV_WS_URL } };
    }

    const token = await this.fetchRtoToken();
    if (!token.data) return unavailable(this.name, token.reason);
    if (config.REFINITIV_WS_URL) {
      return {
        status: "available",
        source: this.name,
        reason: "Using configured Refinitiv RTO WebSocket URL with LDP authentication token.",
        data: { url: config.REFINITIV_WS_URL, authenticationToken: token.data },
      };
    }
    const discovered = await this.discoverRtoWebSocketUrl(token.data);
    if (!discovered.data) return unavailable(this.name, discovered.reason);
    return {
      status: "available",
      source: this.name,
      reason: discovered.reason,
      data: { url: discovered.data, authenticationToken: token.data },
    };
  }

  private async requestSnapshots(rics: string[]): Promise<ProviderResult<Map<string, RefinitivSnapshot>>> {
    if (!this.configured()) {
      return unavailable(this.name, "Refinitiv is not configured. ADS mode needs REFINITIV_WS_URL + REFINITIV_WS_USERNAME. RTO password mode needs username/password/client id. RTO client-credentials mode needs client id/client secret.");
    }

    const uniqueRics = [...new Set(rics.map((ric) => ric.trim().toUpperCase()).filter(Boolean))];
    if (!uniqueRics.length) return unavailable(this.name, "No RICs requested.");

    const connection = await this.connectionInfo();
    if (!connection.data) return unavailable(this.name, connection.reason);

    const snapshots = new Map<string, RefinitivSnapshot>();
    const errors: string[] = [];

    return new Promise((resolve) => {
      let resolved = false;
      let requestSent = false;
      const finish = (result: ProviderResult<Map<string, RefinitivSnapshot>>) => {
        if (resolved) return;
        resolved = true;
        clearTimeout(timer);
        try {
          socket.close();
        } catch {
          // Ignore close errors; the request is already resolved.
        }
        resolve(result);
      };

      const timer = setTimeout(() => {
        if (snapshots.size) {
          finish({
            status: snapshots.size === uniqueRics.length ? "available" : "degraded",
            source: this.name,
            reason: snapshots.size === uniqueRics.length ? SOURCE_NOTE : `${SOURCE_NOTE} Partial snapshot: ${snapshots.size}/${uniqueRics.length} RICs returned before timeout. ${errors.join("; ")}`,
            data: snapshots,
          });
        } else {
          finish(unavailable(this.name, `Refinitiv WebSocket request timed out after ${config.REFINITIV_WS_TIMEOUT_MS}ms. ${errors.join("; ")}`));
        }
      }, config.REFINITIV_WS_TIMEOUT_MS);

      const socket = new WebSocket(connection.data.url, "tr_json2");

      const sendMarketRequest = () => {
        if (requestSent) return;
        requestSent = true;
        const key: Record<string, unknown> = { Name: uniqueRics.length === 1 ? uniqueRics[0] : uniqueRics };
        if (config.REFINITIV_WS_SERVICE) key.Service = config.REFINITIV_WS_SERVICE;
        socket.send(JSON.stringify({
          ID: 2,
          Key: key,
          View: this.view(),
          Streaming: false,
        }));
      };

      socket.on("open", () => {
        if (connection.data?.authenticationToken) {
          socket.send(JSON.stringify({
            ID: 1,
            Domain: "Login",
            Key: {
              NameType: "AuthnToken",
              Elements: {
                ApplicationId: config.REFINITIV_WS_APP_ID,
                Position: config.REFINITIV_WS_POSITION,
                AuthenticationToken: connection.data.authenticationToken,
              },
            },
          }));
        } else {
          socket.send(JSON.stringify({
            ID: 1,
            Domain: "Login",
            Key: {
              Name: config.REFINITIV_WS_USERNAME,
              Elements: {
                ApplicationId: config.REFINITIV_WS_APP_ID,
                Position: config.REFINITIV_WS_POSITION,
              },
            },
          }));
        }
      });

      socket.on("message", (data) => {
        let messages: RefinitivMessage[];
        try {
          messages = parseMessages(data);
        } catch (error) {
          errors.push(error instanceof Error ? error.message : "Failed to parse Refinitiv WebSocket message.");
          return;
        }

        for (const message of messages) {
          if (message.Type === "Ping") {
            socket.send(JSON.stringify({ Type: "Pong" }));
            continue;
          }

          if (isOkLogin(message)) {
            sendMarketRequest();
            continue;
          }

          if (message.Type === "Status" && message.State?.Text) {
            errors.push(message.State.Text);
          }

          if ((message.Type === "Refresh" || message.Type === "Update") && message.Fields) {
            const ric = message.Key?.Name?.toUpperCase();
            if (ric) {
              snapshots.set(ric, { ric, fields: message.Fields, stateText: message.State?.Text, raw: message });
            }
          }
        }

        if (snapshots.size >= uniqueRics.length) {
          finish({ status: "available", source: this.name, reason: SOURCE_NOTE, data: snapshots });
        }
      });

      socket.on("error", (error) => {
        errors.push(error.message);
      });

      socket.on("close", () => {
        if (!resolved && snapshots.size) {
          finish({
            status: snapshots.size === uniqueRics.length ? "available" : "degraded",
            source: this.name,
            reason: snapshots.size === uniqueRics.length ? SOURCE_NOTE : `${SOURCE_NOTE} WebSocket closed after partial snapshot: ${snapshots.size}/${uniqueRics.length}. ${errors.join("; ")}`,
            data: snapshots,
          });
        } else if (!resolved) {
          finish(unavailable(this.name, `Refinitiv WebSocket closed before data was received. ${errors.join("; ")}`));
        }
      });
    });
  }

  private quoteFromSnapshot(symbol: string, snapshot: RefinitivSnapshot, stockMeta?: { sector?: string | null; industry?: string | null }): Quote | null {
    const fields = snapshot.fields;
    const price = firstNumber(fields, ["TRDPRC_1", "LAST", "BID", "ASK"]);
    if (price === undefined) return null;
    const previousClose = firstNumber(fields, ["HST_CLOSE", "PREV_CLOSE"]);
    const bid = firstNumber(fields, ["BID"]);
    const ask = firstNumber(fields, ["ASK"]);
    const volume = firstNumber(fields, ["ACVOL_1", "VOLUME"]);
    const changePercent = firstNumber(fields, ["PCTCHNG"]) ?? (previousClose ? round(((price - previousClose) / previousClose) * 100) : undefined);
    const orderBookStatus = bid !== undefined && ask !== undefined ? "real" as const : "unavailable" as const;
    return {
      symbol,
      price: round(price),
      previousClose,
      changePercent,
      volume,
      sector: stockMeta?.sector,
      industry: stockMeta?.industry,
      bid,
      ask,
      spreadPercent: spreadPercent(bid, ask),
      orderBookStatus,
      orderBookNote: orderBookStatus === "real"
        ? "Real Refinitiv top-of-book bid/ask fields returned for this RIC."
        : "Refinitiv snapshot did not include BID and ASK fields for this RIC/view.",
      bidAskExpectation: buildBidAskExpectation({ price, bid, ask, orderBookStatus }, null),
      capturedAt: dateForSnapshot(fields),
    };
  }

  private candleFromSnapshot(symbol: string, timeframe: Timeframe, snapshot: RefinitivSnapshot): Candle | null {
    const fields = snapshot.fields;
    const close = firstNumber(fields, ["TRDPRC_1", "LAST", "BID", "ASK"]);
    if (close === undefined) return null;
    const open = firstNumber(fields, ["OPEN_PRC"]) ?? close;
    const high = firstNumber(fields, ["HIGH_1"]) ?? Math.max(open, close);
    const low = firstNumber(fields, ["LOW_1"]) ?? Math.min(open, close);
    const volume = firstNumber(fields, ["ACVOL_1", "VOLUME"]) ?? 0;
    return {
      symbol,
      timeframe,
      time: cairoCandleTime(dateForSnapshot(fields)).toISOString(),
      open: round(open),
      high: round(high),
      low: round(low),
      close: round(close),
      volume,
      source: this.name,
    };
  }

  private async persistQuote(quote: Quote, snapshot: RefinitivSnapshot) {
    await prisma.quoteSnapshot.create({
      data: {
        symbolCode: quote.symbol,
        price: quote.price,
        previousClose: quote.previousClose,
        changePercent: quote.changePercent,
        volume: quote.volume,
        bid: quote.bid,
        ask: quote.ask,
        source: this.name,
        orderBookStatus: quote.orderBookStatus,
        orderBookNote: quote.orderBookNote,
        rawPayload: snapshot.raw,
        capturedAt: new Date(quote.capturedAt),
      },
    });
  }

  private async persistCandle(candle: Candle, snapshot: RefinitivSnapshot) {
    await prisma.candle.upsert({
      where: {
        symbolCode_timeframe_candleTime: {
          symbolCode: candle.symbol,
          timeframe: candle.timeframe,
          candleTime: new Date(candle.time),
        },
      },
      update: {
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume,
        source: this.name,
        quality: "partial",
        rawPayload: snapshot.raw,
        importedAt: new Date(),
      },
      create: {
        symbolCode: candle.symbol,
        timeframe: candle.timeframe,
        candleTime: new Date(candle.time),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume,
        source: this.name,
        quality: "partial",
        rawPayload: snapshot.raw,
      },
    });
  }

  async getQuote(symbol: string): Promise<ProviderResult<Quote>> {
    const normalized = symbol.toUpperCase();
    const ric = this.ricForSymbol(normalized);
    const result = await this.requestSnapshots([ric]);
    const snapshot = result.data?.get(ric);
    if (!snapshot) return unavailable(this.name, result.reason ?? `No Refinitiv snapshot returned for ${ric}.`);
    const stock = await prisma.egxSymbol.findUnique({ where: { symbolCode: normalized } });
    const quote = this.quoteFromSnapshot(normalized, snapshot, stock ?? undefined);
    if (!quote) return unavailable(this.name, `Refinitiv snapshot for ${ric} did not contain a usable price field.`);
    await safePersist(() => this.persistQuote(quote, snapshot));
    const candle = this.candleFromSnapshot(normalized, "1D", snapshot);
    if (candle) await safePersist(() => this.persistCandle(candle, snapshot));
    return { status: quote.orderBookStatus === "real" ? "available" : "degraded", source: this.name, reason: SOURCE_NOTE, data: quote };
  }

  async getCandles(symbol: string, timeframe: Timeframe): Promise<ProviderResult<Candle[]>> {
    const normalized = symbol.toUpperCase();
    if (timeframe !== "1D") {
      return unavailable(this.name, "Refinitiv WebSocket snapshot mode provides quote/top-of-book and current daily OHLC fields only. Configure a licensed historical/intraday feed or import candles for 15M/30M/1H/4H.");
    }
    await this.getQuote(normalized);
    const rows = await prisma.candle.findMany({
      where: { symbolCode: normalized, timeframe: "1D", source: this.name },
      orderBy: { candleTime: "asc" },
      take: 260,
    });
    const candles = rows.map((row): Candle => ({
      symbol: row.symbolCode,
      timeframe: "1D",
      time: row.candleTime.toISOString(),
      open: row.open,
      high: row.high,
      low: row.low,
      close: row.close,
      volume: row.volume,
      source: row.source,
    }));
    return candles.length
      ? { status: candles.length >= 20 ? "available" : "degraded", source: this.name, reason: `${SOURCE_NOTE} Stored daily snapshots are used as candles; add a historical feed for deeper backfill.`, data: candles }
      : unavailable(this.name, `No stored Refinitiv daily candles available for ${normalized}.`);
  }

  async getScanner(): Promise<ProviderResult<ScannerRow[]>> {
    if (!this.configured()) {
      return unavailable(this.name, "Refinitiv is not configured. See README for ADS and RTO .env options.");
    }
    const symbols = await prisma.egxSymbol.findMany({ where: { isActive: true }, orderBy: { symbolCode: "asc" } });
    const requested = symbols.slice(0, Math.max(1, config.REFINITIV_WS_MAX_BATCH_SIZE));
    const ricBySymbol = new Map(requested.map((symbol) => [symbol.symbolCode, this.ricForSymbol(symbol.symbolCode)]));
    const result = await this.requestSnapshots([...ricBySymbol.values()]);
    if (!result.data?.size) return unavailable(this.name, result.reason ?? "No Refinitiv scanner snapshots returned.");

    const rows: ScannerRow[] = [];
    for (const stock of requested) {
      const ric = ricBySymbol.get(stock.symbolCode);
      const snapshot = ric ? result.data.get(ric) : undefined;
      if (!snapshot) {
        rows.push({ symbol: stock.symbolCode, companyName: stock.companyNameEn, sector: stock.sector, dataQuality: "unavailable", reason: `No Refinitiv snapshot returned for ${ric}.` });
        continue;
      }
      const quote = this.quoteFromSnapshot(stock.symbolCode, snapshot, stock);
      if (!quote) {
        rows.push({ symbol: stock.symbolCode, companyName: stock.companyNameEn, sector: stock.sector, dataQuality: "unavailable", reason: `Refinitiv snapshot for ${ric} did not contain a usable price field.` });
        continue;
      }
      await safePersist(() => this.persistQuote(quote, snapshot));
      const candle = this.candleFromSnapshot(stock.symbolCode, "1D", snapshot);
      if (candle) await safePersist(() => this.persistCandle(candle, snapshot));
      const candles = candle ? [candle] : [];
      const rec = candles.length ? recommendation(candles) : { recommendation: "WATCH" as const, confidence: 30, reason: "Live quote is available, but candle history is not yet available." };
      const analysis = candles.length ? buildTimeframeAnalysis(stock.symbolCode, "1D", candles, true) : undefined;
      rows.push({
        symbol: stock.symbolCode,
        companyName: stock.companyNameEn,
        sector: stock.sector,
        price: quote.price,
        changePercent: quote.changePercent,
        volume: quote.volume,
        capturedAt: quote.capturedAt,
        bid: quote.bid,
        ask: quote.ask,
        spreadPercent: quote.spreadPercent,
        orderBookStatus: quote.orderBookStatus,
        orderBookNote: quote.orderBookNote,
        bidAskExpectation: quote.bidAskExpectation,
        recommendation: rec.recommendation,
        confidence: rec.confidence,
        dataQuality: quote.orderBookStatus === "real" ? "real" : "partial",
        reason: `${rec.reason} (${SOURCE_NOTE})`,
        analysis,
      });
    }

    return {
      status: rows.some((row) => row.dataQuality !== "unavailable") ? "available" : "unavailable",
      source: this.name,
      reason: `${SOURCE_NOTE} Scanner batch size is ${requested.length}/${symbols.length}. Increase REFINITIV_WS_MAX_BATCH_SIZE only if your license and rate limits allow it.`,
      data: rows,
    };
  }

  async refreshScanner(): Promise<ProviderResult<ScannerRow[]>> {
    return this.getScanner();
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
