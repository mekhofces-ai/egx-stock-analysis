import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Bot, BrainCircuit, Download, PlayCircle, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import ActionBadge from "../components/ActionBadge";
import Metric from "../components/Metric";
import type { MarketDataSnapshot } from "../data/mockData";
import { buildBotPrediction, type BotLesson, type BotLessonCategory } from "../lib/analystBot";
import { egyptNow, money } from "../lib/format";
import type { ActionNow, BestStock, TimeframeAnalysis } from "../types";

const storageKey = "egx-smart-screener-analyst-bot-lessons";
const categories: BotLessonCategory[] = ["Strategy Rule", "Chart Pattern", "Daily Report", "Risk Rule", "Market Psychology", "Personal Note"];

function loadLessons(): BotLesson[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(storageKey) ?? "[]") as BotLesson[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveLessons(lessons: BotLesson[]) {
  localStorage.setItem(storageKey, JSON.stringify(lessons));
}

function exportLessons(lessons: BotLesson[]) {
  const blob = new Blob([JSON.stringify(lessons, null, 2)], { type: "application/json;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "stock-signal-pro-bot-memory.json";
  link.click();
  URL.revokeObjectURL(link.href);
}

function stanceClass(stance: string) {
  if (stance === "Bullish") return "border-emerald-400/35 bg-emerald-500/10 text-emerald-300";
  if (stance === "Bearish") return "border-red-400/35 bg-red-500/10 text-red-300";
  if (stance === "Watch") return "border-amber-400/35 bg-amber-500/10 text-amber-300";
  if (stance === "Unavailable") return "border-slate-500/35 bg-slate-500/10 text-slate-400";
  return "border-terminal-border bg-[#0B111A] text-slate-300";
}

type DailyPickTier = "Buy Setup" | "Early Watch" | "Protect / Avoid";

type DailyPick = {
  row: TimeframeAnalysis;
  stock?: BestStock;
  tier: DailyPickTier;
  rankScore: number;
};

const actionBonus: Record<ActionNow, number> = {
  "BUY NOW": 34,
  "BREAKOUT BUY": 30,
  "PULLBACK BUY AREA": 28,
  "WATCH EARLY BUY": 18,
  "WAIT PULLBACK": 14,
  WATCH: 12,
  HOLD: 8,
  WAIT: 0,
  "REDUCE / TAKE PROFIT": -12,
  "DO NOT BUY NOW": -24,
  "SELL NOW": -34,
};

function latestEgyptDate(value?: string | null) {
  if (!value) return "Unavailable";
  return new Date(value).toLocaleDateString("en-GB", { timeZone: "Africa/Cairo" });
}

function pickTier(row: TimeframeAnalysis): DailyPickTier {
  const buyActions: ActionNow[] = ["BUY NOW", "BREAKOUT BUY", "PULLBACK BUY AREA"];
  const earlyActions: ActionNow[] = ["WATCH EARLY BUY", "WAIT PULLBACK", "WATCH"];
  if (
    buyActions.includes(row.actionNow) &&
    row.pressure === "Buy Pressure" &&
    row.score >= 6 &&
    row.riskReward >= 1
  ) {
    return "Buy Setup";
  }
  if (
    earlyActions.includes(row.actionNow) ||
    (row.actionNow === "HOLD" && row.pressure === "Buy Pressure" && row.score >= 7)
  ) {
    return "Early Watch";
  }
  return "Protect / Avoid";
}

function dailyPickRank(row: TimeframeAnalysis, stock?: BestStock) {
  const volumeBoost = row.volumeStatus === "Very Strong" ? 16 : row.volumeStatus === "Strong" ? 10 : row.volumeStatus === "Normal" ? 4 : -6;
  const pressureBoost = row.pressure === "Buy Pressure" ? 18 : row.pressure === "Sell Pressure" ? -24 : 0;
  const trendBoost = row.mainTrend === "LONG BULLISH" ? 16 : row.mainTrend === "SWING BULLISH" ? 12 : row.mainTrend === "SHORT BULLISH" ? 6 : row.mainTrend === "BEARISH" ? -18 : 0;
  const rrBoost = Math.min(Math.max(row.riskReward, 0), 3) * 7;
  const actionScore = actionBonus[row.actionNow] ?? 0;
  const chasePenalty = (stock?.changePercent ?? 0) > 10 && row.actionNow !== "PULLBACK BUY AREA" ? 8 : 0;
  return Math.round(row.score * 9 + volumeBoost + pressureBoost + trendBoost + rrBoost + actionScore - chasePenalty);
}

function buildDailyPicks(data: MarketDataSnapshot) {
  const stocksBySymbol = new Map(data.screenerRows.map((row) => [row.symbol, row]));
  const latest = latestEgyptDate(data.backendStatus?.latestCompletedCandleAt ?? data.backendStatus?.latestCandleAt);
  const dailyRows = data.timeframeAnalyses
    .filter((row) => row.timeframe === "1D")
    .filter((row) => latest === "Unavailable" || latestEgyptDate(row.lastUpdateEgypt) === latest);

  const picks = dailyRows.map((row) => {
    const stock = stocksBySymbol.get(row.symbol);
    return {
      row,
      stock,
      tier: pickTier(row),
      rankScore: dailyPickRank(row, stock),
    };
  });

  const buySetups = picks
    .filter((pick) => pick.tier === "Buy Setup")
    .sort((a, b) => b.rankScore - a.rankScore)
    .slice(0, 6);
  const earlyWatch = picks
    .filter((pick) => pick.tier === "Early Watch")
    .sort((a, b) => b.rankScore - a.rankScore)
    .slice(0, 8);
  const protectAvoid = picks
    .filter((pick) => pick.tier === "Protect / Avoid")
    .sort((a, b) => a.rankScore - b.rankScore)
    .slice(0, 6);

  return { buySetups, earlyWatch, protectAvoid, latest };
}

function pickTone(tier: DailyPickTier) {
  if (tier === "Buy Setup") return "border-emerald-400/30 bg-emerald-500/8";
  if (tier === "Early Watch") return "border-amber-400/30 bg-amber-500/8";
  return "border-red-400/30 bg-red-500/8";
}

function DailyPickCard({ pick }: { pick: DailyPick }) {
  const row = pick.row;
  const company = pick.stock?.companyName ?? row.symbol;
  return (
    <article className={`rounded border p-3 ${pickTone(pick.tier)}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base font-bold text-white">{row.symbol}</span>
            <ActionBadge action={row.actionNow} />
            <span className="rounded border border-terminal-border bg-[#0B111A] px-2 py-1 text-[11px] text-slate-300">{row.timeframe}</span>
          </div>
          <p className="mt-1 truncate text-xs text-slate-400">{company}</p>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-sm font-bold text-cyan-300">{pick.rankScore}</div>
          <div className="text-[11px] text-slate-500">rank</div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <Metric label="Price" value={money(row.currentPrice)} accent="text-white" />
        <Metric label="Score" value={`${row.score}/10`} accent="text-amber-300" />
        <Metric label="Entry" value={money(row.suggestedEntry)} accent="text-emerald-300" />
        <Metric label="Target" value={money(row.suggestedTarget)} accent="text-teal-300" />
        <Metric label="Stop" value={money(row.suggestedStop)} accent="text-red-300" />
        <Metric label="R/R" value={row.riskReward.toFixed(2)} accent="text-cyan-300" />
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-300">
        {row.pressure}, {row.volumeStatus.toLowerCase()} volume, {row.mainTrend.toLowerCase()}. {row.advice ?? "Wait for confirmation."}
      </p>
    </article>
  );
}

export default function AnalystBot({
  data,
  selectedSymbol,
  onManualRefresh,
  isRefreshing = false,
  refreshMessage = "",
}: {
  data: MarketDataSnapshot;
  selectedSymbol: string;
  onManualRefresh?: () => void | Promise<void>;
  isRefreshing?: boolean;
  refreshMessage?: string;
}) {
  const [lessons, setLessons] = useState<BotLesson[]>([]);
  const [symbol, setSymbol] = useState(selectedSymbol || "COMI");
  const [category, setCategory] = useState<BotLessonCategory>("Strategy Rule");
  const [scopeSymbol, setScopeSymbol] = useState("ALL");
  const [tags, setTags] = useState("breakout, volume, support");
  const [weight, setWeight] = useState(3);
  const [lessonText, setLessonText] = useState("");
  const [dailyReport, setDailyReport] = useState("");
  const [question, setQuestion] = useState("Predict the best action now and explain risk.");
  const [status, setStatus] = useState("");
  const prediction = useMemo(() => buildBotPrediction({
    symbol,
    stocks: data.stocks,
    bestRows: data.screenerRows,
    analyses: data.timeframeAnalyses,
    lessons,
    question,
    dailyReport,
    dataStatus: data.backendStatus,
  }), [data, dailyReport, lessons, question, symbol]);

  useEffect(() => {
    setLessons(loadLessons());
  }, []);

  useEffect(() => {
    if (selectedSymbol) setSymbol(selectedSymbol);
  }, [selectedSymbol]);

  const persist = (next: BotLesson[]) => {
    setLessons(next);
    saveLessons(next);
  };

  const teach = () => {
    const text = lessonText.trim();
    if (text.length < 8) {
      setStatus("Write a clear lesson first.");
      return;
    }
    const nextLesson: BotLesson = {
      id: `lesson-${Date.now()}`,
      category,
      text,
      scopeSymbol: scopeSymbol.trim().toUpperCase() || "ALL",
      tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean),
      weight,
      createdAtEgypt: egyptNow(),
      source: "user",
    };
    persist([nextLesson, ...lessons]);
    setLessonText("");
    setStatus(`Saved lesson for ${nextLesson.scopeSymbol}. Bot memory now has ${lessons.length + 1} lesson(s).`);
  };

  const removeLesson = (id: string) => {
    persist(lessons.filter((lesson) => lesson.id !== id));
  };

  const selectedRow = data.screenerRows.find((row) => row.symbol === symbol);
  const strategyFrame = data.timeframeAnalyses.find((row) => row.symbol === prediction.symbol && row.timeframe === prediction.strategyFrame);
  const currentPrice = strategyFrame?.currentPrice ?? selectedRow?.currentPrice;
  const matchedCount = prediction.matchedLessons.length;
  const dailyPicks = useMemo(() => buildDailyPicks(data), [data]);
  const hasDirectBuy = dailyPicks.buySetups.length > 0;
  const visibleStrategyLessons = [
    ...prediction.matchedLessons.filter((lesson) => lesson.source === "strategy-core").slice(0, 3),
    ...prediction.matchedLessons.filter((lesson) => lesson.source !== "strategy-core").slice(0, 3),
  ];

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 place-items-center rounded bg-teal-500/15 text-teal-300"><Bot size={22} /></div>
              <div>
                <h2 className="text-xl font-bold text-white">StockSignalPro Learning Bot</h2>
                <p className="mt-1 text-sm text-slate-400">Teach it your rules, daily reports, chart notes, and risk discipline. It combines your memory with the live EGX strategy rows.</p>
              </div>
            </div>
          </div>
          <div className="grid min-w-[280px] grid-cols-2 gap-3 rounded border border-terminal-border bg-[#0C131D] p-3">
            <Metric label="Memory Lessons" value={lessons.length} accent="text-teal-300" />
            <Metric label="Matched Now" value={matchedCount} accent="text-amber-300" />
            <Metric label="Strategy Core" value="Omar V3" accent="text-emerald-300" />
            <Metric label="Applied Frame" value={prediction.strategyFrame} />
            <Metric label="Consensus" value={`${prediction.consensusScore}/100`} accent="text-cyan-300" />
            <Metric label="Primary Lens" value={prediction.primaryStrategy} accent="text-white" />
            <Metric label="Reliability" value={`${prediction.dataReliability.grade} ${prediction.dataReliability.score}/100`} accent={prediction.dataReliability.grade === "High" ? "text-emerald-300" : prediction.dataReliability.grade === "Medium" ? "text-amber-300" : "text-red-300"} />
            <Metric label="Provider" value={data.backendStatus?.activeProvider ?? "unknown"} />
            <Metric label="Coverage" value={`${data.realCoverageCount}/${data.stocks.length}`} />
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-3 rounded border border-terminal-border bg-[#0C131D] p-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="text-xs font-bold uppercase tracking-normal text-slate-500">Provider Data</div>
            <div className="mt-1 text-sm text-slate-300">
              {data.realCoverageCount > 0
                ? `${data.realCoverageCount}/${data.stocks.length} EGX symbols loaded from ${data.backendStatus?.activeProvider ?? "provider"}.`
                : "No provider rows are loaded yet. Refresh the backend provider before trusting any prediction."}
            </div>
            {refreshMessage && <div className="mt-1 text-xs text-cyan-300">{refreshMessage}</div>}
          </div>
          {onManualRefresh && (
            <button
              type="button"
              onClick={() => void onManualRefresh()}
              disabled={isRefreshing}
              className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded bg-cyan-500 px-4 text-sm font-semibold text-[#031018] disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
              {isRefreshing ? "Refreshing" : "Refresh Provider Data"}
            </button>
          )}
        </div>
        <div className="mt-4 rounded border border-amber-400/30 bg-amber-500/8 p-3 text-xs leading-5 text-amber-100">
          Not financial advice. The bot gives educational scenarios and risk checks. It must not be treated as a guaranteed prediction or automatic trading instruction.
        </div>
      </section>

      <section className="rounded-lg border border-emerald-400/20 bg-terminal-card p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-bold text-white">Daily Picks Today</h3>
              <span className="rounded border border-cyan-400/30 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-200">{dailyPicks.latest}</span>
              <span className={`rounded border px-2 py-1 text-xs font-semibold ${hasDirectBuy ? "border-emerald-400/35 bg-emerald-500/10 text-emerald-300" : "border-amber-400/35 bg-amber-500/10 text-amber-300"}`}>
                {hasDirectBuy ? `${dailyPicks.buySetups.length} buy setup(s)` : "No clean BUY NOW setup"}
              </span>
            </div>
            <p className="mt-2 max-w-5xl text-sm leading-6 text-slate-300">
              Daily pickup means the strongest daily candidates from Omar Smart PRO V3 and the expert strategy stack. Use it as an educational watchlist: confirm price reaction, volume direction, and stop level before any real trade.
            </p>
          </div>
          <div className="grid min-w-[320px] grid-cols-2 gap-3 rounded border border-terminal-border bg-[#0C131D] p-3">
            <Metric label="Buy Setups" value={dailyPicks.buySetups.length} accent="text-emerald-300" />
            <Metric label="Early Watch" value={dailyPicks.earlyWatch.length} accent="text-amber-300" />
            <Metric label="Protect / Avoid" value={dailyPicks.protectAvoid.length} accent="text-red-300" />
            <Metric label="Real Bid/Ask" value={data.backendStatus?.bidAskStatus ?? "unavailable"} />
          </div>
        </div>

        {!hasDirectBuy && (
          <div className="mt-4 flex gap-3 rounded border border-amber-400/30 bg-amber-500/8 p-3 text-xs leading-5 text-amber-100">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <div>
              The strategy is not giving a clean daily BUY NOW basket from the current provider snapshot. The best professional action is to watch the early candidates below and wait for a pullback, breakout confirmation, or stronger buy pressure.
            </div>
          </div>
        )}

        <div className="mt-4 grid gap-4 2xl:grid-cols-3">
          <div>
            <div className="mb-2 text-xs font-bold uppercase tracking-normal text-emerald-300">Buy Setup</div>
            <div className="grid gap-3">
              {(dailyPicks.buySetups.length ? dailyPicks.buySetups : dailyPicks.earlyWatch.slice(0, 3)).map((pick) => <DailyPickCard key={`${pick.row.symbol}-${pick.tier}`} pick={pick} />)}
            </div>
          </div>
          <div>
            <div className="mb-2 text-xs font-bold uppercase tracking-normal text-amber-300">Early Watch / Pullback</div>
            <div className="grid gap-3">
              {dailyPicks.earlyWatch.slice(0, 4).map((pick) => <DailyPickCard key={`${pick.row.symbol}-${pick.tier}`} pick={pick} />)}
            </div>
          </div>
          <div>
            <div className="mb-2 text-xs font-bold uppercase tracking-normal text-red-300">Protect / Avoid</div>
            <div className="grid gap-3">
              {dailyPicks.protectAvoid.slice(0, 4).map((pick) => <DailyPickCard key={`${pick.row.symbol}-${pick.tier}`} pick={pick} />)}
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-cyan-400/25 bg-terminal-card p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-bold text-white">Final Consensus Recommendation</h3>
              <ActionBadge action={prediction.action} />
              <span className={`rounded border px-2 py-1 text-xs font-semibold ${prediction.bias === "Bullish" ? "border-emerald-400/35 bg-emerald-500/10 text-emerald-300" : prediction.bias === "Bearish" ? "border-red-400/35 bg-red-500/10 text-red-300" : "border-amber-400/35 bg-amber-500/10 text-amber-300"}`}>{prediction.bias}</span>
            </div>
            <p className="mt-3 max-w-5xl text-sm leading-6 text-slate-200">{prediction.recommendationReason}</p>
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              <div className="rounded border border-emerald-400/20 bg-emerald-500/5 p-3">
                <div className="text-xs font-bold uppercase text-emerald-300">Confirmations</div>
                <ul className="mt-2 space-y-2 text-xs leading-5 text-slate-300">
                  {(prediction.confirmations.length ? prediction.confirmations : ["No strong bullish confirmation is available yet."]).slice(0, 3).map((item) => <li key={item}>{item}</li>)}
                </ul>
              </div>
              <div className="rounded border border-red-400/20 bg-red-500/5 p-3">
                <div className="text-xs font-bold uppercase text-red-300">Warnings</div>
                <ul className="mt-2 space-y-2 text-xs leading-5 text-slate-300">
                  {(prediction.warnings.length ? prediction.warnings : ["No major bearish warning dominates the stack."]).slice(0, 3).map((item) => <li key={item}>{item}</li>)}
                </ul>
              </div>
            </div>
          </div>
          <div className="grid min-w-[320px] grid-cols-2 gap-3 rounded border border-terminal-border bg-[#0C131D] p-3">
            <Metric label="Final Action" value={prediction.action} accent="text-white" />
            <Metric label="Original Omar V3" value={prediction.originalStrategyAction} />
            <Metric label="Consensus" value={`${prediction.consensusScore}/100`} accent="text-cyan-300" />
            <Metric label="Agreement" value={`${prediction.strategyVoteSummary.agreementPercent}%`} accent="text-amber-300" />
            <Metric label="Reliability" value={`${prediction.dataReliability.grade} ${prediction.dataReliability.score}/100`} accent={prediction.dataReliability.grade === "High" ? "text-emerald-300" : prediction.dataReliability.grade === "Medium" ? "text-amber-300" : "text-red-300"} />
            <Metric label="Frames" value={prediction.dataReliability.frameCoverage} />
            <Metric label="Bullish Lenses" value={prediction.strategyVoteSummary.bullish} accent="text-emerald-300" />
            <Metric label="Watch Lenses" value={prediction.strategyVoteSummary.watch} accent="text-amber-300" />
            <Metric label="Bearish Lenses" value={prediction.strategyVoteSummary.bearish} accent="text-red-300" />
            <Metric label="Risk" value={prediction.riskLevel} />
          </div>
        </div>
        <div className="mt-3 rounded border border-terminal-border bg-[#0C131D] p-3 text-xs leading-5 text-slate-300">
          <span className="font-bold text-cyan-300">Data reliability: </span>
          {prediction.dataReliability.note} Latest Egypt date: {prediction.dataReliability.latestDateEgypt}. Real bid/ask is {data.backendStatus?.bidAskStatus ?? "unavailable"}.
        </div>
      </section>

      <section className="rounded-lg border border-emerald-400/20 bg-terminal-card p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-bold text-white">Omar Smart PRO V3 Core</h3>
              <span className={`rounded border px-2 py-1 text-xs font-semibold ${prediction.strategyApplied ? "border-emerald-400/35 bg-emerald-500/10 text-emerald-300" : "border-red-400/35 bg-red-500/10 text-red-300"}`}>
                {prediction.strategyApplied ? "Strategy Applied" : "Waiting For Data"}
              </span>
              <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-xs text-slate-300">Mode {prediction.strategyMode}</span>
            </div>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-300">
              The bot applies this strategy first: range filters, EMA trend stack, RSI, ATR zones, volume pressure, score, entry signals, and smart exits. Your saved lessons adjust the forecast after this core decision.
            </p>
            <div className="mt-3 grid gap-2 text-xs leading-5 text-slate-400 md:grid-cols-2">
              {prediction.strategySummary.map((item) => (
                <div key={item} className="rounded border border-terminal-border bg-[#0C131D] px-3 py-2">{item}</div>
              ))}
            </div>
          </div>
          <div className="grid min-w-[320px] grid-cols-2 gap-3 rounded border border-terminal-border bg-[#0C131D] p-3">
            <Metric label="Omar V3 Action" value={strategyFrame?.actionNow ?? prediction.originalStrategyAction} accent="text-white" />
            <Metric label="Score" value={strategyFrame ? `${strategyFrame.score}/10` : "-"} accent="text-amber-300" />
            <Metric label="Trend" value={strategyFrame?.mainTrend ?? "-"} />
            <Metric label="Plan" value={strategyFrame?.plan ?? "-"} />
            <Metric label="Pressure" value={strategyFrame?.pressure ?? "-"} accent={strategyFrame?.pressure === "Buy Pressure" ? "text-emerald-300" : strategyFrame?.pressure === "Sell Pressure" ? "text-red-300" : "text-slate-300"} />
            <Metric label="Volume" value={strategyFrame?.volumeStatus ?? "-"} />
            <Metric label="Buy Zone" value={strategyFrame ? `${money(strategyFrame.buyZoneLow)} - ${money(strategyFrame.buyZoneHigh)}` : "-"} accent="text-emerald-300" />
            <Metric label="Risk/Reward" value={strategyFrame ? strategyFrame.riskReward.toFixed(2) : "-"} accent="text-teal-300" />
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <h3 className="font-bold text-white">Expert Strategy Stack</h3>
            <p className="mt-1 max-w-4xl text-sm leading-6 text-slate-400">
              The bot now checks multiple independent lenses: Omar V3, multi-timeframe alignment, EMA trend quality, breakout confirmation, pullback value, volume pressure, RSI momentum, risk/reward, relative strength, and defensive risk.
            </p>
          </div>
          <div className="grid min-w-[280px] grid-cols-2 gap-3 rounded border border-terminal-border bg-[#0C131D] p-3">
            <Metric label="Consensus Score" value={`${prediction.consensusScore}/100`} accent="text-cyan-300" />
            <Metric label="Strongest Lens" value={prediction.primaryStrategy} accent="text-white" />
            <Metric label="Strategy Count" value={prediction.strategySignals.length} />
            <Metric label="User Lessons Used" value={prediction.userLessonCount} accent="text-amber-300" />
          </div>
        </div>
        <div className="mt-4 grid gap-3 xl:grid-cols-2 2xl:grid-cols-3">
          {prediction.strategySignals.map((item) => (
            <article key={item.id} className="rounded border border-terminal-border bg-[#0C131D] p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h4 className="truncate text-sm font-bold text-white">{item.name}</h4>
                  <p className="mt-2 text-xs leading-5 text-slate-400">{item.reason}</p>
                </div>
                <span className={`shrink-0 rounded border px-2 py-1 text-[11px] font-semibold ${stanceClass(item.stance)}`}>{item.stance}</span>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-3">
                <Metric label="Score" value={`${item.score}/100`} accent="text-cyan-300" />
                <Metric label="Confidence" value={`${item.confidence}%`} accent="text-amber-300" />
              </div>
              <div className="mt-3 space-y-1 text-[11px] leading-5 text-slate-500">
                {item.evidence.slice(0, 3).map((evidence) => <div key={evidence}>{evidence}</div>)}
              </div>
            </article>
          ))}
        </div>
      </section>

      <div className="grid gap-4 2xl:grid-cols-[minmax(360px,0.85fr)_minmax(0,1.15fr)]">
        <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
          <div className="flex items-center gap-2">
            <BrainCircuit size={18} className="text-teal-300" />
            <h3 className="font-bold text-white">Teach The Bot</h3>
          </div>
          <div className="mt-4 grid gap-3">
            <label className="grid gap-2 text-xs text-slate-400">Lesson type
              <select value={category} onChange={(event) => setCategory(event.target.value as BotLessonCategory)} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-sm text-slate-100">
                {categories.map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="grid gap-2 text-xs text-slate-400">Applies to symbol
                <input value={scopeSymbol} onChange={(event) => setScopeSymbol(event.target.value.toUpperCase())} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-sm text-slate-100" placeholder="ALL or COMI" />
              </label>
              <label className="grid gap-2 text-xs text-slate-400">Lesson strength: {weight}
                <input type="range" min={1} max={5} value={weight} onChange={(event) => setWeight(Number(event.target.value))} className="accent-teal-400" />
              </label>
            </div>
            <label className="grid gap-2 text-xs text-slate-400">Tags
              <input value={tags} onChange={(event) => setTags(event.target.value)} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-sm text-slate-100" placeholder="support, breakout, volume" />
            </label>
            <label className="grid gap-2 text-xs text-slate-400">Lesson / rule / chart note
              <textarea value={lessonText} onChange={(event) => setLessonText(event.target.value)} rows={6} className="resize-y rounded border border-terminal-border bg-[#0B111A] p-3 text-sm leading-6 text-slate-100 outline-none focus:border-teal-400" placeholder="Example: If EGTS breaks resistance with strong volume and RSI remains under 80, wait for a pullback to the buy zone before entry." />
            </label>
            <button type="button" onClick={teach} className="inline-flex h-10 items-center justify-center gap-2 rounded bg-teal-500 px-4 text-sm font-semibold text-[#04110F]"><Plus size={16} /> Save Lesson</button>
            {status && <div className="rounded border border-emerald-400/30 bg-emerald-500/10 p-3 text-sm text-emerald-300">{status}</div>}
          </div>
        </section>

        <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <h3 className="font-bold text-white">Ask For A Prediction</h3>
              <p className="mt-1 text-xs text-slate-400">Uses current provider data, Omar Smart PRO V3 output, and your saved lessons.</p>
            </div>
            <select value={symbol} onChange={(event) => setSymbol(event.target.value)} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-sm text-slate-100">
              {data.stocks.map((stock) => <option key={stock.symbol} value={stock.symbol}>{stock.symbol} - {stock.companyName}</option>)}
            </select>
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            <label className="grid gap-2 text-xs text-slate-400">Daily report / chart observation
              <textarea value={dailyReport} onChange={(event) => setDailyReport(event.target.value)} rows={7} className="resize-y rounded border border-terminal-border bg-[#0B111A] p-3 text-sm leading-6 text-slate-100 outline-none focus:border-teal-400" placeholder="Paste your daily report, news notes, volume observations, support/resistance, or chart plan here." />
            </label>
            <label className="grid gap-2 text-xs text-slate-400">Question to the bot
              <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={7} className="resize-y rounded border border-terminal-border bg-[#0B111A] p-3 text-sm leading-6 text-slate-100 outline-none focus:border-teal-400" />
            </label>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" onClick={() => setStatus(`Prediction refreshed for ${symbol} at ${egyptNow()}.`)} className="inline-flex h-10 items-center justify-center gap-2 rounded bg-emerald-500 px-4 text-sm font-semibold text-[#04110F]"><PlayCircle size={16} /> Run Prediction</button>
            <button type="button" onClick={() => exportLessons(lessons)} className="inline-flex h-10 items-center justify-center gap-2 rounded border border-terminal-border bg-[#0B111A] px-4 text-sm text-slate-200"><Download size={16} /> Export Memory</button>
          </div>
        </section>
      </div>

      <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-lg font-bold text-white">{prediction.symbol} Consensus Forecast</h3>
              <ActionBadge action={prediction.action} />
              <span className={`rounded border px-2 py-1 text-xs font-semibold ${prediction.bias === "Bullish" ? "border-emerald-400/35 bg-emerald-500/10 text-emerald-300" : prediction.bias === "Bearish" ? "border-red-400/35 bg-red-500/10 text-red-300" : "border-amber-400/35 bg-amber-500/10 text-amber-300"}`}>{prediction.bias}</span>
              <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-xs text-slate-300">Confidence {prediction.confidence}%</span>
              <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-xs text-slate-300">Risk {prediction.riskLevel}</span>
            </div>
            <p className="mt-2 text-sm text-slate-400">{prediction.companyName}</p>
            <p className="mt-3 max-w-4xl text-sm leading-6 text-slate-200">{prediction.forecast}</p>
          </div>
          <div className="grid min-w-[320px] grid-cols-2 gap-3 rounded border border-terminal-border bg-[#0C131D] p-3">
            <Metric label="Current Price" value={currentPrice ? money(currentPrice) : "-"} accent="text-white" />
            <Metric label="Time Horizon" value={prediction.timeHorizon} />
            <Metric label="Strategy Frame" value={prediction.strategyFrame} />
            <Metric label="Entry Zone" value={prediction.entryZone} accent="text-emerald-300" />
            <Metric label="Targets" value={prediction.targets} accent="text-teal-300" />
            <Metric label="Stop" value={prediction.stop} accent="text-red-300" />
            <Metric label="Memory Score" value={prediction.memoryScore} accent="text-amber-300" />
          </div>
        </div>
        <div className="mt-4 grid gap-3 xl:grid-cols-4">
          <div className="rounded border border-terminal-border bg-[#0C131D] p-3">
            <div className="text-xs font-bold uppercase text-slate-500">Reasoning</div>
            <ul className="mt-2 space-y-2 text-sm leading-6 text-slate-300">{prediction.reasoning.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
          <div className="rounded border border-terminal-border bg-[#0C131D] p-3">
            <div className="text-xs font-bold uppercase text-slate-500">Strategy Memory Used</div>
            <ul className="mt-2 space-y-2 text-sm leading-6 text-slate-300">
              {visibleStrategyLessons.map((lesson) => (
                <li key={lesson.id}>
                  <span className={lesson.source === "strategy-core" ? "text-emerald-300" : "text-amber-300"}>{lesson.source === "strategy-core" ? "Core" : "User"}:</span> {lesson.text}
                </li>
              ))}
            </ul>
          </div>
          <div className="rounded border border-terminal-border bg-[#0C131D] p-3">
            <div className="text-xs font-bold uppercase text-slate-500">Checklist Before Action</div>
            <ul className="mt-2 space-y-2 text-sm leading-6 text-slate-300">{prediction.checklist.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
          <div className="rounded border border-terminal-border bg-[#0C131D] p-3">
            <div className="text-xs font-bold uppercase text-slate-500">Invalidation</div>
            <p className="mt-2 text-sm leading-6 text-slate-300">{prediction.invalidation}</p>
            <p className="mt-3 rounded border border-amber-400/30 bg-amber-500/10 p-2 text-xs leading-5 text-amber-100">{prediction.disclaimer}</p>
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="font-bold text-white">Bot Memory</h3>
            <p className="mt-1 text-xs text-slate-400">Stored locally in this browser. Export it before moving machines or clearing browser data.</p>
          </div>
          <button type="button" onClick={() => exportLessons(lessons)} className="inline-flex h-9 items-center justify-center gap-2 rounded border border-terminal-border bg-[#0B111A] px-3 text-xs font-semibold text-slate-200"><Save size={14} /> Export Memory</button>
        </div>
        <div className="mt-3 grid gap-3 xl:grid-cols-2">
          {lessons.map((lesson) => (
            <article key={lesson.id} className="rounded border border-terminal-border bg-[#0C131D] p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap gap-2">
                    <span className="rounded border border-teal-400/30 bg-teal-500/10 px-2 py-1 text-[11px] font-semibold text-teal-300">{lesson.category}</span>
                    <span className="rounded border border-terminal-border bg-terminal-card px-2 py-1 text-[11px] text-slate-300">{lesson.scopeSymbol}</span>
                    <span className="rounded border border-terminal-border bg-terminal-card px-2 py-1 text-[11px] text-slate-300">weight {lesson.weight}</span>
                  </div>
                  <p className="mt-3 text-sm leading-6 text-slate-200">{lesson.text}</p>
                  <div className="mt-2 text-xs text-slate-500">{lesson.tags.join(", ") || "No tags"} - {lesson.createdAtEgypt}</div>
                </div>
                <button type="button" onClick={() => removeLesson(lesson.id)} className="grid h-8 w-8 shrink-0 place-items-center rounded border border-terminal-border text-slate-400 hover:border-red-400/40 hover:text-red-300" aria-label="Delete lesson">
                  <Trash2 size={14} />
                </button>
              </div>
            </article>
          ))}
          {!lessons.length && <div className="rounded border border-terminal-border bg-[#0C131D] p-5 text-sm text-slate-400">No lessons yet. Start teaching the bot your trading rules and daily chart observations.</div>}
        </div>
      </section>
    </div>
  );
}
