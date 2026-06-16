import { useMemo, useState } from "react";
import ActionBadge from "../components/ActionBadge";
import FilterToolbar, { FilterInput, FilterSelect } from "../components/FilterToolbar";
import SmartAlertsPanel from "../components/SmartAlertsPanel";
import { timeframes, type MarketDataSnapshot } from "../data/mockData";
import { money } from "../lib/format";

export default function Signals({ data }: { data: MarketDataSnapshot }) {
  const { signals } = data;
  const [symbol, setSymbol] = useState("");
  const [action, setAction] = useState("All");
  const [tf, setTf] = useState("All");
  const [date, setDate] = useState("");
  const [score, setScore] = useState(0);

  const rows = useMemo(() => signals.filter((row) =>
    row.symbol.toLowerCase().includes(symbol.toLowerCase()) &&
    (action === "All" || row.action === action) &&
    (tf === "All" || row.timeframe === tf) &&
    (!date || row.createdAtEgypt.startsWith(date) || row.createdAtEgypt.includes(date.split("-").reverse().join("/"))) &&
    row.score >= score
  ), [signals, symbol, action, tf, date, score]);

  return (
    <div className="space-y-4">
      <SmartAlertsPanel alerts={data.smartAlerts} compact />
      <FilterToolbar>
        <FilterInput placeholder="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
        <FilterSelect value={action} onChange={(e) => setAction(e.target.value)}><option>All</option><option>BUY NOW</option><option>BREAKOUT BUY</option><option>PULLBACK BUY AREA</option><option>WATCH EARLY BUY</option><option>SELL NOW</option><option>DO NOT BUY NOW</option></FilterSelect>
        <FilterSelect value={tf} onChange={(e) => setTf(e.target.value)}><option>All</option>{timeframes.map((item) => <option key={item}>{item}</option>)}</FilterSelect>
        <FilterInput type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <label className="flex h-9 items-center gap-2 rounded border border-terminal-border bg-terminal-card px-3 text-xs text-slate-400">Score <input type="number" min={0} max={10} value={score} onChange={(e) => setScore(Number(e.target.value))} className="w-14 bg-transparent text-slate-100 outline-none" /></label>
      </FilterToolbar>
      <div className="overflow-hidden rounded-lg border border-terminal-border bg-terminal-card">
        <div className="overflow-auto">
          <table className="w-full min-w-[980px] text-left text-xs">
            <thead className="sticky top-0 bg-[#101A27] text-[11px] uppercase text-slate-400">
              <tr>{["Time Egypt", "Symbol", "Timeframe", "Signal Type", "Action", "Price", "Score", "Message"].map((h) => <th key={h} className="border-b border-terminal-border px-3 py-2">{h}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-terminal-border/70 hover:bg-white/[0.03]">
                  <td className="px-3 py-2 text-slate-500">{row.createdAtEgypt}</td>
                  <td className="px-3 py-2 font-bold text-white">{row.symbol}</td>
                  <td className="px-3 py-2 text-cyan-300">{row.timeframe}</td>
                  <td className="px-3 py-2 text-slate-300">{row.signalType}</td>
                  <td className="px-3 py-2"><ActionBadge action={row.action} /></td>
                  <td className="px-3 py-2">{money(row.price)}</td>
                  <td className="px-3 py-2 text-emerald-300">{row.score}</td>
                  <td className="px-3 py-2 text-slate-400">{row.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && <div className="p-5 text-sm text-slate-400">No signals match the current filters.</div>}
        </div>
      </div>
    </div>
  );
}
