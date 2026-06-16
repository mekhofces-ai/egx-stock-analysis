import { config } from "../config.js";
import { prisma } from "../db.js";
import type { MarketDataService } from "./marketDataService.js";

const MIN_INTERVAL_MS = 120_000;

function refreshIntervalMs() {
  return Math.max(MIN_INTERVAL_MS, config.AUTO_REFRESH_INTERVAL_MS);
}

function summarizeRows(rows: Awaited<ReturnType<MarketDataService["refreshScanner"]>>["data"] = []) {
  return {
    total: rows.length,
    available: rows.filter((row) => row.dataQuality !== "unavailable").length,
    priced: rows.filter((row) => typeof row.price === "number").length,
    unavailable: rows.filter((row) => row.dataQuality === "unavailable").length,
    latestCompletedCandleAt: rows
      .map((row) => row.analysis?.candleTimeEgypt)
      .filter((time): time is string => Boolean(time))
      .sort()
      .at(-1) ?? null,
  };
}

export function startMarketAutoRefresh(marketData: MarketDataService) {
  if (!config.AUTO_REFRESH_ENABLED) {
    console.log("Market auto-refresh is disabled.");
    return null;
  }

  let running = false;
  let lastStartedAt: Date | null = null;
  let lastFinishedAt: Date | null = null;
  let lastError: string | null = null;

  const run = async (trigger: "startup" | "interval") => {
    if (running) {
      console.log(`Market auto-refresh skipped (${trigger}); previous refresh is still running.`);
      return;
    }
    running = true;
    lastStartedAt = new Date();
    try {
      const result = await marketData.refreshScanner();
      const summary = summarizeRows(result.data);
      lastFinishedAt = new Date();
      lastError = null;
      await prisma.rawDataSnapshot.create({
        data: {
          provider: result.source,
          endpoint: `/api/market/auto-refresh/${trigger}`,
          status: result.status,
          payload: {
            ...summary,
            reason: result.reason,
            durationMs: lastFinishedAt.getTime() - lastStartedAt.getTime(),
            intervalMs: refreshIntervalMs(),
          },
        },
      });
      console.log(
        `Market auto-refresh ${trigger} complete: ${summary.priced}/${summary.total} priced, ${summary.unavailable} unavailable.`,
      );
    } catch (error) {
      lastError = error instanceof Error ? error.message : "Market auto-refresh failed.";
      lastFinishedAt = new Date();
      await prisma.rawDataSnapshot.create({
        data: {
          provider: config.MARKET_DATA_PROVIDER,
          endpoint: `/api/market/auto-refresh/${trigger}`,
          status: "unavailable",
          error: lastError,
        },
      });
      console.error(`Market auto-refresh ${trigger} failed: ${lastError}`);
    } finally {
      running = false;
    }
  };

  if (config.AUTO_REFRESH_ON_START) {
    setTimeout(() => void run("startup"), 3_000);
  }

  const timer = setInterval(() => void run("interval"), refreshIntervalMs());
  return {
    stop: () => clearInterval(timer),
    status: () => ({
      enabled: true,
      running,
      intervalMs: refreshIntervalMs(),
      lastStartedAt: lastStartedAt?.toISOString() ?? null,
      lastFinishedAt: lastFinishedAt?.toISOString() ?? null,
      lastError,
    }),
  };
}
