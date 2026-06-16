import { Activity, ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { VolumeDirectionAlert } from "../types";

function iconFor(direction: VolumeDirectionAlert["direction"]) {
  if (direction === "Accumulation") return <ArrowUpRight size={16} />;
  if (direction === "Distribution") return <ArrowDownRight size={16} />;
  return <Activity size={16} />;
}

function toneFor(direction: VolumeDirectionAlert["direction"]) {
  if (direction === "Accumulation") return "border-emerald-400/30 bg-emerald-500/10 text-emerald-200";
  if (direction === "Distribution") return "border-red-400/30 bg-red-500/10 text-red-200";
  return "border-cyan-400/30 bg-cyan-500/10 text-cyan-200";
}

export default function VolumeAlertsPanel({ alerts, onOpenStock, compact = false }: { alerts: VolumeDirectionAlert[]; onOpenStock?: (symbol: string) => void; compact?: boolean }) {
  return (
    <section className="rounded-lg border border-terminal-border bg-terminal-card">
      <div className="flex items-center justify-between border-b border-terminal-border px-3 py-2">
        <h2 className="text-sm font-bold text-white">Volume Direction Alerts</h2>
        <span className="text-[11px] text-slate-500">Pressure + volume MA 20</span>
      </div>
      <div className={compact ? "divide-y divide-terminal-border/70" : "grid gap-2 p-3 lg:grid-cols-2"}>
        {alerts.slice(0, compact ? 5 : 10).map((alert) => (
          <button
            key={alert.id}
            onClick={() => onOpenStock?.(alert.symbol)}
            className={`w-full rounded border px-3 py-2 text-left transition hover:brightness-110 ${toneFor(alert.direction)}`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-xs font-bold">
                {iconFor(alert.direction)}
                <span>{alert.symbol}</span>
                <span className="text-[11px] text-slate-400">{alert.timeframe}</span>
              </div>
              <span className="rounded bg-black/20 px-2 py-0.5 text-[10px] font-bold">{alert.severity}</span>
            </div>
            <div className="mt-1 truncate text-xs opacity-90">{alert.companyName}</div>
            <div className="mt-2 text-xs leading-5 text-slate-200">{alert.message}</div>
          </button>
        ))}
        {!alerts.length && <div className="p-4 text-sm text-slate-500">No strong volume-direction alerts from the current provider candles.</div>}
      </div>
    </section>
  );
}
