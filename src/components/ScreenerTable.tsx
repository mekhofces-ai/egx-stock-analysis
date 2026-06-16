import ActionBadge from "./ActionBadge";
import type { BestStock, TimeframeAnalysis } from "../types";
import { money } from "../lib/format";

function valueOrDash(value: number | undefined) {
  return value && value > 0 ? money(value) : "-";
}

function statusText(row: BestStock, bestAnalysis?: TimeframeAnalysis) {
  if (bestAnalysis) return money(bestAnalysis.currentPrice);
  if (row.currentPrice) return money(row.currentPrice);
  return "Unavailable";
}

export default function ScreenerTable({ rows, analyses, onOpen, compact = false }: { rows: BestStock[]; analyses: TimeframeAnalysis[]; onOpen: (symbol: string) => void; compact?: boolean }) {
  return (
    <div className="overflow-hidden rounded-lg border border-terminal-border bg-terminal-card">
      <div className="max-h-[calc(100vh-210px)] overflow-auto">
        <table className="w-full min-w-[1500px] border-collapse text-left text-xs">
          <thead className="sticky top-0 z-10 bg-[#101A27] text-[11px] uppercase text-slate-400">
            <tr>
              {["Symbol", "Company", "Price", "Bid", "Ask", "Spread", "Book", "Best Action", "Best Frame", "Score", "Plan", "15M", "30M", "1H", "4H", "1D", "Trend", "Pressure", "Volume", "Entry", "Target", "Stop", "RSI", "ATR", "R/R", "Last Update"].slice(0, compact ? 21 : 26).map((head) => (
                <th key={head} className="border-b border-terminal-border px-3 py-2 font-semibold">{head}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const map = Object.fromEntries(analyses.filter((item) => item.symbol === row.symbol).map((item) => [item.timeframe, item])) as Partial<Record<"15M" | "30M" | "1H" | "4H" | "1D", TimeframeAnalysis>>;
              const bestFrame = ["15M", "30M", "1H", "4H", "1D"].includes(row.bestFrame) ? row.bestFrame as "15M" | "30M" | "1H" | "4H" | "1D" : "1D";
              const bestAnalysis = map[bestFrame];
              const unavailable = !bestAnalysis && row.dataQuality === "unavailable";
              return (
                <tr key={row.symbol} className="border-b border-terminal-border/70 hover:bg-white/[0.03]">
                  <td className="px-3 py-2 font-bold text-white"><button onClick={() => onOpen(row.symbol)} className="hover:text-teal-300">{row.symbol}</button></td>
                  <td className="max-w-[220px] truncate px-3 py-2 text-slate-300">{row.companyName}</td>
                  <td className={unavailable ? "px-3 py-2 text-slate-500" : "px-3 py-2 text-slate-100"}>{statusText(row, bestAnalysis)}</td>
                  <td className="px-3 py-2 text-slate-300">{row.bid ? money(row.bid) : "-"}</td>
                  <td className="px-3 py-2 text-slate-300">{row.ask ? money(row.ask) : "-"}</td>
                  <td className="px-3 py-2 text-slate-400">{row.spreadPercent !== undefined ? `${row.spreadPercent}%` : "-"}</td>
                  <td className={row.orderBookStatus === "real" ? "px-3 py-2 text-emerald-300" : "px-3 py-2 text-slate-500"}>{row.orderBookStatus ?? "unavailable"}</td>
                  <td className="px-3 py-2"><ActionBadge action={row.bestAction} /></td>
                  <td className="px-3 py-2 text-cyan-300">{unavailable ? "-" : row.bestFrame}</td>
                  <td className={unavailable ? "px-3 py-2 text-slate-600" : "px-3 py-2 font-semibold text-emerald-300"}>{row.overallScore}</td>
                  <td className="px-3 py-2 text-slate-300">{row.plan}</td>
                  {(["15M", "30M", "1H", "4H", "1D"] as const).map((tf) => {
                    const frame = map[tf];
                    return (
                      <td key={tf} className="px-3 py-2">
                        {frame ? (
                          <>
                            <span className="mr-2 text-slate-400">{frame.score}</span>
                            <ActionBadge action={frame.actionNow} />
                          </>
                        ) : (
                          <span className="text-slate-600">No data</span>
                        )}
                      </td>
                    );
                  })}
                  <td className="px-3 py-2 text-slate-300">{bestAnalysis?.mainTrend ?? "No data"}</td>
                  <td className="px-3 py-2 text-slate-300">{unavailable ? "No data" : row.pressure}</td>
                  <td className="px-3 py-2 text-slate-300">{unavailable ? "No data" : row.volumeStatus}</td>
                  <td className="px-3 py-2 text-slate-100">{valueOrDash(row.entry)}</td>
                  <td className="px-3 py-2 text-emerald-300">{valueOrDash(row.target)}</td>
                  <td className="px-3 py-2 text-red-300">{valueOrDash(row.stop)}</td>
                  <td className="px-3 py-2 text-slate-300">{bestAnalysis?.rsi ?? "-"}</td>
                  {!compact && <td className="px-3 py-2 text-slate-300">{bestAnalysis?.atr ?? "-"}</td>}
                  {!compact && <td className="px-3 py-2 text-teal-300">{row.riskReward || "-"}</td>}
                  {!compact && <td className="px-3 py-2 text-slate-500">{row.lastUpdateEgypt}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length && <div className="p-5 text-sm text-slate-400">No EGX rows match the current filters.</div>}
      </div>
    </div>
  );
}
