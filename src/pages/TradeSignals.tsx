import { useEffect, useMemo, useState } from "react";
import { BellRing, RefreshCw, ShieldAlert, Target, TrendingDown, TrendingUp } from "lucide-react";
import ActionBadge from "../components/ActionBadge";
import FilterToolbar, { FilterInput, FilterSelect } from "../components/FilterToolbar";
import Metric from "../components/Metric";
import type { MarketDataSnapshot } from "../data/mockData";
import { egyptNow, money } from "../lib/format";
import type { ActionNow, BestStock, TimeframeAnalysis } from "../types";

type SignalSide = "BUY" | "WATCH" | "SELL" | "AVOID";

type TradeSignalRow = {
  id: string;
  symbol: string;
  companyName: string;
  timeframe: TimeframeAnalysis["timeframe"];
  side: SignalSide;
  action: ActionNow;
  currentPrice: number;
  bestBuyPoint: number;
  bestSellPoint: number;
  takeProfit: number;
  stopLoss: number;
  score: number;
  urgency: number;
  confidence: number;
  trend: string;
  plan: string;
  pressure: string;
  volumeStatus: string;
  riskReward: number;
  buyReason: string;
  sellReason: string;
  strategyReason: string;
  dataQuality: BestStock["dataQuality"];
  orderBookStatus: BestStock["orderBookStatus"];
  providerUpdatedAt?: string;
  lastUpdateEgypt: string;
};

const buyActions: ActionNow[] = ["BUY NOW", "BREAKOUT BUY", "PULLBACK BUY AREA"];
const watchActions: ActionNow[] = ["WATCH EARLY BUY", "WAIT PULLBACK", "WATCH", "HOLD"];
const sellActions: ActionNow[] = ["SELL NOW", "REDUCE / TAKE PROFIT"];

function actionScore(action: ActionNow) {
  if (action === "BUY NOW") return 34;
  if (action === "BREAKOUT BUY") return 32;
  if (action === "PULLBACK BUY AREA") return 30;
  if (action === "WATCH EARLY BUY") return 22;
  if (action === "WAIT PULLBACK") return 16;
  if (action === "HOLD") return 10;
  if (action === "WATCH") return 8;
  if (action === "REDUCE / TAKE PROFIT") return 6;
  if (action === "SELL NOW") return 28;
  if (action === "DO NOT BUY NOW") return 10;
  return 0;
}

function sideFor(row: TimeframeAnalysis): SignalSide {
  if (sellActions.includes(row.actionNow)) return "SELL";
  if (row.actionNow === "DO NOT BUY NOW" || row.pressure === "Sell Pressure" && row.mainTrend === "BEARISH") return "AVOID";
  if (buyActions.includes(row.actionNow) && row.pressure === "Buy Pressure" && row.score >= 6 && row.riskReward >= 1) return "BUY";
  if (watchActions.includes(row.actionNow) || row.pressure === "Buy Pressure") return "WATCH";
  return "AVOID";
}

function sideTone(side: SignalSide) {
  if (side === "BUY") return "border-emerald-400/35 bg-emerald-500/10 text-emerald-300";
  if (side === "WATCH") return "border-amber-400/35 bg-amber-500/10 text-amber-300";
  if (side === "SELL") return "border-red-400/35 bg-red-500/10 text-red-300";
  return "border-slate-400/30 bg-slate-500/10 text-slate-300";
}

function signalIcon(side: SignalSide) {
  if (side === "BUY") return TrendingUp;
  if (side === "SELL") return TrendingDown;
  if (side === "AVOID") return ShieldAlert;
  return BellRing;
}

function buildBuyReason(row: TimeframeAnalysis) {
  if (row.pressure !== "Buy Pressure") return "No buy trigger: buyer pressure is not confirmed.";
  if (buyActions.includes(row.actionNow)) {
    return `${row.actionNow}: price is near strategy entry zone with ${row.score}/10 score, ${row.mainTrend.toLowerCase()}, and ${row.volumeStatus.toLowerCase()} volume.`;
  }
  if (row.earlyAccumulationStatus) {
    return `Early accumulation: buyers are active, score is ${row.score}/10, but confirmation is still weaker than a direct buy.`;
  }
  return `Watch only: buyer pressure exists, but the setup still needs stronger confirmation or a better pullback.`;
}

function buildSellReason(row: TimeframeAnalysis) {
  if (row.actionNow === "SELL NOW") return "Sell now: trend weakness, sell pressure, or stop breakdown is active.";
  if (row.actionNow === "REDUCE / TAKE PROFIT") return "Reduce/take profit: sellers appeared while the trade is profitable.";
  if (row.pressure === "Sell Pressure") return "Avoid new buy: sell pressure is stronger than buyer pressure.";
  return `Planned sell point is the target at ${money(row.suggestedTarget)}; risk exit is below ${money(row.suggestedStop)}.`;
}

function providerTime(value?: string) {
  if (!value) return "-";
  return new Date(value).toLocaleString("en-GB", {
    timeZone: "Africa/Cairo",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function buildRows(data: MarketDataSnapshot): TradeSignalRow[] {
  const bestBySymbol = new Map(data.screenerRows.map((row) => [row.symbol, row]));
  return data.timeframeAnalyses
    .filter((row) => row.timeframe === "1D")
    .map((row) => {
      const stock = bestBySymbol.get(row.symbol);
      const side = sideFor(row);
      const pressureBoost = row.pressure === "Buy Pressure" ? 18 : row.pressure === "Sell Pressure" ? 18 : 0;
      const volumeBoost = row.volumeStatus === "Very Strong" ? 18 : row.volumeStatus === "Strong" ? 12 : row.volumeStatus === "Normal" ? 5 : 0;
      const trendBoost = row.mainTrend === "LONG BULLISH" ? 16 : row.mainTrend === "SWING BULLISH" ? 12 : row.mainTrend === "BEARISH" ? 14 : 4;
      const urgency = Math.min(100, Math.round(row.score * 7 + actionScore(row.actionNow) + pressureBoost + volumeBoost + trendBoost + Math.min(row.riskReward, 3) * 4));
      const confidence = Math.min(100, Math.round((stock?.overallScore ?? row.score) * 8 + (stock?.dataQuality === "partial" ? 8 : 0) + (row.riskReward >= 1 ? 8 : 0)));
      return {
        id: `trade-signal-${row.symbol}-${row.timeframe}`,
        symbol: row.symbol,
        companyName: stock?.companyName ?? row.symbol,
        timeframe: row.timeframe,
        side,
        action: row.actionNow,
        currentPrice: row.currentPrice,
        bestBuyPoint: row.suggestedEntry,
        bestSellPoint: side === "SELL" ? row.currentPrice : row.suggestedTarget,
        takeProfit: row.suggestedTarget,
        stopLoss: row.suggestedStop,
        score: row.score,
        urgency,
        confidence,
        trend: row.mainTrend,
        plan: row.plan,
        pressure: row.pressure,
        volumeStatus: row.volumeStatus,
        riskReward: row.riskReward,
        buyReason: buildBuyReason(row),
        sellReason: buildSellReason(row),
        strategyReason: row.advice ?? stock?.reason ?? "Strategy row loaded from current provider analysis.",
        dataQuality: stock?.dataQuality ?? "partial",
        orderBookStatus: stock?.orderBookStatus ?? "unavailable",
        providerUpdatedAt: stock?.providerUpdatedAt,
        lastUpdateEgypt: row.lastUpdateEgypt,
      };
    })
    .sort((a, b) => {
      const sideWeight: Record<SignalSide, number> = { BUY: 4, WATCH: 3, SELL: 2, AVOID: 1 };
      return sideWeight[b.side] * 1000 + b.urgency - (sideWeight[a.side] * 1000 + a.urgency);
    });
}

function NotificationCard({ row, onOpenStock }: { row: TradeSignalRow; onOpenStock: (symbol: string) => void }) {
  const Icon = signalIcon(row.side);
  return (
    <button type="button" onClick={() => onOpenStock(row.symbol)} className={`rounded-lg border p-3 text-left transition hover:border-cyan-400/50 ${row.side === "BUY" ? "bg-emerald-500/8" : row.side === "SELL" ? "bg-red-500/8" : row.side === "WATCH" ? "bg-amber-500/8" : "bg-[#0C131D]"}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 gap-3">
          <div className={`grid h-9 w-9 shrink-0 place-items-center rounded border ${sideTone(row.side)}`}>
            <Icon size={17} />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-bold text-white">{row.symbol}</span>
              <span className={`rounded border px-2 py-1 text-[11px] font-bold ${sideTone(row.side)}`}>{row.side}</span>
              <ActionBadge action={row.action} />
            </div>
            <p className="mt-1 truncate text-xs text-slate-400">{row.companyName}</p>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-sm font-bold text-cyan-300">{row.urgency}</div>
          <div className="text-[11px] text-slate-500">urgency</div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <Metric label="Buy Point" value={money(row.bestBuyPoint)} accent="text-emerald-300" />
        <Metric label="Sell/Target" value={money(row.bestSellPoint)} accent="text-teal-300" />
        <Metric label="Stop" value={money(row.stopLoss)} accent="text-red-300" />
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-300">{row.side === "SELL" || row.side === "AVOID" ? row.sellReason : row.buyReason}</p>
    </button>
  );
}

export default function TradeSignals({
  data,
  onOpenStock,
  onManualRefresh,
  isRefreshing,
  refreshMessage,
}: {
  data: MarketDataSnapshot;
  onOpenStock: (symbol: string) => void;
  onManualRefresh: () => Promise<void>;
  isRefreshing: boolean;
  refreshMessage: string;
}) {
  const [query, setQuery] = useState("");
  const [side, setSide] = useState("All");
  const [action, setAction] = useState("All");
  const [minScore, setMinScore] = useState(0);
  const [browserAlertsEnabled, setBrowserAlertsEnabled] = useState(() => typeof Notification !== "undefined" && Notification.permission === "granted");
  const [alertStatus, setAlertStatus] = useState("");

  const rows = useMemo(() => buildRows(data), [data]);
  const filtered = useMemo(() => rows.filter((row) =>
    (row.symbol.toLowerCase().includes(query.toLowerCase()) || row.companyName.toLowerCase().includes(query.toLowerCase())) &&
    (side === "All" || row.side === side) &&
    (action === "All" || row.action === action) &&
    row.score >= minScore
  ), [action, minScore, query, rows, side]);

  const buyRows = rows.filter((row) => row.side === "BUY");
  const watchRows = rows.filter((row) => row.side === "WATCH");
  const sellRows = rows.filter((row) => row.side === "SELL" || row.side === "AVOID");
  const notifications = [...buyRows.slice(0, 4), ...watchRows.slice(0, 3), ...sellRows.slice(0, 3)].sort((a, b) => b.urgency - a.urgency).slice(0, 8);

  const enableBrowserAlerts = async () => {
    if (typeof Notification === "undefined") {
      setAlertStatus("Browser notifications are not supported here.");
      return;
    }
    if (Notification.permission === "granted") {
      setBrowserAlertsEnabled(true);
      setAlertStatus("Browser signal notifications are enabled.");
      return;
    }
    const permission = await Notification.requestPermission();
    setBrowserAlertsEnabled(permission === "granted");
    setAlertStatus(permission === "granted" ? "Browser signal notifications are enabled." : "Notifications were not allowed by the browser.");
  };

  useEffect(() => {
    if (!browserAlertsEnabled || typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const urgent = rows
      .filter((row) => (row.side === "BUY" || row.side === "SELL") && row.urgency >= 75)
      .sort((a, b) => b.urgency - a.urgency)
      .slice(0, 3);

    for (const row of urgent) {
      const key = `trade-signal-notified:${row.symbol}:${row.side}:${row.action}:${row.lastUpdateEgypt}`;
      if (localStorage.getItem(key)) continue;
      localStorage.setItem(key, "1");
      const title = `${row.side} signal: ${row.symbol}`;
      const body = row.side === "BUY"
        ? `Buy near ${money(row.bestBuyPoint)} | Target ${money(row.takeProfit)} | Stop ${money(row.stopLoss)}`
        : `Exit/protect near ${money(row.bestSellPoint)} | Stop ${money(row.stopLoss)} | ${row.action}`;
      new Notification(title, { body });
    }
  }, [browserAlertsEnabled, rows]);

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <BellRing size={20} className="text-cyan-300" />
              <h2 className="text-lg font-bold text-white">Trade Signals & Notifications</h2>
              <span className="rounded border border-cyan-400/30 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-200">Auto updates with market scan</span>
            </div>
            <p className="mt-2 max-w-5xl text-sm leading-6 text-slate-400">
              A decision table built from the same Omar Smart PRO V3 strategy output, provider scanner rows, and risk levels. Signals are educational only and show why to buy, why to sell, best buy point, target/sell point, and stop.
            </p>
          </div>
          <div className="grid min-w-[360px] grid-cols-2 gap-3 rounded border border-terminal-border bg-[#0C131D] p-3">
            <Metric label="Buy Signals" value={buyRows.length} accent="text-emerald-300" />
            <Metric label="Watch Signals" value={watchRows.length} accent="text-amber-300" />
            <Metric label="Sell/Avoid" value={sellRows.length} accent="text-red-300" />
            <Metric label="Latest Update" value={data.backendStatus?.latestDataRefreshAt ? new Date(data.backendStatus.latestDataRefreshAt).toLocaleTimeString("en-GB", { timeZone: "Africa/Cairo" }) : egyptNow()} />
            <Metric label="Provider" value={data.backendStatus?.activeProvider ?? data.sourceLabel} />
            <Metric label="Bid/Ask" value={data.backendStatus?.bidAskStatus ?? "unavailable"} />
          </div>
        </div>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
          <button type="button" onClick={enableBrowserAlerts} className="inline-flex h-9 items-center justify-center gap-2 rounded border border-cyan-400/35 bg-cyan-500/10 px-3 text-xs font-semibold text-cyan-200">
            <BellRing size={14} /> {browserAlertsEnabled ? "Browser Notifications On" : "Enable Browser Notifications"}
          </button>
          <button type="button" onClick={() => void onManualRefresh()} disabled={isRefreshing} className="inline-flex h-9 items-center justify-center gap-2 rounded bg-teal-500 px-3 text-xs font-semibold text-[#04110F] disabled:opacity-60">
            <RefreshCw size={14} className={isRefreshing ? "animate-spin" : ""} /> Refresh Provider Data
          </button>
          {alertStatus && <span className="text-xs text-slate-400">{alertStatus}</span>}
          {refreshMessage && <span className="text-xs text-slate-400">{refreshMessage}</span>}
        </div>
        <div className="mt-3 rounded border border-amber-400/30 bg-amber-500/8 p-3 text-xs leading-5 text-amber-100">
          The current free provider does not expose real bid/ask, true order book depth, or guaranteed tick-by-tick live exchange data. Buy/sell points below come from the latest provider OHLCV snapshot, ATR, range filters, trend, volume, and pressure.
        </div>
      </section>

      <section className="grid gap-3 xl:grid-cols-2 2xl:grid-cols-4">
        {notifications.map((row) => <NotificationCard key={row.id} row={row} onOpenStock={onOpenStock} />)}
      </section>

      <FilterToolbar>
        <FilterInput placeholder="Search symbol/company" value={query} onChange={(event) => setQuery(event.target.value)} />
        <FilterSelect value={side} onChange={(event) => setSide(event.target.value)}>
          <option>All</option>
          <option>BUY</option>
          <option>WATCH</option>
          <option>SELL</option>
          <option>AVOID</option>
        </FilterSelect>
        <FilterSelect value={action} onChange={(event) => setAction(event.target.value)}>
          <option>All</option>
          <option>BUY NOW</option>
          <option>BREAKOUT BUY</option>
          <option>PULLBACK BUY AREA</option>
          <option>WATCH EARLY BUY</option>
          <option>WAIT PULLBACK</option>
          <option>HOLD</option>
          <option>REDUCE / TAKE PROFIT</option>
          <option>SELL NOW</option>
          <option>DO NOT BUY NOW</option>
          <option>WATCH</option>
          <option>WAIT</option>
        </FilterSelect>
        <label className="flex h-9 items-center gap-2 rounded border border-terminal-border bg-terminal-card px-3 text-xs text-slate-400">
          Min score
          <input type="number" min={0} max={10} value={minScore} onChange={(event) => setMinScore(Number(event.target.value))} className="w-14 bg-transparent text-slate-100 outline-none" />
        </label>
        <button type="button" onClick={() => window.location.reload()} className="inline-flex h-9 items-center justify-center gap-2 rounded border border-terminal-border bg-terminal-card px-3 text-xs font-semibold text-slate-200">
          <RefreshCw size={14} /> Refresh View
        </button>
      </FilterToolbar>

      <section className="overflow-hidden rounded-lg border border-terminal-border bg-terminal-card">
        <div className="flex items-center gap-2 border-b border-terminal-border bg-[#0C131D] px-4 py-3">
          <Target size={17} className="text-teal-300" />
          <h3 className="font-bold text-white">Full Signal Analysis Table</h3>
          <span className="text-xs text-slate-500">{filtered.length} rows</span>
        </div>
        <div className="overflow-auto">
          <table className="w-full min-w-[1900px] text-left text-xs">
            <thead className="sticky top-0 bg-[#101A27] text-[11px] uppercase text-slate-400">
              <tr>
                {[
                  "Symbol",
                  "Company",
                  "Side",
                  "Action",
                  "Frame",
                  "Price",
                  "Best Buy Point",
                  "Best Sell / Target",
                  "Stop Loss",
                  "Score",
                  "Urgency",
                  "Confidence",
                  "Trend",
                  "Plan",
                  "Pressure",
                  "Volume",
                  "R/R",
                  "Why Buy",
                  "Why Sell / Avoid",
                  "Strategy Reason",
                  "Data",
                  "Quote Time",
                  "Last Update",
                ].map((heading) => <th key={heading} className="border-b border-terminal-border px-3 py-2">{heading}</th>)}
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr key={row.id} className="border-b border-terminal-border/70 hover:bg-white/[0.03]">
                  <td className="sticky left-0 z-10 bg-terminal-card px-3 py-2 font-bold text-white">
                    <button type="button" onClick={() => onOpenStock(row.symbol)} className="text-left hover:text-cyan-300">{row.symbol}</button>
                  </td>
                  <td className="px-3 py-2 text-slate-300">{row.companyName}</td>
                  <td className="px-3 py-2"><span className={`rounded border px-2 py-1 text-[11px] font-bold ${sideTone(row.side)}`}>{row.side}</span></td>
                  <td className="px-3 py-2"><ActionBadge action={row.action} /></td>
                  <td className="px-3 py-2 text-cyan-300">{row.timeframe}</td>
                  <td className="px-3 py-2 text-white">{money(row.currentPrice)}</td>
                  <td className="px-3 py-2 text-emerald-300">{money(row.bestBuyPoint)}</td>
                  <td className="px-3 py-2 text-teal-300">{money(row.bestSellPoint)}</td>
                  <td className="px-3 py-2 text-red-300">{money(row.stopLoss)}</td>
                  <td className="px-3 py-2 text-amber-300">{row.score}/10</td>
                  <td className="px-3 py-2 text-cyan-300">{row.urgency}</td>
                  <td className="px-3 py-2 text-slate-200">{row.confidence}%</td>
                  <td className="px-3 py-2 text-slate-300">{row.trend}</td>
                  <td className="px-3 py-2 text-slate-300">{row.plan}</td>
                  <td className="px-3 py-2 text-slate-300">{row.pressure}</td>
                  <td className="px-3 py-2 text-slate-300">{row.volumeStatus}</td>
                  <td className="px-3 py-2 text-slate-200">{row.riskReward.toFixed(2)}</td>
                  <td className="max-w-[320px] px-3 py-2 leading-5 text-slate-400">{row.buyReason}</td>
                  <td className="max-w-[320px] px-3 py-2 leading-5 text-slate-400">{row.sellReason}</td>
                  <td className="max-w-[320px] px-3 py-2 leading-5 text-slate-400">{row.strategyReason}</td>
                  <td className="px-3 py-2 text-slate-400">{row.dataQuality ?? "partial"} / {row.orderBookStatus ?? "unavailable"}</td>
                  <td className="px-3 py-2 text-slate-500">{providerTime(row.providerUpdatedAt)}</td>
                  <td className="px-3 py-2 text-slate-500">{row.lastUpdateEgypt}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!filtered.length && <div className="p-5 text-sm text-slate-400">No trade signals match the current filters.</div>}
        </div>
      </section>
    </div>
  );
}
