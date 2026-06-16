import { useMemo, useState } from "react";
import FilterToolbar, { FilterInput, FilterSelect } from "../components/FilterToolbar";
import ScreenerTable from "../components/ScreenerTable";
import { timeframes, type MarketDataSnapshot } from "../data/mockData";
import type { TimeframeAnalysis } from "../types";

export default function Screener({ data, onOpenStock }: { data: MarketDataSnapshot; onOpenStock: (symbol: string) => void }) {
  const { screenerRows, timeframeAnalyses } = data;
  const [query, setQuery] = useState("");
  const [tf, setTf] = useState("All");
  const [action, setAction] = useState("All");
  const [trend, setTrend] = useState("All");
  const [plan, setPlan] = useState("All");
  const [score, setScore] = useState(0);
  const [pressure, setPressure] = useState("All");
  const [volume, setVolume] = useState("All");

  const rows = useMemo(() => screenerRows.filter((row) => {
    const map = Object.fromEntries(timeframeAnalyses.filter((item) => item.symbol === row.symbol).map((item) => [item.timeframe, item])) as Partial<Record<"15M" | "30M" | "1H" | "4H" | "1D", TimeframeAnalysis>>;
    const scoped = (tf === "All" ? Object.values(map) : [map[tf as keyof typeof map]]).filter(Boolean) as TimeframeAnalysis[];
    const technicalMatch = scoped.length
      ? scoped.some((item) => (action === "All" || item.actionNow === action) && (trend === "All" || item.mainTrend === trend) && item.score >= score)
      : action === "All" && trend === "All" && score <= row.overallScore;
    return `${row.symbol} ${row.companyName}`.toLowerCase().includes(query.toLowerCase()) &&
      technicalMatch &&
      (plan === "All" || row.plan === plan) &&
      (pressure === "All" || row.pressure === pressure) &&
      (volume === "All" || row.volumeStatus === volume);
  }), [screenerRows, timeframeAnalyses, query, tf, action, trend, plan, score, pressure, volume]);

  const trends = Array.from(new Set(timeframeAnalyses.map((row) => row.mainTrend)));

  return (
    <div>
      <FilterToolbar>
        <FilterInput placeholder="Search symbol/company" value={query} onChange={(e) => setQuery(e.target.value)} />
        <FilterSelect value={tf} onChange={(e) => setTf(e.target.value)}><option>All</option>{timeframes.map((item) => <option key={item}>{item}</option>)}</FilterSelect>
        <FilterSelect value={action} onChange={(e) => setAction(e.target.value)}><option>All</option><option>BUY NOW</option><option>BREAKOUT BUY</option><option>PULLBACK BUY AREA</option><option>WATCH EARLY BUY</option><option>WAIT PULLBACK</option><option>HOLD</option><option>SELL NOW</option><option>DO NOT BUY NOW</option></FilterSelect>
        <FilterSelect value={trend} onChange={(e) => setTrend(e.target.value)}><option>All</option>{trends.map((item) => <option key={item}>{item}</option>)}</FilterSelect>
        <FilterSelect value={plan} onChange={(e) => setPlan(e.target.value)}><option>All</option><option>BUY & HOLD</option><option>SWING TRADE</option><option>SCALP ONLY</option><option>WAIT</option></FilterSelect>
        <FilterSelect value={pressure} onChange={(e) => setPressure(e.target.value)}><option>All</option><option>Buy Pressure</option><option>Sell Pressure</option><option>Neutral</option></FilterSelect>
        <FilterSelect value={volume} onChange={(e) => setVolume(e.target.value)}><option>All</option><option>Very Strong</option><option>Strong</option><option>Normal</option><option>Weak</option></FilterSelect>
        <label className="flex h-9 items-center gap-2 rounded border border-terminal-border bg-terminal-card px-3 text-xs text-slate-400">Score <input type="number" min={0} max={10} value={score} onChange={(e) => setScore(Number(e.target.value))} className="w-14 bg-transparent text-slate-100 outline-none" /></label>
      </FilterToolbar>
      <ScreenerTable rows={rows} analyses={timeframeAnalyses} onOpen={onOpenStock} />
    </div>
  );
}
