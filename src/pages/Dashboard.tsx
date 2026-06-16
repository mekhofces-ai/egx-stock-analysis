import { AlertTriangle, CheckCircle2, Clock, RefreshCw, ShieldAlert, TrendingUp } from "lucide-react";
import KPICard from "../components/KPICard";
import BestStockCard from "../components/BestStockCard";
import ScreenerTable from "../components/ScreenerTable";
import SmartAlertsPanel from "../components/SmartAlertsPanel";
import VolumeAlertsPanel from "../components/VolumeAlertsPanel";
import type { MarketDataSnapshot } from "../data/mockData";
import { money } from "../lib/format";

function formatChange(value?: number) {
  if (value === undefined) return "-";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function MiniMoverRow({ row, onOpenStock }: { row: MarketDataSnapshot["screenerRows"][number]; onOpenStock: (symbol: string) => void }) {
  const positive = (row.changePercent ?? 0) >= 0;
  return (
    <button onClick={() => onOpenStock(row.symbol)} className="grid w-full grid-cols-[64px_1fr_auto] items-center gap-2 border-b border-terminal-border/70 px-3 py-2 text-left text-xs last:border-0 hover:bg-white/[0.03]">
      <span className="font-bold text-white">{row.symbol}</span>
      <span className="truncate text-slate-400">{row.companyName}</span>
      <span className={positive ? "font-semibold text-emerald-300" : "font-semibold text-red-300"}>{formatChange(row.changePercent)}</span>
    </button>
  );
}

function isLatestMoverRow(row: MarketDataSnapshot["screenerRows"][number], latestCompletedDate?: string | null) {
  if (!latestCompletedDate || !row.lastUpdateEgypt || row.lastUpdateEgypt === "Unavailable") return false;
  const rowDate = new Date(row.lastUpdateEgypt).toLocaleDateString("en-GB", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit" });
  return rowDate === latestCompletedDate;
}

export default function Dashboard({
  data,
  onOpenStock,
  onManualRefresh,
  isRefreshing = false,
  refreshMessage = "",
}: {
  data: MarketDataSnapshot;
  onOpenStock: (symbol: string) => void;
  onManualRefresh?: () => void;
  isRefreshing?: boolean;
  refreshMessage?: string;
}) {
  const { bestStocks, screenerRows, timeframeAnalyses } = data;
  const actionRows = bestStocks.length ? bestStocks : timeframeAnalyses;
  const buyNow = actionRows.filter((row) => ["BUY NOW", "BREAKOUT BUY", "PULLBACK BUY AREA"].includes("actionNow" in row ? row.actionNow : row.bestAction)).length;
  const avoid = actionRows.filter((row) => ["SELL NOW", "DO NOT BUY NOW"].includes("actionNow" in row ? row.actionNow : row.bestAction)).length;
  const marketBias = buyNow > avoid ? "Bullish Watch" : avoid > buyNow ? "Risk Off" : data.realCoverageCount ? "Mixed" : "No Provider Data";
  const highSmartAlerts = data.smartAlerts.filter((alert) => alert.severity === "High").length;
  const pricedRows = screenerRows.filter((row) => row.changePercent !== undefined && row.dataQuality !== "unavailable");
  const moverRows = pricedRows.filter((row) => isLatestMoverRow(row, data.moverContext?.latestCompletedDate));
  const unavailableRows = screenerRows.filter((row) => row.dataQuality === "unavailable");
  const realBidAskRows = screenerRows.filter((row) => row.orderBookStatus === "real" && row.bid && row.ask);
  const highlightedUnavailable = [...unavailableRows].sort((a, b) => Number(b.symbol === "CRST") - Number(a.symbol === "CRST"));
  const topGainers = moverRows.filter((row) => (row.changePercent ?? 0) > 0).sort((a, b) => (b.changePercent ?? 0) - (a.changePercent ?? 0)).slice(0, 5);
  const topLosers = moverRows.filter((row) => (row.changePercent ?? 0) < 0).sort((a, b) => (a.changePercent ?? 0) - (b.changePercent ?? 0)).slice(0, 5);
  const activeProvider = data.backendStatus?.activeProvider ?? "unknown";
  const activeProviderStatus = data.backendStatus?.providers?.find((provider) => provider.provider === activeProvider);
  const providerReason = activeProviderStatus?.reason ?? "Provider status reason is not available yet.";
  const latestCompletedLabel = data.moverContext?.latestCompletedDate ?? "Unavailable";
  const isStaleSession = Boolean(data.moverContext && !data.moverContext.isTodaySession);
  const autoRefreshMinutes = data.backendStatus?.autoRefreshIntervalMs ? Math.round(data.backendStatus.autoRefreshIntervalMs / 60000) : null;

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-terminal-border bg-terminal-card p-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-sm font-bold text-white">Market Data Refresh</h2>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              Latest completed candle: {data.moverContext?.latestCompletedDate ?? "Unavailable"} - Last backend refresh: {data.backendStatus?.latestDataRefreshAt ? new Date(data.backendStatus.latestDataRefreshAt).toLocaleString("en-GB", { timeZone: "Africa/Cairo", hour12: false }) : "Unavailable"}
            </p>
            {refreshMessage && <div className="mt-2 text-xs text-teal-300">{refreshMessage}</div>}
          </div>
          <button
            type="button"
            onClick={onManualRefresh}
            disabled={isRefreshing || !onManualRefresh}
            className="inline-flex h-9 items-center justify-center gap-2 rounded border border-terminal-border bg-[#0C131D] px-3 text-xs font-semibold text-slate-200 hover:border-teal-400/50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw size={15} className={isRefreshing ? "animate-spin" : ""} />
            {isRefreshing ? "Refreshing..." : "Manual Update Now"}
          </button>
        </div>
      </section>

      <section className={`rounded-lg border p-3 ${isStaleSession ? "border-amber-400/35 bg-amber-500/8" : "border-terminal-border bg-terminal-card"}`}>
        <div className="grid gap-3 xl:grid-cols-[1.1fr_1fr_auto] xl:items-center">
          <div>
            <div className="text-xs font-semibold uppercase text-slate-500">Active Data Provider</div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span className="rounded border border-cyan-400/35 bg-cyan-500/10 px-2 py-1 text-sm font-bold text-cyan-200">{activeProvider}</span>
              <span className={`rounded border px-2 py-1 text-xs font-semibold ${activeProviderStatus?.status === "available" ? "border-emerald-400/35 bg-emerald-500/10 text-emerald-200" : activeProviderStatus?.status === "degraded" ? "border-amber-400/35 bg-amber-500/10 text-amber-200" : "border-red-400/35 bg-red-500/10 text-red-200"}`}>{activeProviderStatus?.status ?? "not checked"}</span>
              <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-xs text-slate-300">Latest candle: {latestCompletedLabel}</span>
              <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-xs text-slate-300">Real bid/ask: {data.backendStatus?.realBidAskSnapshots ?? 0}</span>
              <span className={`rounded border px-2 py-1 text-xs font-semibold ${data.backendStatus?.autoRefreshEnabled ? "border-emerald-400/35 bg-emerald-500/10 text-emerald-200" : "border-slate-400/30 bg-slate-500/10 text-slate-300"}`}>
                Auto refresh: {data.backendStatus?.autoRefreshEnabled ? `ON${autoRefreshMinutes ? ` / ${autoRefreshMinutes}m` : ""}` : "OFF"}
              </span>
            </div>
          </div>
          <p className="text-xs leading-5 text-slate-400">
            {providerReason} Prices refresh from the embedded EGX-AI-compatible API running inside this backend. The source follows the repo's public Mubasher page model and is delayed during the session; true bid/ask remains unavailable unless a licensed order-book feed is added.
          </p>
          <a href="/import-data" className="inline-flex h-9 items-center justify-center rounded border border-terminal-border bg-[#0C131D] px-3 text-xs font-semibold text-slate-200 hover:border-teal-400/50">
            Data Setup
          </a>
        </div>
      </section>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <KPICard label="Buy Now Count" value={buyNow} detail="Confirmed or near-zone buy actions from available data" icon={<CheckCircle2 size={18} />} tone="bg-emerald-500/15 text-emerald-300" />
        <KPICard label="Smart Alerts" value={data.smartAlerts.length} detail={`${highSmartAlerts} high urgency alarms`} icon={<Clock size={18} />} tone="bg-amber-500/15 text-amber-300" />
        <KPICard label="Avoid / Sell Count" value={avoid} detail="Sell pressure or trend breakdown from available data" icon={<ShieldAlert size={18} />} tone="bg-red-500/15 text-red-300" />
        <KPICard label="Market Bias" value={marketBias} detail="Derived only from provider-returned candles" icon={<TrendingUp size={18} />} tone="bg-cyan-500/15 text-cyan-300" />
        <KPICard label="EGX Symbols" value={data.stocks.length} detail={`Priced coverage ${data.realCoverageCount}/${data.stocks.length} symbols`} icon={<AlertTriangle size={18} />} tone="bg-slate-500/15 text-slate-300" />
      </div>

      <SmartAlertsPanel alerts={data.smartAlerts} onOpenStock={onOpenStock} />

      <div className="grid gap-3 xl:grid-cols-3">
        <section className="rounded-lg border border-terminal-border bg-terminal-card">
          <div className="flex items-center justify-between border-b border-terminal-border px-3 py-2">
            <h2 className="text-sm font-bold text-white">Latest Completed Gainers</h2>
            <span className="text-[11px] text-slate-500">{data.moverContext?.latestCompletedDate ?? "No date"}</span>
          </div>
          {topGainers.map((row) => <MiniMoverRow key={row.symbol} row={row} onOpenStock={onOpenStock} />)}
          {!topGainers.length && <div className="p-4 text-sm text-slate-500">No positive gainers in the latest completed provider session.</div>}
        </section>
        <section className="rounded-lg border border-terminal-border bg-terminal-card">
          <div className="flex items-center justify-between border-b border-terminal-border px-3 py-2">
            <h2 className="text-sm font-bold text-white">Latest Completed Losers</h2>
            <span className="text-[11px] text-slate-500">{data.moverContext?.latestCompletedDate ?? "No date"}</span>
          </div>
          {topLosers.map((row) => <MiniMoverRow key={row.symbol} row={row} onOpenStock={onOpenStock} />)}
          {!topLosers.length && <div className="p-4 text-sm text-slate-500">No negative losers in the latest completed provider session.</div>}
        </section>
        <section className="rounded-lg border border-terminal-border bg-terminal-card">
          <div className="flex items-center justify-between border-b border-terminal-border px-3 py-2">
            <h2 className="text-sm font-bold text-white">Watchlist</h2>
            <span className="text-[11px] text-slate-500">Generated from ranked rows</span>
          </div>
          {data.watchlist.slice(0, 5).map((item) => {
            const row = screenerRows.find((entry) => entry.symbol === item.symbol);
            return (
              <button key={item.symbol} onClick={() => onOpenStock(item.symbol)} className="grid w-full grid-cols-[64px_1fr_auto] items-center gap-2 border-b border-terminal-border/70 px-3 py-2 text-left text-xs last:border-0 hover:bg-white/[0.03]">
                <span className="font-bold text-white">{item.symbol}</span>
                <span className="truncate text-slate-400">{item.userNotes}</span>
                <span className="font-semibold text-slate-300">{row?.currentPrice ? money(row.currentPrice) : "-"}</span>
              </button>
            );
          })}
        </section>
      </div>

      <VolumeAlertsPanel alerts={data.volumeAlerts} onOpenStock={onOpenStock} compact />

      <section className="rounded-lg border border-terminal-border bg-terminal-card p-3">
        <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-sm font-bold text-white">Data Quality Audit</h2>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              {data.moverContext?.warning} Public delayed coverage is partial, so unavailable symbols are visible in the screener but excluded from mover rankings.
            </p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="rounded border border-emerald-400/30 bg-emerald-500/10 px-2 py-1 text-emerald-300">{pricedRows.length} priced</span>
            <span className="rounded border border-amber-400/30 bg-amber-500/10 px-2 py-1 text-amber-300">{data.moverContext?.latestSessionRows ?? 0} latest-session movers</span>
            <span className="rounded border border-slate-400/30 bg-slate-500/10 px-2 py-1 text-slate-300">{data.moverContext?.staleMoverCount ?? 0} stale mover rows hidden</span>
            <span className="rounded border border-cyan-400/30 bg-cyan-500/10 px-2 py-1 text-cyan-300">{realBidAskRows.length} real bid/ask</span>
            <span className="rounded border border-red-400/30 bg-red-500/10 px-2 py-1 text-red-300">{unavailableRows.length} unavailable</span>
            <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-slate-400">Missing examples: {highlightedUnavailable.slice(0, 6).map((row) => row.symbol).join(", ") || "None"}</span>
          </div>
        </div>
      </section>

      {unavailableRows.length > 0 && (
        <section className="rounded-lg border border-terminal-border bg-terminal-card">
          <div className="flex flex-col gap-1 border-b border-terminal-border px-3 py-2 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h2 className="text-sm font-bold text-white">Missing Provider Coverage</h2>
              <p className="mt-1 text-xs text-slate-500">Symbols are in the EGX universe, but the active free chart provider has no usable daily OHLCV for them.</p>
            </div>
            <span className="rounded border border-red-400/30 bg-red-500/10 px-2 py-1 text-xs font-semibold text-red-200">{unavailableRows.length} unavailable</span>
          </div>
          <div className="max-h-[260px] overflow-auto">
            <table className="w-full min-w-[920px] text-left text-xs">
              <thead className="sticky top-0 bg-[#101A27] text-[11px] uppercase text-slate-400">
                <tr>
                  <th className="border-b border-terminal-border px-3 py-2">Symbol</th>
                  <th className="border-b border-terminal-border px-3 py-2">Company</th>
                  <th className="border-b border-terminal-border px-3 py-2">Provider reason</th>
                </tr>
              </thead>
              <tbody>
                {unavailableRows.map((row) => (
                  <tr key={row.symbol} className="border-b border-terminal-border/70 hover:bg-white/[0.03]">
                    <td className="px-3 py-2 font-bold text-white">{row.symbol}</td>
                    <td className="px-3 py-2 text-slate-300">{row.companyName}</td>
                    <td className="px-3 py-2 text-slate-400">{row.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-bold text-white">Best Stocks Preview</h2>
          <span className="text-xs text-slate-500">Universe: {data.stocks.length} EGX symbols - Priced coverage: {data.realCoverageCount}/{data.stocks.length} - {data.sourceLabel}</span>
        </div>
        {bestStocks.length ? (
          <div className="grid gap-3 xl:grid-cols-3 2xl:grid-cols-4">
            {bestStocks.slice(0, 4).map((stock) => <BestStockCard key={stock.symbol} stock={stock} onOpen={onOpenStock} />)}
          </div>
        ) : (
          <div className="rounded-lg border border-terminal-border bg-terminal-card p-5 text-sm text-slate-400">No provider candle data has been received yet. The app will rank stocks only after OHLCV candles arrive from the active source.</div>
        )}
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-bold text-white">Screener Preview</h2>
          <span className="text-xs text-slate-500">All EGX symbols; unavailable fields are not faked</span>
        </div>
        <ScreenerTable rows={screenerRows.slice(0, 10)} analyses={timeframeAnalyses} onOpen={onOpenStock} compact />
      </section>

      <footer className="rounded-lg border border-amber-400/25 bg-amber-500/8 p-3 text-xs text-amber-200">
        Educational analysis only. This is not financial advice. No profit is guaranteed. Trading involves risk. No sample prices are used in live mode.
      </footer>
    </div>
  );
}
