import Metric from "./Metric";
import type { TechnicalSummary } from "../lib/analysis";
import { compact, money } from "../lib/format";

export default function TechnicalSummaryPanel({ summary }: { summary: TechnicalSummary | null }) {
  if (!summary) {
    return (
      <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
        <h2 className="text-base font-bold text-white">Technical Analysis</h2>
        <div className="mt-4 rounded border border-terminal-border bg-[#0C131D] p-4 text-sm text-slate-400">
          Not enough candles to calculate the full technical set.
        </div>
      </section>
    );
  }

  const macdTone = summary.macdHistogram >= 0 ? "text-emerald-300" : "text-red-300";
  const volumeTone = summary.volumeDirection === "Accumulation" ? "text-emerald-300" : summary.volumeDirection === "Distribution" ? "text-red-300" : "text-cyan-300";

  return (
    <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-bold text-white">Technical Analysis</h2>
        <span className={`rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-xs font-semibold ${volumeTone}`}>
          {summary.volumeDirection}
        </span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="SMA 20" value={money(summary.sma20)} />
        <Metric label="SMA 50" value={money(summary.sma50)} />
        <Metric label="SMA 200" value={money(summary.sma200)} />
        <Metric label="EMA 9" value={money(summary.ema9)} accent="text-sky-300" />
        <Metric label="EMA 21" value={money(summary.ema21)} accent="text-amber-300" />
        <Metric label="RSI 14" value={summary.rsi14} />
        <Metric label="ATR 14" value={money(summary.atr14)} />
        <Metric label="Vol Avg 20" value={compact(summary.volumeAverage20)} />
        <Metric label="MACD" value={summary.macd} />
        <Metric label="Signal" value={summary.macdSignal} />
        <Metric label="Histogram" value={summary.macdHistogram} accent={macdTone} />
        <Metric label="Support" value={money(summary.support)} accent="text-red-300" />
        <Metric label="BB Upper" value={money(summary.bollingerUpper)} />
        <Metric label="BB Middle" value={money(summary.bollingerMiddle)} />
        <Metric label="BB Lower" value={money(summary.bollingerLower)} />
        <Metric label="Resistance" value={money(summary.resistance)} accent="text-emerald-300" />
      </div>
      <div className={`mt-4 rounded border px-3 py-2 text-sm ${summary.volumeDirection === "Accumulation" ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-200" : summary.volumeDirection === "Distribution" ? "border-red-400/30 bg-red-500/10 text-red-200" : "border-cyan-400/30 bg-cyan-500/10 text-cyan-200"}`}>
        {summary.volumeAlert}
      </div>
    </section>
  );
}
