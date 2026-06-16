import type { ActionNow, BestStock, Stock, TimeframeAnalysis } from "../types";

export type BotLessonCategory = "Strategy Rule" | "Chart Pattern" | "Daily Report" | "Risk Rule" | "Market Psychology" | "Personal Note";

export interface BotLesson {
  id: string;
  category: BotLessonCategory;
  text: string;
  scopeSymbol: string;
  tags: string[];
  weight: number;
  createdAtEgypt: string;
  source?: "user" | "strategy-core";
}

export interface BotPrediction {
  symbol: string;
  companyName: string;
  action: ActionNow;
  bias: "Bullish" | "Watch" | "Bearish" | "Unavailable";
  confidence: number;
  riskLevel: "Low" | "Medium" | "High" | "Unavailable";
  timeHorizon: string;
  forecast: string;
  entryZone: string;
  stop: string;
  targets: string;
  invalidation: string;
  matchedLessons: BotLesson[];
  reasoning: string[];
  checklist: string[];
  memoryScore: number;
  strategyName: string;
  strategyMode: "Aggressive" | "Balanced" | "Safe";
  strategyFrame: string;
  strategyApplied: boolean;
  strategySummary: string[];
  builtInLessonCount: number;
  userLessonCount: number;
  primaryStrategy: string;
  consensusScore: number;
  strategySignals: StrategySignal[];
  recommendationReason: string;
  strategyVoteSummary: StrategyVoteSummary;
  confirmations: string[];
  warnings: string[];
  originalStrategyAction: ActionNow;
  dataReliability: DataReliability;
  disclaimer: string;
}

export type StrategyStance = "Bullish" | "Watch" | "Bearish" | "Neutral" | "Unavailable";

export interface StrategyVoteSummary {
  bullish: number;
  watch: number;
  bearish: number;
  neutral: number;
  unavailable: number;
  agreementPercent: number;
}

export interface StrategySignal {
  id: string;
  name: string;
  stance: StrategyStance;
  score: number;
  confidence: number;
  reason: string;
  evidence: string[];
}

export interface BotDataStatus {
  activeProvider?: string;
  latestCandleAt?: string | null;
  latestCompletedCandleAt?: string | null;
  latestDataRefreshAt?: string | null;
  bidAskStatus?: "real" | "unavailable";
  realBidAskSnapshots?: number;
}

export interface DataReliability {
  grade: "High" | "Medium" | "Low" | "Unavailable";
  score: number;
  frameCoverage: string;
  latestDateEgypt: string;
  note: string;
}

interface ConsensusDecision {
  action: ActionNow;
  bias: BotPrediction["bias"];
  riskLevel: BotPrediction["riskLevel"];
  confidence: number;
  reason: string;
  voteSummary: StrategyVoteSummary;
  confirmations: string[];
  warnings: string[];
}

const positiveWords = ["buy", "bullish", "breakout", "accumulation", "support", "higher low", "volume up", "demand", "hold above", "pullback"];
const negativeWords = ["sell", "bearish", "distribution", "breakdown", "resistance reject", "weak", "supply", "avoid", "stop", "risk"];

const actionBias: Record<string, number> = {
  "BUY NOW": 24,
  "BREAKOUT BUY": 22,
  "PULLBACK BUY AREA": 18,
  "WATCH EARLY BUY": 12,
  "WAIT PULLBACK": 8,
  HOLD: 6,
  WATCH: 4,
  WAIT: 0,
  "REDUCE / TAKE PROFIT": -12,
  "DO NOT BUY NOW": -20,
  "SELL NOW": -26,
};

export const omarSmartProStrategyLessons: BotLesson[] = [
  {
    id: "omar-v3-range-filters",
    category: "Strategy Rule",
    text: "Omar Smart PRO V3 uses a fast range filter for early turns and a slow range filter for trend risk. Bullish decisions need price above the relevant filters; bearish or avoid decisions respect slow-filter breakdowns.",
    scopeSymbol: "ALL",
    tags: ["range filter", "fast filter", "slow filter", "trend"],
    weight: 5,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
  {
    id: "omar-v3-trend-stack",
    category: "Strategy Rule",
    text: "Trend context comes from EMA 21, EMA 50, EMA 200, and higher timeframe confirmation when available. SHORT BULLISH is tactical, SWING BULLISH is stronger, and LONG BULLISH is the strongest context.",
    scopeSymbol: "ALL",
    tags: ["ema", "trend", "higher timeframe", "swing", "long"],
    weight: 5,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
  {
    id: "omar-v3-pressure-volume",
    category: "Strategy Rule",
    text: "Volume and buy-sell pressure must confirm the setup. Buy pressure with strong or very strong volume improves confidence; sell pressure downgrades or blocks new buy decisions.",
    scopeSymbol: "ALL",
    tags: ["volume", "buy pressure", "sell pressure", "confirmation"],
    weight: 5,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
  {
    id: "omar-v3-entry-zone",
    category: "Risk Rule",
    text: "Entry is based on the ATR buy zone around the fast range filter. BUY NOW requires price inside or very near the zone; WAIT PULLBACK means the setup may be good but price is too high to chase.",
    scopeSymbol: "ALL",
    tags: ["entry", "buy zone", "atr", "pullback", "risk"],
    weight: 5,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
  {
    id: "omar-v3-action-map",
    category: "Strategy Rule",
    text: "Action Now is decided from score, pressure, trend, zone location, accumulation, pullback, breakout, and exit logic. SELL NOW and DO NOT BUY NOW override bullish hopes when risk conditions appear.",
    scopeSymbol: "ALL",
    tags: ["action", "score", "breakout", "accumulation", "exit"],
    weight: 5,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
  {
    id: "omar-v3-exit-discipline",
    category: "Risk Rule",
    text: "Smart exits use sell pressure, range-filter sell turns, RSI weakness, strong seller candles, EMA trend breakdowns, and active stop or target logic. Protection is part of the strategy, not an afterthought.",
    scopeSymbol: "ALL",
    tags: ["sell", "exit", "stop", "target", "rsi"],
    weight: 5,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
  {
    id: "expert-support-resistance",
    category: "Strategy Rule",
    text: "Support and resistance strategy: respect previous reaction zones, moving-average support, and ATR-adjusted invalidation. A clean setup has a defined level, room to target, and a clear stop.",
    scopeSymbol: "ALL",
    tags: ["support", "resistance", "moving average", "atr", "stop"],
    weight: 4,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
  {
    id: "expert-volume-breakout",
    category: "Strategy Rule",
    text: "Breakout strategy: prefer closes above resistance or recent highs only when volume is above average and buy pressure confirms demand. Breakouts without volume are watch-only.",
    scopeSymbol: "ALL",
    tags: ["breakout", "volume", "resistance", "demand"],
    weight: 4,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
  {
    id: "expert-relative-strength",
    category: "Strategy Rule",
    text: "Relative-strength strategy: stocks ranking near the top of the market with strong score, constructive trend, and positive pressure get priority over weak peers in the same market.",
    scopeSymbol: "ALL",
    tags: ["relative strength", "rank", "market leader", "momentum"],
    weight: 4,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
  {
    id: "expert-momentum-rsi",
    category: "Strategy Rule",
    text: "Momentum strategy: RSI above 50 but below overbought conditions supports continuation. RSI weakness below the exit zone or overextended RSI reduces confidence.",
    scopeSymbol: "ALL",
    tags: ["rsi", "momentum", "overbought", "weakness"],
    weight: 4,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
  {
    id: "expert-multi-timeframe",
    category: "Strategy Rule",
    text: "Multi-timeframe strategy: confidence improves when the strongest available timeframe aligns with the broader daily trend. Mixed frames reduce position size and conviction.",
    scopeSymbol: "ALL",
    tags: ["multi timeframe", "daily", "alignment", "trend"],
    weight: 4,
    createdAtEgypt: "Built-in",
    source: "strategy-core",
  },
];

export const omarSmartProStrategySummary = [
  "Fast and slow range filters detect early turns and trend breakdown risk.",
  "EMA 21 / 50 / 200 classify scalp, swing, and long-term trend quality.",
  "OHLCV buy-sell pressure plus volume MA decides whether demand is real.",
  "ATR buy zone, target, stop, and active risk levels control entry discipline.",
  "Action Now maps score, trend, pressure, breakout, pullback, accumulation, and smart exit logic.",
  "Additional expert lenses check trend quality, breakout volume, relative strength, RSI momentum, and risk/reward.",
];

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function includesAny(text: string, words: string[]) {
  return words.some((word) => text.includes(word));
}

function scoreLesson(text: string) {
  const normalized = text.toLowerCase();
  let score = 0;
  for (const word of positiveWords) if (normalized.includes(word)) score += 1;
  for (const word of negativeWords) if (normalized.includes(word)) score -= 1;
  return score;
}

function lessonMatches(lesson: BotLesson, symbol: string, query: string, report: string) {
  if (lesson.source === "strategy-core") return true;
  const haystack = `${query} ${report}`.toLowerCase();
  const symbolMatch = !lesson.scopeSymbol || lesson.scopeSymbol === "ALL" || lesson.scopeSymbol === symbol;
  const tagMatch = !lesson.tags.length || lesson.tags.some((tag) => haystack.includes(tag.toLowerCase()));
  const textMatch = haystack.length < 8 || lesson.text.toLowerCase().split(/\s+/).some((word) => word.length > 5 && haystack.includes(word));
  return symbolMatch && (tagMatch || textMatch || lesson.category === "Risk Rule");
}

function bestFrameFor(symbol: string, analyses: TimeframeAnalysis[]) {
  return analyses
    .filter((row) => row.symbol === symbol)
    .sort((a, b) => (b.score + (actionBias[b.actionNow] ?? 0) + b.riskReward) - (a.score + (actionBias[a.actionNow] ?? 0) + a.riskReward))[0];
}

function assessRiskLevel(row?: BestStock, frame?: TimeframeAnalysis): BotPrediction["riskLevel"] {
  if (!row && !frame) return "Unavailable";
  const action = frame?.actionNow ?? row?.bestAction ?? "WAIT";
  const rr = frame?.riskReward ?? row?.riskReward ?? 0;
  const pressure = frame?.pressure ?? row?.pressure ?? "Neutral";
  if (["SELL NOW", "DO NOT BUY NOW", "REDUCE / TAKE PROFIT"].includes(action)) return "High";
  if (pressure === "Sell Pressure" || rr < 1) return "High";
  if (rr >= 1.5 && pressure === "Buy Pressure") return "Medium";
  return "Medium";
}

function signal(
  id: string,
  name: string,
  stance: StrategyStance,
  score: number,
  confidence: number,
  reason: string,
  evidence: string[],
): StrategySignal {
  return {
    id,
    name,
    stance,
    score: Math.round(clamp(score, 0, 100)),
    confidence: Math.round(clamp(confidence, 0, 100)),
    reason,
    evidence,
  };
}

function isBullishAction(action?: ActionNow) {
  return action === "BUY NOW" || action === "BREAKOUT BUY" || action === "PULLBACK BUY AREA" || action === "WATCH EARLY BUY" || action === "HOLD";
}

function isRiskAction(action?: ActionNow) {
  return action === "SELL NOW" || action === "DO NOT BUY NOW" || action === "REDUCE / TAKE PROFIT";
}

function strategyStance(score: number, bearish = false): StrategyStance {
  if (bearish) return "Bearish";
  if (score >= 70) return "Bullish";
  if (score >= 52) return "Watch";
  if (score <= 34) return "Bearish";
  return "Neutral";
}

function formatNumber(value?: number) {
  return value === undefined || Number.isNaN(value) ? "-" : value.toFixed(2);
}

function egyptDateLabel(value?: string | null) {
  if (!value) return "Unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unavailable";
  return date.toLocaleDateString("en-GB", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit" });
}

function todayEgyptLabel() {
  return egyptDateLabel(new Date().toISOString());
}

export function assessDataReliability(symbol: string, row: BestStock | undefined, frames: TimeframeAnalysis[], dataStatus?: BotDataStatus): DataReliability {
  const relatedFrames = frames.filter((item) => item.symbol === symbol);
  const availableFrames = new Set(relatedFrames.map((item) => item.timeframe));
  const expectedFrames = ["15M", "30M", "1H", "4H", "1D"];
  const frameCoverage = `${availableFrames.size}/${expectedFrames.length}`;
  const sortedFrameDates = relatedFrames.map((item) => item.lastUpdateEgypt).sort();
  const latestRaw = dataStatus?.latestCompletedCandleAt ?? dataStatus?.latestCandleAt ?? sortedFrameDates[sortedFrameDates.length - 1];
  const latestDateEgypt = egyptDateLabel(latestRaw);
  const today = todayEgyptLabel();
  const isToday = latestDateEgypt !== "Unavailable" && latestDateEgypt === today;
  const hasPrice = row?.currentPrice !== undefined || relatedFrames.some((item) => item.currentPrice > 0);
  const hasDaily = availableFrames.has("1D");
  const hasIntraday = ["15M", "30M", "1H", "4H"].some((tf) => availableFrames.has(tf as TimeframeAnalysis["timeframe"]));
  const bidAskReal = dataStatus?.bidAskStatus === "real" || (dataStatus?.realBidAskSnapshots ?? 0) > 0;
  let score = 0;
  if (hasPrice) score += 24;
  if (hasDaily) score += 22;
  if (hasIntraday) score += 22;
  score += Math.min(20, availableFrames.size * 4);
  if (isToday) score += 8;
  if (bidAskReal) score += 4;
  if (row?.dataQuality === "unavailable") score = 0;
  if (!hasIntraday) score = Math.min(score, 62);
  if (!bidAskReal) score = Math.min(score, 88);
  const grade: DataReliability["grade"] = score >= 78 ? "High" : score >= 52 ? "Medium" : score > 0 ? "Low" : "Unavailable";
  const missing: string[] = [];
  if (!hasIntraday) missing.push("intraday frames");
  if (!bidAskReal) missing.push("real bid/ask");
  if (!isToday) missing.push("today's completed candle");
  const note = grade === "Unavailable"
    ? "No usable provider data is available for this symbol."
    : missing.length
      ? `Reliability is capped because ${missing.join(", ")} ${missing.length === 1 ? "is" : "are"} unavailable.`
      : "Provider coverage is strong for the configured data source.";
  return { grade, score: Math.round(clamp(score, 0, 100)), frameCoverage, latestDateEgypt, note };
}

function buildStrategySignals(symbol: string, row: BestStock | undefined, frames: TimeframeAnalysis[], bestRows: BestStock[], dataStatus?: BotDataStatus) {
  const frame = bestFrameFor(symbol, frames);
  const reliability = assessDataReliability(symbol, row, frames, dataStatus);
  if (!frame && !row) {
    return [
      signal("data-availability", "Data Availability", "Unavailable", 0, 0, "No usable OHLCV analysis exists for this symbol yet.", ["Connect daily or intraday candles before prediction."]),
    ];
  }

  const relatedFrames = frames.filter((item) => item.symbol === symbol);
  const action = frame?.actionNow ?? row?.bestAction ?? "WAIT";
  const score = frame?.score ?? row?.overallScore ?? 0;
  const rr = frame?.riskReward ?? row?.riskReward ?? 0;
  const pressure = frame?.pressure ?? row?.pressure ?? "Neutral";
  const volume = frame?.volumeStatus ?? row?.volumeStatus ?? "Normal";
  const bullishFrames = relatedFrames.filter((item) => item.mainTrend.includes("BULLISH")).length;
  const bearishFrames = relatedFrames.filter((item) => item.mainTrend === "BEARISH" || isRiskAction(item.actionNow)).length;
  const avgFrameScore = relatedFrames.length ? relatedFrames.reduce((sum, item) => sum + item.score, 0) / relatedFrames.length : score;
  const inBuyZone = frame ? frame.currentPrice >= frame.buyZoneLow && frame.currentPrice <= frame.buyZoneHigh : false;
  const nearBuyZone = frame ? frame.currentPrice <= frame.buyZoneHigh + frame.atr * 0.25 && frame.currentPrice >= frame.buyZoneLow - frame.atr * 0.25 : false;
  const priceAboveZone = frame ? frame.currentPrice > frame.buyZoneHigh : false;
  const emaStackBullish = frame ? frame.currentPrice > frame.ema21 && frame.ema21 > frame.ema50 && frame.ema50 > frame.ema200 : false;
  const emaSwingBullish = frame ? frame.currentPrice > frame.ema50 && frame.ema21 > frame.ema50 : false;
  const belowRiskTrend = frame ? frame.currentPrice < frame.ema50 || frame.currentPrice < frame.slowRangeFilter : false;
  const rankedUniverse = bestRows.length || 1;
  const rankPercentile = row ? 1 - (row.rank - 1) / rankedUniverse : 0;

  const omarScore = clamp(score * 8 + (actionBias[action] ?? 0) + rr * 3, 0, 100);
  const alignmentScore = clamp(avgFrameScore * 8 + bullishFrames * 8 - bearishFrames * 14, 0, 100);
  const trendScore = emaStackBullish ? 86 : emaSwingBullish ? 70 : frame?.currentPrice && frame.currentPrice > frame.ema21 ? 56 : belowRiskTrend ? 26 : 44;
  const breakoutScore = frame?.breakoutStatus && pressure === "Buy Pressure" && (volume === "Strong" || volume === "Very Strong")
    ? 88
    : frame?.breakoutStatus
      ? 58
      : pressure === "Sell Pressure"
        ? 30
        : 46;
  const pullbackScore = (inBuyZone || nearBuyZone) && frame?.mainTrend.includes("BULLISH") && pressure === "Buy Pressure"
    ? 84
    : priceAboveZone && frame?.mainTrend.includes("BULLISH")
      ? 58
      : belowRiskTrend
        ? 32
        : 48;
  const volumeScore = frame?.earlyAccumulationStatus && pressure === "Buy Pressure"
    ? 82
    : pressure === "Buy Pressure" && (volume === "Strong" || volume === "Very Strong")
      ? 76
      : pressure === "Sell Pressure" && (volume === "Strong" || volume === "Very Strong")
        ? 24
        : 48;
  const rsiScore = frame?.rsi === undefined
    ? 50
    : frame.rsi > 50 && frame.rsi < 70
      ? 74
      : frame.rsi >= 70 && frame.rsi < 80
        ? 60
        : frame.rsi >= 80
          ? 38
          : frame.rsi < 42
            ? 28
            : 48;
  const rrScore = rr >= 2 ? 82 : rr >= 1.4 ? 66 : rr >= 1 ? 52 : 30;
  const relativeScore = row ? clamp(row.overallScore * 8 + rankPercentile * 24 + (row.changePercent ?? 0) * 2, 0, 100) : 45;
  const defenseRisk = pressure === "Sell Pressure" || belowRiskTrend || isRiskAction(action);
  const defenseScore = defenseRisk ? 74 : 42;

  return [
    signal(
      "data-reliability",
      "Data Reliability / Provider Freshness",
      reliability.grade === "High" ? "Bullish" : reliability.grade === "Medium" ? "Watch" : reliability.grade === "Unavailable" ? "Unavailable" : "Neutral",
      reliability.score,
      Math.max(35, reliability.score),
      reliability.note,
      [`Frames: ${reliability.frameCoverage}`, `Latest Egypt date: ${reliability.latestDateEgypt}`, `Provider: ${dataStatus?.activeProvider ?? "unknown"}`],
    ),
    signal(
      "omar-smart-pro-v3",
      "Omar Smart PRO V3",
      strategyStance(omarScore, isRiskAction(action)),
      omarScore,
      Math.abs(omarScore - 50) + 45,
      `${action} from score ${score}/10, ${pressure.toLowerCase()}, ${volume.toLowerCase()} volume.`,
      [`Frame: ${frame?.timeframe ?? row?.bestFrame ?? "-"}`, `Plan: ${frame?.plan ?? row?.plan ?? "-"}`, `Advice: ${frame?.advice ?? row?.reason ?? "-"}`],
    ),
    signal(
      "multi-timeframe-alignment",
      "Multi-Timeframe Alignment",
      strategyStance(alignmentScore, bearishFrames > bullishFrames && bearishFrames > 0),
      alignmentScore,
      Math.abs(alignmentScore - 50) + 42,
      `${bullishFrames}/${relatedFrames.length || 1} available frame(s) are bullish; ${bearishFrames} show bearish or exit risk.`,
      [`Average frame score: ${formatNumber(avgFrameScore)}/10`, `Best frame: ${frame?.timeframe ?? row?.bestFrame ?? "-"}`],
    ),
    signal(
      "ema-trend-quality",
      "EMA Trend Quality",
      strategyStance(trendScore, belowRiskTrend && pressure === "Sell Pressure"),
      trendScore,
      Math.abs(trendScore - 50) + 42,
      emaStackBullish ? "Price and EMA stack show strong trend quality." : emaSwingBullish ? "Swing trend is constructive but not fully long-term aligned." : belowRiskTrend ? "Price is below a key trend filter." : "Trend quality is mixed.",
      [`Price: ${formatNumber(frame?.currentPrice)}`, `EMA21/50/200: ${formatNumber(frame?.ema21)} / ${formatNumber(frame?.ema50)} / ${formatNumber(frame?.ema200)}`],
    ),
    signal(
      "volume-breakout-confirmation",
      "Volume Breakout Confirmation",
      strategyStance(breakoutScore, pressure === "Sell Pressure" && !frame?.breakoutStatus),
      breakoutScore,
      Math.abs(breakoutScore - 50) + 42,
      frame?.breakoutStatus ? "Breakout condition is active; volume and pressure decide confirmation quality." : "No confirmed breakout condition on the selected strategy frame.",
      [`Breakout: ${frame?.breakoutStatus ? "Yes" : "No"}`, `Volume: ${volume}`, `Pressure: ${pressure}`],
    ),
    signal(
      "pullback-value-zone",
      "Pullback To Value Zone",
      strategyStance(pullbackScore, belowRiskTrend && pressure === "Sell Pressure"),
      pullbackScore,
      Math.abs(pullbackScore - 50) + 42,
      inBuyZone ? "Price is inside the ATR buy zone." : nearBuyZone ? "Price is near the ATR buy zone." : priceAboveZone ? "Setup may require patience because price is above safe value." : "Price is not near a high-quality buy zone.",
      [`Buy zone: ${formatNumber(frame?.buyZoneLow)} - ${formatNumber(frame?.buyZoneHigh)}`, `Current: ${formatNumber(frame?.currentPrice)}`],
    ),
    signal(
      "volume-pressure-accumulation",
      "Volume Pressure / Accumulation",
      strategyStance(volumeScore, pressure === "Sell Pressure" && (volume === "Strong" || volume === "Very Strong")),
      volumeScore,
      Math.abs(volumeScore - 50) + 42,
      frame?.earlyAccumulationStatus ? "Early accumulation is active." : pressure === "Buy Pressure" ? "Buyer pressure is present; volume decides quality." : pressure === "Sell Pressure" ? "Seller pressure is active; protect capital." : "Pressure is neutral.",
      [`Pressure: ${pressure}`, `Volume: ${volume}`, `Early accumulation: ${frame?.earlyAccumulationStatus ? "Yes" : "No"}`],
    ),
    signal(
      "rsi-momentum-health",
      "RSI Momentum Health",
      strategyStance(rsiScore, (frame?.rsi ?? 50) < 42),
      rsiScore,
      Math.abs(rsiScore - 50) + 42,
      frame?.rsi === undefined ? "RSI is unavailable." : frame.rsi > 50 && frame.rsi < 80 ? "RSI supports momentum without an exit weakness signal." : frame.rsi >= 80 ? "RSI is extended; avoid chasing." : frame.rsi < 42 ? "RSI is weak enough to warn about exit risk." : "RSI is neutral.",
      [`RSI: ${formatNumber(frame?.rsi)}`],
    ),
    signal(
      "risk-reward-discipline",
      "Risk / Reward Discipline",
      strategyStance(rrScore, rr < 1),
      rrScore,
      Math.abs(rrScore - 50) + 42,
      rr >= 1.4 ? "Reward is acceptable versus the ATR stop." : rr >= 1 ? "Risk/reward is borderline; require stronger confirmation." : "Risk/reward is poor; avoid forcing the trade.",
      [`Entry: ${formatNumber(frame?.suggestedEntry ?? row?.entry)}`, `Target: ${formatNumber(frame?.suggestedTarget ?? row?.target)}`, `Stop: ${formatNumber(frame?.suggestedStop ?? row?.stop)}`, `R/R: ${formatNumber(rr)}`],
    ),
    signal(
      "relative-strength-ranking",
      "Relative Strength / Market Leadership",
      strategyStance(relativeScore),
      relativeScore,
      Math.abs(relativeScore - 50) + 42,
      row ? `Rank ${row.rank} in the current scanner with overall score ${row.overallScore}/10.` : "No ranking row is available.",
      [`Rank percentile: ${row ? `${Math.round(rankPercentile * 100)}%` : "-"}`, `Change: ${row?.changePercent === undefined ? "-" : `${row.changePercent.toFixed(2)}%`}`],
    ),
    signal(
      "defensive-risk-filter",
      "Defensive Risk Filter",
      defenseRisk ? "Bearish" : "Neutral",
      defenseScore,
      defenseRisk ? 82 : 52,
      defenseRisk ? "One or more risk filters are active; new buying needs extra caution." : "No major defensive risk filter is active.",
      [`Below trend filter: ${belowRiskTrend ? "Yes" : "No"}`, `Risk action: ${isRiskAction(action) ? "Yes" : "No"}`, `Pressure: ${pressure}`],
    ),
  ];
}

function strategyConsensusScore(signals: StrategySignal[]) {
  if (!signals.length) return 0;
  const total = signals.reduce((sum, item) => {
    const direction = item.stance === "Bullish" ? 1 : item.stance === "Watch" ? 0.35 : item.stance === "Neutral" ? 0 : item.stance === "Bearish" ? -1 : -0.5;
    return sum + direction * item.confidence;
  }, 0);
  return Math.round(clamp(50 + total / Math.max(signals.length, 1) / 1.7, 0, 100));
}

function primaryStrategy(signals: StrategySignal[]) {
  const actionable = signals.filter((item) => item.stance === "Bullish" || item.stance === "Bearish");
  return (actionable.length ? actionable : signals).sort((a, b) => b.confidence - a.confidence)[0]?.name ?? "No strategy";
}

function voteSummary(signals: StrategySignal[]): StrategyVoteSummary {
  const summary = signals.reduce(
    (acc, item) => {
      if (item.stance === "Bullish") acc.bullish += 1;
      else if (item.stance === "Watch") acc.watch += 1;
      else if (item.stance === "Bearish") acc.bearish += 1;
      else if (item.stance === "Unavailable") acc.unavailable += 1;
      else acc.neutral += 1;
      return acc;
    },
    { bullish: 0, watch: 0, bearish: 0, neutral: 0, unavailable: 0, agreementPercent: 0 },
  );
  const total = Math.max(signals.length, 1);
  const strongestSide = Math.max(summary.bullish + summary.watch * 0.5, summary.bearish, summary.neutral, summary.unavailable);
  return { ...summary, agreementPercent: Math.round((strongestSide / total) * 100) };
}

function topSignals(signals: StrategySignal[], stance: StrategyStance | StrategyStance[], limit: number) {
  const stances = Array.isArray(stance) ? stance : [stance];
  return signals
    .filter((item) => stances.includes(item.stance))
    .sort((a, b) => b.confidence - a.confidence || b.score - a.score)
    .slice(0, limit)
    .map((item) => `${item.name}: ${item.reason}`);
}

function determineConsensusDecision(args: {
  originalAction: ActionNow;
  consensusScore: number;
  confidenceBase: number;
  signals: StrategySignal[];
  row?: BestStock;
  frame?: TimeframeAnalysis;
  reliability: DataReliability;
}): ConsensusDecision {
  const { originalAction, consensusScore, confidenceBase, signals, row, frame, reliability } = args;
  const summary = voteSummary(signals);
  const confirmations = topSignals(signals, ["Bullish", "Watch"], 4);
  const warnings = topSignals(signals, "Bearish", 4);
  const pressure = frame?.pressure ?? row?.pressure ?? "Neutral";
  const volume = frame?.volumeStatus ?? row?.volumeStatus ?? "Normal";
  const rr = frame?.riskReward ?? row?.riskReward ?? 0;
  const inBuyZone = frame ? frame.currentPrice >= frame.buyZoneLow && frame.currentPrice <= frame.buyZoneHigh : false;
  const nearBuyZone = frame ? frame.currentPrice <= frame.buyZoneHigh + frame.atr * 0.25 && frame.currentPrice >= frame.buyZoneLow - frame.atr * 0.25 : false;
  const priceAboveZone = frame ? frame.currentPrice > frame.buyZoneHigh : false;
  const breakout = Boolean(frame?.breakoutStatus);
  const earlyAccumulation = Boolean(frame?.earlyAccumulationStatus);
  const positionOpen = frame?.positionState === "IN TRADE";
  const strongVolume = volume === "Strong" || volume === "Very Strong";
  const sellPressure = pressure === "Sell Pressure";
  const buyPressure = pressure === "Buy Pressure";
  const defensiveRisk = signals.some((item) => item.id === "defensive-risk-filter" && item.stance === "Bearish");
  const bullishWeight = signals.reduce((sum, item) => sum + (item.stance === "Bullish" ? item.confidence : item.stance === "Watch" ? item.confidence * 0.35 : 0), 0);
  const bearishWeight = signals.reduce((sum, item) => sum + (item.stance === "Bearish" ? item.confidence : 0), 0);
  let action: ActionNow = "WAIT";
  let bias: BotPrediction["bias"] = "Watch";
  let riskLevel: BotPrediction["riskLevel"] = assessRiskLevel(row, frame);

  if (isRiskAction(originalAction) && (sellPressure || defensiveRisk || consensusScore <= 42)) {
    action = originalAction === "REDUCE / TAKE PROFIT" && positionOpen ? "REDUCE / TAKE PROFIT" : originalAction === "SELL NOW" && positionOpen ? "SELL NOW" : "DO NOT BUY NOW";
    bias = "Bearish";
    riskLevel = "High";
  } else if (sellPressure && strongVolume && bearishWeight >= bullishWeight * 0.85) {
    action = positionOpen ? "SELL NOW" : "DO NOT BUY NOW";
    bias = "Bearish";
    riskLevel = "High";
  } else if (consensusScore <= 35 || bearishWeight > bullishWeight * 1.25) {
    action = positionOpen ? "REDUCE / TAKE PROFIT" : "DO NOT BUY NOW";
    bias = "Bearish";
    riskLevel = "High";
  } else if (positionOpen && consensusScore >= 52 && !sellPressure) {
    action = "HOLD";
    bias = consensusScore >= 62 ? "Bullish" : "Watch";
  } else if (consensusScore >= 72 && summary.bullish >= 4 && buyPressure && strongVolume && rr >= 1 && reliability.score >= 52) {
    action = breakout ? "BREAKOUT BUY" : inBuyZone ? "BUY NOW" : nearBuyZone ? "PULLBACK BUY AREA" : "WAIT PULLBACK";
    bias = "Bullish";
  } else if (consensusScore >= 62 && summary.bullish + summary.watch >= 5 && buyPressure && rr >= 1 && reliability.score >= 42) {
    action = priceAboveZone ? "WAIT PULLBACK" : earlyAccumulation ? "WATCH EARLY BUY" : nearBuyZone ? "PULLBACK BUY AREA" : "WATCH";
    bias = action === "WATCH" || action === "WAIT PULLBACK" ? "Watch" : "Bullish";
  } else if (consensusScore >= 52 || summary.watch >= 3) {
    action = buyPressure ? "WATCH EARLY BUY" : "WATCH";
    bias = "Watch";
  } else {
    action = sellPressure || defensiveRisk ? "DO NOT BUY NOW" : "WAIT";
    bias = sellPressure || defensiveRisk ? "Bearish" : "Watch";
  }

  const reliabilityPenalty = reliability.score < 52 ? (52 - reliability.score) * 0.35 : 0;
  const confidenceCeiling = reliability.score >= 78 ? 94 : reliability.score >= 52 ? 84 : reliability.score > 0 ? 68 : 35;
  const confidence = Math.round(clamp(confidenceBase + (consensusScore - 50) * 0.25 + (summary.agreementPercent - 50) * 0.18 - reliabilityPenalty, 18, confidenceCeiling));
  const reason = [
    `Final action is ${action} because the full strategy stack scored ${consensusScore}/100 with ${summary.bullish} bullish, ${summary.watch} watch, ${summary.bearish} bearish, and ${summary.neutral} neutral lens(es).`,
    `Data reliability is ${reliability.grade} (${reliability.score}/100): ${reliability.note}`,
    confirmations.length ? `Main confirmations: ${confirmations.slice(0, 2).join(" | ")}.` : "No strong bullish confirmation is available.",
    warnings.length ? `Main warnings: ${warnings.slice(0, 2).join(" | ")}.` : "No major bearish warning dominates the stack.",
  ].join(" ");

  return {
    action,
    bias,
    riskLevel,
    confidence,
    reason,
    voteSummary: summary,
    confirmations,
    warnings,
  };
}

export function buildBotPrediction(params: {
  symbol: string;
  stocks: Stock[];
  bestRows: BestStock[];
  analyses: TimeframeAnalysis[];
  lessons: BotLesson[];
  question: string;
  dailyReport: string;
  dataStatus?: BotDataStatus;
}): BotPrediction {
  const symbol = params.symbol.toUpperCase();
  const stock = params.stocks.find((item) => item.symbol === symbol);
  const row = params.bestRows.find((item) => item.symbol === symbol);
  const frame = bestFrameFor(symbol, params.analyses);
  const builtInLessons = omarSmartProStrategyLessons
    .filter((lesson) => lessonMatches(lesson, symbol, params.question, params.dailyReport))
    .slice(0, 6);
  const userMatchedLessons = params.lessons
    .filter((lesson) => lessonMatches(lesson, symbol, params.question, params.dailyReport))
    .sort((a, b) => b.weight - a.weight)
    .slice(0, 8);
  const matchedLessons = [...builtInLessons, ...userMatchedLessons];
  const dataReliability = assessDataReliability(symbol, row, params.analyses, params.dataStatus);
  const strategySignals = buildStrategySignals(symbol, row, params.analyses, params.bestRows, params.dataStatus);
  const consensusScore = strategyConsensusScore(strategySignals);
  const mainStrategy = primaryStrategy(strategySignals);

  if (!stock || (!row && !frame)) {
    const unavailableDecision = determineConsensusDecision({
      originalAction: "WAIT",
      consensusScore,
      confidenceBase: 0,
      signals: strategySignals,
      row,
      frame,
      reliability: dataReliability,
    });
    return {
      symbol,
      companyName: stock?.companyName ?? symbol,
      action: unavailableDecision.action,
      bias: "Unavailable",
      confidence: unavailableDecision.confidence,
      riskLevel: "Unavailable",
      timeHorizon: "No forecast",
      forecast: "No usable provider analysis is available for this symbol yet.",
      entryZone: "-",
      stop: "-",
      targets: "-",
      invalidation: "Wait until real OHLCV data is available.",
      matchedLessons,
      reasoning: ["The bot refuses to predict from missing data."],
      checklist: ["Connect real candles for this symbol", "Refresh provider data", "Avoid decisions without a chart"],
      memoryScore: 0,
      strategyName: "Omar Smart PRO V3",
      strategyMode: "Balanced",
      strategyFrame: "Unavailable",
      strategyApplied: false,
      strategySummary: omarSmartProStrategySummary,
      builtInLessonCount: builtInLessons.length,
      userLessonCount: userMatchedLessons.length,
      primaryStrategy: mainStrategy,
      consensusScore,
      strategySignals,
      recommendationReason: "No final recommendation is produced because the symbol has no usable OHLCV analysis yet.",
      strategyVoteSummary: unavailableDecision.voteSummary,
      confirmations: unavailableDecision.confirmations,
      warnings: unavailableDecision.warnings,
      originalStrategyAction: "WAIT",
      dataReliability,
      disclaimer: "Not financial advice. Educational analysis only.",
    };
  }

  const originalAction = frame?.actionNow ?? row?.bestAction ?? "WAIT";
  const score = frame?.score ?? row?.overallScore ?? 0;
  const rr = frame?.riskReward ?? row?.riskReward ?? 0;
  const pressure = frame?.pressure ?? row?.pressure ?? "Neutral";
  const volume = frame?.volumeStatus ?? row?.volumeStatus ?? "Weak";
  const trend = frame?.mainTrend ?? "NEUTRAL";
  const memoryScore = userMatchedLessons.reduce((sum, lesson) => sum + scoreLesson(lesson.text) * lesson.weight, 0);
  const reportScore = scoreLesson(`${params.question} ${params.dailyReport}`) * 4;
  const confidenceBase = 42 + score * 4.5 + (actionBias[originalAction] ?? 0) + (rr > 1.5 ? 7 : 0) + (pressure === "Buy Pressure" ? 7 : pressure === "Sell Pressure" ? -8 : 0) + memoryScore + reportScore;
  const decision = determineConsensusDecision({
    originalAction,
    consensusScore,
    confidenceBase,
    signals: strategySignals,
    row,
    frame,
    reliability: dataReliability,
  });
  const forecast = decision.bias === "Bullish"
    ? `${symbol} has a constructive setup if price respects the entry zone and buyer pressure continues.`
    : decision.bias === "Bearish"
      ? `${symbol} has distribution or exit risk now; the bot prefers protection before new exposure.`
      : `${symbol} is a watchlist candidate, but confirmation is not strong enough for a clean action.`;

  const entryLow = frame?.buyZoneLow ?? row?.entry;
  const entryHigh = frame?.buyZoneHigh ?? row?.entry;
  const target = frame?.suggestedTarget ?? row?.target;
  const stop = frame?.suggestedStop ?? row?.stop;

  return {
    symbol,
    companyName: stock?.companyName ?? row?.companyName ?? symbol,
    action: decision.action,
    bias: decision.bias,
    confidence: decision.confidence,
    riskLevel: decision.riskLevel,
    timeHorizon: frame?.timeframe === "1D" || row?.bestFrame === "1D" ? "Daily swing / position context" : `${frame?.timeframe ?? row?.bestFrame} tactical context`,
    forecast,
    entryZone: entryLow && entryHigh ? `${entryLow.toFixed(2)} - ${entryHigh.toFixed(2)}` : "-",
    stop: stop ? stop.toFixed(2) : "-",
    targets: target ? `${target.toFixed(2)} first target` : "-",
    invalidation: stop ? `Forecast weakens if price closes below ${stop.toFixed(2)} or sell pressure expands.` : "Forecast weakens if real candle data disappears.",
    matchedLessons,
    reasoning: [
      `Applied Omar Smart PRO V3 ${frame?.signalMode ?? "Balanced"} on ${frame?.timeframe ?? row?.bestFrame} data first.`,
      `Expert strategy stack consensus is ${consensusScore}/100; strongest lens now is ${mainStrategy}.`,
      `Data reliability is ${dataReliability.grade} (${dataReliability.score}/100), frame coverage ${dataReliability.frameCoverage}, latest date ${dataReliability.latestDateEgypt}.`,
      `Original strategy action was ${originalAction}; final consensus action is ${decision.action}.`,
      `Trend: ${trend}; Omar score: ${score}/10.`,
      `Pressure is ${pressure.toLowerCase()} with ${volume.toLowerCase()} volume and risk/reward ${rr || "-"}.`,
      decision.reason,
      userMatchedLessons.length ? `Adjusted with ${userMatchedLessons.length} matching user lesson(s) after the core strategy.` : "No matching user lessons yet; prediction uses the core strategy only.",
      params.dailyReport.trim() ? "Daily report text was included as context." : "No daily report was provided for this run.",
    ],
    checklist: [
      "Start with Omar Smart PRO V3 action, score, trend, pressure, and buy zone.",
      "Require at least two independent strategy lenses to agree before treating a signal as high quality.",
      "Confirm the latest candle date before acting.",
      "Check whether price is inside or far above the buy zone.",
      "Do not chase WAIT PULLBACK setups above the safe entry zone.",
      "Treat strong sell pressure as a risk warning even in bullish trends.",
      "Use stop and target levels; do not average down without a rule.",
    ],
    memoryScore: Number(memoryScore.toFixed(1)),
    strategyName: "Omar Smart PRO V3",
    strategyMode: frame?.signalMode ?? "Balanced",
    strategyFrame: frame?.timeframe ?? row?.bestFrame ?? "Unavailable",
    strategyApplied: Boolean(frame || row),
    strategySummary: omarSmartProStrategySummary,
    builtInLessonCount: builtInLessons.length,
    userLessonCount: userMatchedLessons.length,
    primaryStrategy: mainStrategy,
    consensusScore,
    strategySignals,
    recommendationReason: decision.reason,
    strategyVoteSummary: decision.voteSummary,
    confirmations: decision.confirmations,
    warnings: decision.warnings,
    originalStrategyAction: originalAction,
    dataReliability,
    disclaimer: "Not financial advice. This is an educational probability-style forecast, not a guarantee or trade instruction.",
  };
}
