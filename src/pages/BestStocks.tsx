import { useEffect, useMemo, useState, type ReactNode } from "react";
import { BadgeDollarSign, Database, Medal, ShieldAlert } from "lucide-react";
import ActionBadge from "../components/ActionBadge";
import BestStockCard from "../components/BestStockCard";
import FilterToolbar, { FilterInput, FilterSelect } from "../components/FilterToolbar";
import { timeframes, type MarketDataSnapshot } from "../data/mockData";
import { fetchGoldMarketContext, type GoldMarketContext } from "../lib/dataAdapters";
import { money } from "../lib/format";

function formatChange(value?: number) {
  if (value === undefined) return "-";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function ScoreCard({
  label,
  value,
  detail,
  tone,
  icon,
}: {
  label: string;
  value: string | number;
  detail: string;
  tone: string;
  icon: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-normal text-slate-500">{label}</div>
          <div className="mt-1 text-2xl font-bold text-white">{value}</div>
        </div>
        <div className={`grid h-9 w-9 place-items-center rounded ${tone}`}>{icon}</div>
      </div>
      <div className="mt-3 text-xs leading-5 text-slate-400">{detail}</div>
    </section>
  );
}

export default function BestStocks({ data, onOpenStock }: { data: MarketDataSnapshot; onOpenStock: (symbol: string) => void }) {
  const { bestStocks } = data;
  const [goldContext, setGoldContext] = useState<GoldMarketContext | null>(null);
  const [query, setQuery] = useState("");
  const [action, setAction] = useState("All");
  const [frame, setFrame] = useState("All");
  const [plan, setPlan] = useState("All");
  const [minScore, setMinScore] = useState(0);
  const [pressure, setPressure] = useState("All");
  const [volume, setVolume] = useState("All");

  const rows = useMemo(() => bestStocks.filter((row) =>
    `${row.symbol} ${row.companyName}`.toLowerCase().includes(query.toLowerCase()) &&
    (action === "All" || row.bestAction === action) &&
    (frame === "All" || row.bestFrame === frame) &&
    (plan === "All" || row.plan === plan) &&
    row.overallScore >= minScore &&
    (pressure === "All" || row.pressure === pressure) &&
    (volume === "All" || row.volumeStatus === volume)
  ), [bestStocks, query, action, frame, plan, minScore, pressure, volume]);
  const topScore = bestStocks[0]?.overallScore ?? 0;
  const highAlerts = data.smartAlerts.filter((alert) => alert.severity === "High").length;
  const unavailableCount = data.screenerRows.filter((row) => row.dataQuality === "unavailable").length;
  const pricedCount = data.screenerRows.length - unavailableCount;

  useEffect(() => {
    let cancelled = false;
    fetchGoldMarketContext()
      .then((context) => {
        if (!cancelled) setGoldContext(context);
      })
      .catch((error) => {
        if (!cancelled) {
          setGoldContext({ status: "unavailable", source: "public-yahoo-chart", reason: error instanceof Error ? error.message : "Gold context unavailable" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const gold = goldContext?.data;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <ScoreCard label="Top Score" value={`${topScore}/10`} detail={bestStocks[0] ? `${bestStocks[0].symbol} leads the ranking from available provider candles.` : "No ranked stocks from provider candles yet."} icon={<Medal size={18} />} tone="bg-emerald-500/15 text-emerald-300" />
        <ScoreCard label="Stock Audit" value={`${pricedCount}/${data.stocks.length}`} detail={`${unavailableCount} symbols have no usable candles from the active free provider.`} icon={<Database size={18} />} tone="bg-cyan-500/15 text-cyan-300" />
        <ScoreCard label="High Alerts" value={highAlerts} detail="Smart early alarms ranked by pressure, volume, zone distance, and risk." icon={<ShieldAlert size={18} />} tone="bg-amber-500/15 text-amber-300" />
        <ScoreCard
          label="Gold USD"
          value={gold ? `$${money(gold.price)}` : "-"}
          detail={gold ? `${gold.label} ${gold.symbol} (${gold.exchange}) ${formatChange(gold.changePercent)}. ${goldContext?.reason}` : goldContext?.reason ?? "Loading public USD gold context..."}
          icon={<BadgeDollarSign size={18} />}
          tone="bg-yellow-500/15 text-yellow-300"
        />
      </div>

      <FilterToolbar>
        <FilterInput placeholder="Search symbol/company" value={query} onChange={(e) => setQuery(e.target.value)} />
        <FilterSelect value={action} onChange={(e) => setAction(e.target.value)}><option>All</option><option>BUY NOW</option><option>BREAKOUT BUY</option><option>WAIT PULLBACK</option><option>WATCH EARLY BUY</option><option>DO NOT BUY NOW</option></FilterSelect>
        <FilterSelect value={frame} onChange={(e) => setFrame(e.target.value)}><option>All</option>{timeframes.map((tf) => <option key={tf}>{tf}</option>)}</FilterSelect>
        <FilterSelect value={plan} onChange={(e) => setPlan(e.target.value)}><option>All</option><option>BUY & HOLD</option><option>SWING TRADE</option><option>SCALP ONLY</option><option>WAIT</option></FilterSelect>
        <FilterSelect value={pressure} onChange={(e) => setPressure(e.target.value)}><option>All</option><option>Buy Pressure</option><option>Sell Pressure</option><option>Neutral</option></FilterSelect>
        <FilterSelect value={volume} onChange={(e) => setVolume(e.target.value)}><option>All</option><option>Very Strong</option><option>Strong</option><option>Normal</option><option>Weak</option></FilterSelect>
        <label className="flex h-9 items-center gap-2 rounded border border-terminal-border bg-terminal-card px-3 text-xs text-slate-400">Min score <input type="number" min={0} max={10} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} className="w-14 bg-transparent text-slate-100 outline-none" /></label>
      </FilterToolbar>

      <div className="grid gap-3 xl:grid-cols-3">
        {rows.slice(0, 6).map((stock) => <BestStockCard key={stock.symbol} stock={stock} onOpen={onOpenStock} />)}
      </div>
      {!rows.length && <div className="rounded-lg border border-terminal-border bg-terminal-card p-5 text-sm text-slate-400">No ranked stocks yet. Rankings will appear after provider candles are received.</div>}

      <div className="overflow-hidden rounded-lg border border-terminal-border bg-terminal-card">
        <div className="overflow-auto">
          <table className="w-full min-w-[1250px] text-left text-xs">
            <thead className="sticky top-0 bg-[#101A27] text-[11px] uppercase text-slate-400">
              <tr>{["Rank", "Symbol", "Company", "Best Action", "Best Frame", "Overall Score", "Plan", "Entry", "Target", "Stop", "R/R", "Pressure", "Volume Status", "Reason", "Last Update"].map((h) => <th key={h} className="border-b border-terminal-border px-3 py-2">{h}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.symbol} className="border-b border-terminal-border/70 hover:bg-white/[0.03]">
                  <td className="px-3 py-2 text-slate-400">#{row.rank}</td>
                  <td className="px-3 py-2 font-bold text-white"><button onClick={() => onOpenStock(row.symbol)} className="hover:text-teal-300">{row.symbol}</button></td>
                  <td className="px-3 py-2 text-slate-300">{row.companyName}</td>
                  <td className="px-3 py-2"><ActionBadge action={row.bestAction} /></td>
                  <td className="px-3 py-2 text-cyan-300">{row.bestFrame}</td>
                  <td className="px-3 py-2 font-semibold text-emerald-300">{row.overallScore}</td>
                  <td className="px-3 py-2 text-slate-300">{row.plan}</td>
                  <td className="px-3 py-2">{money(row.entry)}</td>
                  <td className="px-3 py-2 text-emerald-300">{money(row.target)}</td>
                  <td className="px-3 py-2 text-red-300">{money(row.stop)}</td>
                  <td className="px-3 py-2 text-teal-300">{row.riskReward}</td>
                  <td className="px-3 py-2">{row.pressure}</td>
                  <td className="px-3 py-2">{row.volumeStatus}</td>
                  <td className="max-w-[320px] px-3 py-2 text-slate-400">{row.reason}</td>
                  <td className="px-3 py-2 text-slate-500">{row.lastUpdateEgypt}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
