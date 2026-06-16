import ActionBadge from "./ActionBadge";
import Metric from "./Metric";
import type { TimeframeAnalysis } from "../types";
import { money } from "../lib/format";

export default function TimeframePanel({ row }: { row: TimeframeAnalysis }) {
  return (
    <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-lg font-bold text-white">{row.timeframe}</div>
        <ActionBadge action={row.actionNow} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <Metric label="Trend" value={row.mainTrend} />
        <Metric label="Plan" value={row.plan} />
        <Metric label="Score" value={`${row.score}/10`} accent="text-emerald-300" />
        <Metric label="Pressure" value={row.pressure} />
        <Metric label="Volume" value={row.volumeStatus} />
        <Metric label="RSI" value={row.rsi} />
        <Metric label="ATR" value={row.atr} />
        <Metric label="EMA 21" value={money(row.ema21)} />
        <Metric label="EMA 50" value={money(row.ema50)} />
        <Metric label="EMA 200" value={money(row.ema200)} />
        <Metric label="Fast Filter" value={money(row.fastRangeFilter)} accent={row.currentPrice > row.fastRangeFilter ? "text-emerald-300" : "text-red-300"} />
        <Metric label="Slow Filter" value={money(row.slowRangeFilter)} accent={row.currentPrice > row.slowRangeFilter ? "text-emerald-300" : "text-red-300"} />
        <Metric label="Buy Zone" value={`${money(row.buyZoneLow)} - ${money(row.buyZoneHigh)}`} />
        <Metric label="Entry" value={money(row.suggestedEntry)} />
        <Metric label="Target" value={money(row.suggestedTarget)} accent="text-emerald-300" />
        <Metric label="Stop" value={money(row.suggestedStop)} accent="text-red-300" />
        <Metric label="Risk/Reward" value={row.riskReward} accent="text-teal-300" />
        {row.activeTarget !== undefined && <Metric label="Active Target" value={row.activeTarget ? money(row.activeTarget) : "No active trade"} accent="text-emerald-300" />}
        {row.activeStop !== undefined && <Metric label="Active Stop" value={row.activeStop ? money(row.activeStop) : "No active trade"} accent="text-orange-300" />}
        {row.positionState && <Metric label="Position" value={row.positionState} accent={row.positionState === "IN TRADE" ? "text-emerald-300" : "text-slate-400"} />}
        {row.signalMode && <Metric label="Mode" value={row.signalMode} />}
        <Metric label="Candle Time" value={row.candleTimeEgypt} />
      </div>
      {row.advice && (
        <div className="mt-4 rounded border border-teal-400/25 bg-teal-500/8 p-3 text-xs leading-5 text-teal-100">
          <span className="font-bold text-teal-300">Omar Smart PRO V3 Advice: </span>{row.advice}
        </div>
      )}
      <div className="mt-4 grid grid-cols-3 gap-2 text-center text-[11px]">
        <div className={`rounded border px-2 py-2 ${row.breakoutStatus ? "border-emerald-400/40 bg-emerald-500/10 text-emerald-300" : "border-terminal-border text-slate-500"}`}>Breakout</div>
        <div className={`rounded border px-2 py-2 ${row.pullbackStatus ? "border-amber-400/40 bg-amber-500/10 text-amber-300" : "border-terminal-border text-slate-500"}`}>Pullback</div>
        <div className={`rounded border px-2 py-2 ${row.earlyAccumulationStatus ? "border-cyan-400/40 bg-cyan-500/10 text-cyan-300" : "border-terminal-border text-slate-500"}`}>Early Accum.</div>
      </div>
    </section>
  );
}
