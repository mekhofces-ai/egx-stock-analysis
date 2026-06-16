import { useEffect, useMemo, useState } from "react";
import ActionBadge from "../components/ActionBadge";
import CandleVolumeChart from "../components/CandleVolumeChart";
import Metric from "../components/Metric";
import SmartAlertsPanel from "../components/SmartAlertsPanel";
import TechnicalSummaryPanel from "../components/TechnicalSummaryPanel";
import TimeframePanel from "../components/TimeframePanel";
import TradingViewOfficialWidget from "../components/TradingViewOfficialWidget";
import VolumeAlertsPanel from "../components/VolumeAlertsPanel";
import { buildBotPrediction } from "../lib/analystBot";
import { buildTechnicalSummary } from "../lib/analysis";
import { fetchBackendCandlesResult, type BackendCandlesResult } from "../lib/dataAdapters";
import { money } from "../lib/format";
import type { MarketDataSnapshot } from "../data/mockData";
import type { ImportedCandle, Timeframe } from "../types";

const detailTimeframes: Timeframe[] = ["15M", "30M", "1H", "4H", "1D"];

function UnavailableTimeframePanel({ timeframe, result }: { timeframe: Timeframe; result?: BackendCandlesResult }) {
  const status = result?.status ?? "unavailable";
  const reason = result?.reason ?? (
    timeframe === "1D"
      ? "No daily candles were returned for this symbol by the active provider."
      : "No licensed intraday EGX feed is configured for this timeframe. Connect a provider that includes intraday EGX candles to enable this panel."
  );
  return (
    <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-lg font-bold text-white">{timeframe}</div>
        <span className="inline-flex rounded border border-slate-400/30 bg-slate-500/15 px-2 py-1 text-[11px] font-semibold uppercase text-slate-300">{status}</span>
      </div>
      <div className="mt-4 rounded border border-terminal-border bg-[#0C131D] p-4 text-sm leading-6 text-slate-400">
        {reason}
        {timeframe !== "1D" && (
          <div className="mt-2 text-xs text-amber-200">
            The app will not reuse daily candles as intraday candles because that would make the 15M, 1H, and 4H analysis false.
          </div>
        )}
        {result?.source && <div className="mt-2 text-xs text-slate-500">Source: {result.source}</div>}
      </div>
    </section>
  );
}

function formatEgyptDate(value?: string | null) {
  if (!value) return null;
  return new Date(value).toLocaleDateString("en-GB", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit" });
}

function formatEgyptDateTime(value?: string | null) {
  if (!value) return "Unavailable";
  return new Date(value).toLocaleString("en-GB", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function tradingViewSymbolUrl(symbol: string) {
  return `https://www.tradingview.com/symbols/EGX-${symbol}/`;
}

function mubasherSymbolUrl(symbol: string) {
  return `https://www.mubasher.info/markets/EGX/stocks/${symbol}`;
}

function buildChartStatus(result: BackendCandlesResult) {
  const latest = result.meta?.latestReturnedCandleDateEgypt ?? formatEgyptDate(result.meta?.latestReturnedCandleAt);
  const upstream = result.meta?.upstreamLatestCandleDateEgypt ?? formatEgyptDate(result.meta?.upstreamLatestCandleAt);
  const upstreamStatus = result.meta?.upstreamLatestCandleStatus;
  const loaded = result.candles.length
    ? `Loaded ${result.candles.length} daily candles. Latest completed candle: ${latest ?? "unknown"}.`
    : "No daily candles returned by the active provider.";
  const upstreamText = upstream
    ? ` Upstream latest row: ${upstream}${upstreamStatus ? ` (${upstreamStatus})` : ""}.`
    : "";
  return `${loaded}${upstreamText}${result.reason ? ` ${result.reason}` : ""}`;
}

export default function StockDetails({ data, symbol }: { data: MarketDataSnapshot; symbol: string }) {
  const [dailyCandles, setDailyCandles] = useState<ImportedCandle[]>([]);
  const [chartStatus, setChartStatus] = useState("Loading daily chart...");
  const [timeframeStatuses, setTimeframeStatuses] = useState<Partial<Record<Timeframe, BackendCandlesResult>>>({});
  const company = data.stocks.find((stock) => stock.symbol === symbol) ?? data.stocks.find((stock) => stock.symbol === "COMI")!;
  const selected = company.symbol;
  const best = data.bestStocks.find((row) => row.symbol === selected) ?? data.screenerRows.find((row) => row.symbol === selected);
  const rows = data.timeframeAnalyses.filter((row) => row.symbol === selected);
  const rowsByFrame = new Map(rows.map((row) => [row.timeframe, row]));
  const consensus = useMemo(() => buildBotPrediction({
    symbol: selected,
    stocks: data.stocks,
    bestRows: data.screenerRows,
    analyses: data.timeframeAnalyses,
    lessons: [],
    question: "Create the final stock detail recommendation from all available strategy lenses.",
    dailyReport: "",
    dataStatus: data.backendStatus,
  }), [data, selected]);
  const dailySummary = useMemo(() => buildTechnicalSummary(dailyCandles), [dailyCandles]);
  const selectedVolumeAlerts = data.volumeAlerts.filter((alert) => alert.symbol === selected);
  const selectedSmartAlerts = data.smartAlerts.filter((alert) => alert.symbol === selected);
  const activeProvider = data.backendStatus?.activeProvider ?? data.sourceLabel;
  const providerTimestamp = best?.providerUpdatedAt ?? best?.lastUpdateEgypt;

  useEffect(() => {
    let cancelled = false;
    setDailyCandles([]);
    setChartStatus("Loading daily chart...");
    setTimeframeStatuses({});
    Promise.all(detailTimeframes.map(async (timeframe) => {
      try {
        const result = await fetchBackendCandlesResult(selected, timeframe);
        return [timeframe, result] as const;
      } catch (error) {
        return [timeframe, {
          status: "unavailable",
          source: "backend",
          reason: error instanceof Error ? error.message : `Unable to load ${timeframe} candles.`,
          candles: [],
        } satisfies BackendCandlesResult] as const;
      }
    }))
      .then((entries) => {
        if (cancelled) return;
        const nextStatuses = Object.fromEntries(entries) as Partial<Record<Timeframe, BackendCandlesResult>>;
        const dailyResult = nextStatuses["1D"];
        setTimeframeStatuses(nextStatuses);
        setDailyCandles(dailyResult?.candles ?? []);
        setChartStatus(dailyResult ? buildChartStatus(dailyResult) : "Unable to load daily candles.");
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  if (!best) {
    return (
      <div className="rounded-lg border border-terminal-border bg-terminal-card p-6">
        <h2 className="text-2xl font-bold text-white">{selected}</h2>
        <div className="mt-1 text-sm text-slate-400">{company.companyName} - {company.sector} - {company.market}</div>
        <p className="mt-4 max-w-3xl text-sm leading-6 text-slate-300">No symbol metadata is available for this stock.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-2xl font-bold text-white">{selected}</h2>
              <ActionBadge action={consensus.action} />
              <span className="rounded border border-cyan-400/30 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-300">Consensus {consensus.consensusScore}/100</span>
              <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-xs text-slate-300">Omar V3: {best.bestAction}</span>
              <span className="rounded border border-cyan-400/30 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-300">{best.bestFrame}</span>
              <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-xs text-slate-400">{best.dataQuality ?? "partial"} data</span>
            </div>
            <div className="mt-1 text-sm text-slate-400">{company.companyName} - {company.sector} - {company.market}</div>
            <div className="mt-3 text-xs font-bold text-slate-500">Final Consensus Recommendation</div>
            <p className="mt-3 max-w-4xl text-sm leading-6 text-slate-300">
              {consensus.recommendationReason} Entry {consensus.entryZone}, target {consensus.targets}, stop {consensus.stop}. Original Omar V3 row: {best.reason}
            </p>
          </div>
          <div className="grid min-w-[320px] grid-cols-2 gap-3 rounded border border-terminal-border bg-[#0C131D] p-3">
            <Metric label="Overall Score" value={`${best.overallScore}/10`} accent="text-emerald-300" />
            <Metric label="Provider Price" value={best.currentPrice ? money(best.currentPrice) : "-"} accent="text-white" />
            <Metric label="Change" value={best.changePercent !== undefined ? `${best.changePercent}%` : "-"} accent={(best.changePercent ?? 0) >= 0 ? "text-emerald-300" : "text-red-300"} />
            <Metric label="Consensus Confidence" value={`${consensus.confidence}%`} accent="text-cyan-300" />
            <Metric label="Reliability" value={`${consensus.dataReliability.grade} ${consensus.dataReliability.score}/100`} accent={consensus.dataReliability.grade === "High" ? "text-emerald-300" : consensus.dataReliability.grade === "Medium" ? "text-amber-300" : "text-red-300"} />
            <Metric label="Frames" value={consensus.dataReliability.frameCoverage} />
            <Metric label="Best Plan" value={best.plan} />
            <Metric label="Entry" value={best.entry ? money(best.entry) : "-"} />
            <Metric label="Risk/Reward" value={best.riskReward || "-"} accent="text-teal-300" />
            <Metric label="Bid" value={best.bid ? money(best.bid) : "-"} accent={best.orderBookStatus === "real" ? "text-emerald-300" : "text-slate-500"} />
            <Metric label="Ask" value={best.ask ? money(best.ask) : "-"} accent={best.orderBookStatus === "real" ? "text-red-300" : "text-slate-500"} />
            <Metric label="Spread" value={best.spreadPercent !== undefined ? `${best.spreadPercent}%` : "-"} />
            <Metric label="Book Status" value={best.orderBookStatus ?? "unavailable"} accent={best.orderBookStatus === "real" ? "text-emerald-300" : "text-slate-500"} />
          </div>
        </div>
        <div className="mt-4 rounded border border-cyan-400/25 bg-cyan-500/8 p-3">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="text-xs font-bold uppercase tracking-[0.08em] text-cyan-200">Price Source Audit</div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded border border-cyan-400/30 bg-cyan-500/10 px-2 py-1 font-semibold text-cyan-200">Provider: {activeProvider}</span>
                <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-slate-300">Provider price: {best.currentPrice ? money(best.currentPrice) : "Unavailable"}</span>
                <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-slate-300">Captured: {formatEgyptDateTime(providerTimestamp)}</span>
                <span className="rounded border border-amber-400/30 bg-amber-500/10 px-2 py-1 font-semibold text-amber-200">TradingView may differ</span>
              </div>
              <p className="mt-2 max-w-5xl text-xs leading-5 text-slate-400">
                The strategy table uses the active backend provider only. For {selected}, the current backend source is the EGX-AI-compatible Mubasher page model, which can lag or differ from TradingView/ICE displayed quotes. TradingView is kept display-only through official widgets and links; the app does not scrape its protected feed into rankings.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <a href={mubasherSymbolUrl(selected)} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center justify-center rounded border border-terminal-border bg-[#0B111A] px-3 text-xs font-semibold text-slate-200 hover:border-cyan-400/60">Open Provider Page</a>
              <a href={tradingViewSymbolUrl(selected)} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center justify-center rounded border border-cyan-400/40 bg-cyan-500/10 px-3 text-xs font-semibold text-cyan-100 hover:border-cyan-300">Open TradingView Reference</a>
            </div>
          </div>
        </div>
        <div className={`mt-4 rounded border p-3 text-sm leading-6 ${best.orderBookStatus === "real" ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-200" : "border-amber-400/30 bg-amber-500/10 text-amber-200"}`}>
          <div className="text-xs font-bold uppercase tracking-[0.08em]">Bid/Ask Expectation</div>
          <div className="mt-1">{best.bidAskExpectation ?? "Real bid/ask is unavailable from the active provider."}</div>
          {best.orderBookNote && <div className="mt-1 text-xs opacity-80">{best.orderBookNote}</div>}
        </div>
        <div className={`mt-4 rounded border p-3 text-sm leading-6 ${consensus.dataReliability.score >= 78 ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-200" : consensus.dataReliability.score >= 52 ? "border-amber-400/30 bg-amber-500/10 text-amber-200" : "border-red-400/30 bg-red-500/10 text-red-100"}`}>
          <div className="text-xs font-bold uppercase tracking-[0.08em]">Data Reliability</div>
          <div className="mt-1">{consensus.dataReliability.note}</div>
          <div className="mt-1 text-xs opacity-80">
            Latest Egypt date: {consensus.dataReliability.latestDateEgypt}. Frame coverage: {consensus.dataReliability.frameCoverage}. Real bid/ask: {data.backendStatus?.bidAskStatus ?? "unavailable"}.
          </div>
        </div>
        {best.dataQuality === "unavailable" && (
          <div className="mt-4 rounded border border-red-400/30 bg-red-500/10 p-3 text-sm leading-6 text-red-100">
            <div className="text-xs font-bold uppercase tracking-[0.08em] text-red-200">No Provider Coverage For {selected}</div>
            <div className="mt-1">{best.reason}</div>
            <div className="mt-1 text-xs text-red-100/80">
              The symbol remains in the EGX universe and the system will keep it visible, but it will not create fake candles, fake bid/ask, or fake alerts. Add a licensed/supplemental data provider to analyze this ticker.
            </div>
          </div>
        )}
      </section>

      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1.45fr)_minmax(420px,0.85fr)]">
        <CandleVolumeChart candles={dailyCandles} symbol={selected} />
        <div className="space-y-4">
          <SmartAlertsPanel alerts={selectedSmartAlerts} compact />
          <TechnicalSummaryPanel summary={dailySummary} />
          <VolumeAlertsPanel alerts={selectedVolumeAlerts} compact />
          <div className="rounded-lg border border-terminal-border bg-terminal-card p-3 text-xs text-slate-500">{chartStatus}</div>
        </div>
      </div>

      <TradingViewOfficialWidget symbol={selected} />

      <section className="rounded-lg border border-terminal-border bg-terminal-card p-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-bold uppercase tracking-[0.08em] text-slate-500">Timeframe Coverage</span>
          {detailTimeframes.map((timeframe) => {
            const hasAnalysis = rowsByFrame.has(timeframe);
            const status = hasAnalysis ? "analysis ready" : (timeframeStatuses[timeframe]?.status ?? "checking");
            const tone = hasAnalysis ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-300" : status === "checking" ? "border-slate-400/30 bg-slate-500/10 text-slate-300" : "border-amber-400/30 bg-amber-500/10 text-amber-200";
            return <span key={timeframe} className={`rounded border px-2 py-1 ${tone}`}>{timeframe}: {status}</span>;
          })}
        </div>
        <p className="mt-2 text-xs leading-5 text-slate-400">
          The active free provider is delayed daily data. Intraday frames require a licensed provider, webhook candles, or CSV/API candles with true intraday timestamps.
        </p>
      </section>

      <div className="grid gap-3 xl:grid-cols-5">
        {detailTimeframes.map((timeframe) => {
          const row = rowsByFrame.get(timeframe);
          return row ? <TimeframePanel key={timeframe} row={row} /> : <UnavailableTimeframePanel key={timeframe} timeframe={timeframe} result={timeframeStatuses[timeframe]} />;
        })}
      </div>
    </div>
  );
}
