import ActionBadge from "./ActionBadge";
import Metric from "./Metric";
import type { BestStock } from "../types";
import { money } from "../lib/format";

export default function BestStockCard({ stock, onOpen }: { stock: BestStock; onOpen: (symbol: string) => void }) {
  return (
    <article className="rounded-lg border border-terminal-border bg-terminal-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <button onClick={() => onOpen(stock.symbol)} className="text-left text-lg font-bold text-white hover:text-teal-300">{stock.symbol}</button>
          <div className="truncate text-xs text-slate-400">{stock.companyName}</div>
        </div>
        <ActionBadge action={stock.bestAction} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <Metric label="Frame" value={stock.bestFrame} accent="text-cyan-300" />
        <Metric label="Score" value={`${stock.overallScore}/10`} accent="text-emerald-300" />
        <Metric label="Plan" value={stock.plan} />
        <Metric label="R/R" value={stock.riskReward} accent="text-teal-300" />
        <Metric label="Entry" value={money(stock.entry)} />
        <Metric label="Target" value={money(stock.target)} accent="text-emerald-300" />
        <Metric label="Stop" value={money(stock.stop)} accent="text-red-300" />
        <Metric label="Pressure" value={stock.pressure} />
      </div>
      <div className="mt-3 rounded border border-terminal-border bg-[#0C131D] p-3 text-xs leading-5 text-slate-400">{stock.reason}</div>
    </article>
  );
}
