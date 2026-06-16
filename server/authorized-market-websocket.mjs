import WebSocket from "ws";

const wsUrl = process.env.AUTHORIZED_MARKET_WS_URL || "";
const apiKey = process.env.AUTHORIZED_MARKET_WS_API_KEY || "";
const backendImportUrl = process.env.AUTHORIZED_MARKET_WS_BACKEND_IMPORT_URL || "http://localhost:8788/api/import/candles";
const reconnectMs = Number(process.env.AUTHORIZED_MARKET_WS_RECONNECT_MS || 5000);
const sourceName = process.env.AUTHORIZED_MARKET_WS_SOURCE || "authorized-websocket";

function parseJsonEnv(name, fallback) {
  const raw = process.env[name];
  if (!raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch {
    console.warn(`[provider-ws] ${name} is not valid JSON; using fallback.`);
    return fallback;
  }
}

const extraHeaders = parseJsonEnv("AUTHORIZED_MARKET_WS_HEADERS_JSON", {});
const subscriptions = parseJsonEnv("AUTHORIZED_MARKET_WS_SUBSCRIPTIONS", []);

function normalizeTimeframe(value) {
  const raw = String(value || "").trim().toUpperCase();
  if (raw === "1" || raw === "1M") return "1m";
  if (raw === "5" || raw === "5M") return "5m";
  if (raw === "15" || raw === "15M") return "15M";
  if (raw === "30" || raw === "30M") return "30M";
  if (raw === "60" || raw === "1H") return "1h";
  if (raw === "240" || raw === "4H") return "4H";
  if (raw === "D" || raw === "1D") return "1D";
  return raw || "1D";
}

function toNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function firstValue(payload, names) {
  for (const name of names) {
    if (payload[name] !== undefined && payload[name] !== null) return payload[name];
  }
  return undefined;
}

function normalizeCandle(raw) {
  const payload = raw?.bar ?? raw?.candle ?? raw?.data ?? raw;
  if (!payload || typeof payload !== "object") return null;

  const symbol = String(firstValue(payload, ["symbol", "ticker", "s", "instrument"]) || "").replace(/^EGX:/i, "").toUpperCase();
  const timeframe = normalizeTimeframe(firstValue(payload, ["timeframe", "interval", "resolution", "tf"]));
  const time = String(firstValue(payload, ["time", "timestamp", "candleTime", "t"]) || new Date().toISOString());
  const open = toNumber(firstValue(payload, ["open", "o"]));
  const high = toNumber(firstValue(payload, ["high", "h"]));
  const low = toNumber(firstValue(payload, ["low", "l"]));
  const close = toNumber(firstValue(payload, ["close", "c", "price", "last"]));
  const volume = toNumber(firstValue(payload, ["volume", "v", "vol"])) ?? 0;

  if (!symbol || open === null || high === null || low === null || close === null) return null;
  return { symbol, timeframe, time, open, high, low, close, volume, source: sourceName };
}

async function importCandle(candle) {
  const response = await fetch(backendImportUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candles: [candle] }),
  });
  if (!response.ok && response.status !== 207) {
    const text = await response.text();
    throw new Error(`Backend import failed ${response.status}: ${text}`);
  }
}

function headers() {
  return {
    ...extraHeaders,
    ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
  };
}

function connect() {
  if (!wsUrl) {
    console.error("[provider-ws] AUTHORIZED_MARKET_WS_URL is not configured.");
    process.exitCode = 1;
    return;
  }

  console.log(`[provider-ws] connecting to authorized market websocket: ${wsUrl}`);
  const socket = new WebSocket(wsUrl, { headers: headers() });

  socket.on("open", () => {
    console.log("[provider-ws] connected.");
    for (const subscription of Array.isArray(subscriptions) ? subscriptions : []) {
      socket.send(JSON.stringify(subscription));
      console.log(`[provider-ws] sent subscription: ${JSON.stringify(subscription)}`);
    }
  });

  socket.on("message", async (message) => {
    try {
      const payload = JSON.parse(message.toString());
      const candle = normalizeCandle(payload);
      if (!candle) return;
      await importCandle(candle);
      console.log(`[provider-ws] imported ${candle.symbol}/${candle.timeframe} ${candle.time} close=${candle.close}`);
    } catch (error) {
      console.warn(`[provider-ws] message skipped: ${error instanceof Error ? error.message : "unknown error"}`);
    }
  });

  socket.on("close", (code, reason) => {
    console.warn(`[provider-ws] disconnected (${code}) ${reason.toString()}. Reconnecting in ${reconnectMs}ms.`);
    setTimeout(connect, reconnectMs);
  });

  socket.on("error", (error) => {
    console.warn(`[provider-ws] websocket error: ${error.message}`);
  });
}

connect();
