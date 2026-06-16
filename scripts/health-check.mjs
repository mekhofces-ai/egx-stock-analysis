const checks = [];

async function checkJson(name, url, validate) {
  const started = Date.now();
  try {
    const response = await fetch(url, { cache: "no-store" });
    const text = await response.text();
    const json = text ? JSON.parse(text) : null;
    const validation = validate ? validate(json) : { ok: response.ok };
    checks.push({
      name,
      url,
      ok: response.ok && validation.ok,
      status: response.status,
      ms: Date.now() - started,
      detail: validation.detail ?? null,
    });
  } catch (error) {
    checks.push({
      name,
      url,
      ok: false,
      status: 0,
      ms: Date.now() - started,
      detail: error instanceof Error ? error.message : String(error),
    });
  }
}

async function checkText(name, url, expectedText) {
  const started = Date.now();
  try {
    const response = await fetch(url, { cache: "no-store" });
    const text = await response.text();
    checks.push({
      name,
      url,
      ok: response.ok && text.includes(expectedText),
      status: response.status,
      ms: Date.now() - started,
      detail: response.ok ? `contains "${expectedText}"` : response.statusText,
    });
  } catch (error) {
    checks.push({
      name,
      url,
      ok: false,
      status: 0,
      ms: Date.now() - started,
      detail: error instanceof Error ? error.message : String(error),
    });
  }
}

await checkText("frontend", "http://localhost:5173/analyst-bot", "EGX Smart Screener");

await checkJson("backend-data-status", "http://localhost:8788/api/data-status", (json) => ({
  ok: Boolean(json?.activeProvider && json?.totalSymbols >= 200 && (json?.symbolsWithCurrentPrices ?? json?.symbolsWithCandles) > 0 && json?.latestCompletedCandleAt),
  detail: json
    ? `provider=${json.activeProvider}; priced=${json.symbolsWithCurrentPrices ?? "?"}/${json.totalSymbols}; analysis=${json.symbolsWithStrategyAnalysis ?? "?"}/${json.totalSymbols}; candles=${json.symbolsWithCandles}/${json.totalSymbols}; latest=${json.latestCompletedCandleAt}; bidAsk=${json.bidAskStatus}`
    : "empty response",
}));

await checkJson("scanner", "http://localhost:8788/api/market/scanner", (json) => ({
  ok: Array.isArray(json?.data) && json.data.some((row) => row?.price),
  detail: Array.isArray(json?.data) ? `rows=${json.data.length}` : "missing scanner rows",
}));

await checkJson("top-gainers", "http://localhost:8788/api/market/top-gainers", (json) => ({
  ok: Array.isArray(json?.data) && json.data.length > 0,
  detail: Array.isArray(json?.data) && json.data[0]
    ? `first=${json.data[0].symbol}; change=${json.data[0].changePercent}`
    : "missing gainers",
}));

await checkJson("webhook", "http://localhost:8787/api/health", (json) => ({
  ok: Boolean(json?.ok),
  detail: json ? `candles=${json.candles}; signals=${json.signals}; events=${json.events}` : "empty response",
}));

const ok = checks.every((check) => check.ok);
const report = {
  ok,
  checkedAt: new Date().toISOString(),
  checks,
};

console.log(JSON.stringify(report, null, 2));
if (!ok) process.exitCode = 1;
