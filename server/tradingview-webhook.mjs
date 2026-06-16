import http from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const rootDir = join(__dirname, "..");
const dataDir = join(rootDir, "data");
const candlesFile = join(dataDir, "tradingview-candles.json");
const signalsFile = join(dataDir, "tradingview-signals.json");
const eventsFile = join(dataDir, "tradingview-events.json");
const port = Number(process.env.TV_WEBHOOK_PORT || process.env.PORT || 8787);
const secret = process.env.TV_WEBHOOK_SECRET || "";

const headers = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-Webhook-Secret",
};

async function readJson(file, fallback) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch {
    return fallback;
  }
}

async function writeJson(file, value) {
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(value, null, 2));
}

function egyptTimestamp(value = new Date()) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Africa/Cairo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(value).replace(",", "");
}

function toNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function normalizeTimeframe(value) {
  const raw = String(value || "").trim().toUpperCase();
  if (raw === "15" || raw === "15M") return "15M";
  if (raw === "30" || raw === "30M") return "30M";
  if (raw === "240" || raw === "4H") return "4H";
  if (raw === "1D" || raw === "D" || raw === "1DAY") return "1D";
  return raw;
}

function parseBody(rawBody, contentType) {
  if (contentType.includes("application/json")) return JSON.parse(rawBody || "{}");
  try {
    return JSON.parse(rawBody || "{}");
  } catch {
    const pairs = Object.fromEntries(new URLSearchParams(rawBody));
    return Object.keys(pairs).length ? pairs : { message: rawBody };
  }
}

function normalizePayload(payload) {
  const symbol = String(payload.symbol || payload.ticker || payload.syminfo_ticker || "").replace(/^EGX:/i, "").toUpperCase();
  const timeframe = normalizeTimeframe(payload.timeframe || payload.interval || payload.resolution);
  const open = toNumber(payload.open);
  const high = toNumber(payload.high);
  const low = toNumber(payload.low);
  const close = toNumber(payload.close || payload.price);
  const volume = toNumber(payload.volume || 0);
  const eventTime = String(payload.time || payload.candleTime || payload.timestamp || egyptTimestamp());
  const action = String(payload.action || payload.signal || payload.signalType || "TradingView Alert").toUpperCase();

  const candle = symbol && timeframe && open !== null && high !== null && low !== null && close !== null
    ? {
        id: `tv-${symbol}-${timeframe}-${eventTime}`,
        symbol,
        timeframe,
        candleTime: eventTime,
        open,
        high,
        low,
        close,
        volume: volume ?? 0,
        source: "webhook",
        importedAt: egyptTimestamp(),
      }
    : null;

  const signal = symbol
    ? {
        id: `tv-signal-${Date.now()}`,
        symbol,
        timeframe: timeframe || "N/A",
        signalType: String(payload.signalType || payload.type || "TradingView Alert"),
        action,
        price: close ?? toNumber(payload.price) ?? 0,
        score: toNumber(payload.score) ?? 0,
        message: String(payload.message || payload.reason || rawMessage(payload)),
        createdAtEgypt: egyptTimestamp(),
      }
    : null;

  return { candle, signal };
}

function rawMessage(payload) {
  if (typeof payload.message === "string") return payload.message;
  return JSON.stringify(payload);
}

async function handleWebhook(request, response, body) {
  const url = new URL(request.url, `http://${request.headers.host}`);
  const suppliedSecret = request.headers["x-webhook-secret"] || url.searchParams.get("secret") || "";
  if (secret && suppliedSecret !== secret) {
    response.writeHead(401, { ...headers, "Content-Type": "application/json" });
    response.end(JSON.stringify({ ok: false, error: "Invalid webhook secret" }));
    return;
  }

  const payload = parseBody(body, request.headers["content-type"] || "");
  const events = await readJson(eventsFile, []);
  events.unshift({ receivedAtEgypt: egyptTimestamp(), payload });
  await writeJson(eventsFile, events.slice(0, 500));

  const { candle, signal } = normalizePayload(payload);
  if (candle) {
    const candles = await readJson(candlesFile, []);
    const next = [candle, ...candles.filter((item) => item.id !== candle.id)].slice(0, 5000);
    await writeJson(candlesFile, next);
  }
  if (signal) {
    const signals = await readJson(signalsFile, []);
    await writeJson(signalsFile, [signal, ...signals].slice(0, 1000));
  }

  response.writeHead(200, { ...headers, "Content-Type": "application/json" });
  response.end(JSON.stringify({ ok: true, candleSaved: Boolean(candle), signalSaved: Boolean(signal) }));
}

const server = http.createServer(async (request, response) => {
  try {
    if (request.method === "OPTIONS") {
      response.writeHead(204, headers);
      response.end();
      return;
    }

    const url = new URL(request.url, `http://${request.headers.host}`);
    if (request.method === "GET" && url.pathname === "/api/health") {
      const [candles, signals, events] = await Promise.all([
        readJson(candlesFile, []),
        readJson(signalsFile, []),
        readJson(eventsFile, []),
      ]);
      response.writeHead(200, { ...headers, "Content-Type": "application/json" });
      response.end(JSON.stringify({ ok: true, source: "TradingView webhook", port, candles: candles.length, signals: signals.length, events: events.length, lastEvent: events[0]?.receivedAtEgypt ?? null }));
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/tradingview/candles") {
      response.writeHead(200, { ...headers, "Content-Type": "application/json" });
      response.end(JSON.stringify(await readJson(candlesFile, [])));
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/tradingview/signals") {
      response.writeHead(200, { ...headers, "Content-Type": "application/json" });
      response.end(JSON.stringify(await readJson(signalsFile, [])));
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/tradingview/webhook") {
      let body = "";
      request.on("data", (chunk) => {
        body += chunk;
        if (body.length > 1_000_000) request.destroy();
      });
      request.on("end", () => void handleWebhook(request, response, body));
      return;
    }

    response.writeHead(404, { ...headers, "Content-Type": "application/json" });
    response.end(JSON.stringify({ ok: false, error: "Not found" }));
  } catch (error) {
    response.writeHead(500, { ...headers, "Content-Type": "application/json" });
    response.end(JSON.stringify({ ok: false, error: error.message }));
  }
});

server.listen(port, () => {
  console.log(`TradingView webhook server listening on http://localhost:${port}`);
  console.log(`Webhook endpoint: http://localhost:${port}/api/tradingview/webhook`);
});
