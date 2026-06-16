import type { ActionNow, Candle, MainTrend, MarketTimeframeAnalysis, Plan, Recommendation, VolumeStatus } from "../types.js";

const avg = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / Math.max(values.length, 1);
const round = (value: number, digits = 2) => Number(value.toFixed(digits));

export function sma(values: number[], period: number) {
  if (values.length < period) return null;
  return round(avg(values.slice(-period)));
}

export function ema(values: number[], period: number) {
  if (!values.length) return null;
  const k = 2 / (period + 1);
  return round(values.reduce((prev, price, index) => (index === 0 ? price : price * k + prev * (1 - k)), values[0]));
}

export function rsi(values: number[], period = 14) {
  if (values.length <= period) return null;
  const slice = values.slice(-period - 1);
  let gains = 0;
  let losses = 0;
  for (let i = 1; i < slice.length; i += 1) {
    const change = slice[i] - slice[i - 1];
    if (change >= 0) gains += change;
    else losses += Math.abs(change);
  }
  if (losses === 0) return 100;
  const rs = gains / period / (losses / period);
  return round(100 - 100 / (1 + rs));
}

export function atr(candles: Candle[], period = 14) {
  if (candles.length <= period) return null;
  const slice = candles.slice(-period - 1);
  const ranges = slice.slice(1).map((candle, index) => {
    const prevClose = slice[index].close;
    return Math.max(candle.high - candle.low, Math.abs(candle.high - prevClose), Math.abs(candle.low - prevClose));
  });
  return round(avg(ranges));
}

export function macd(values: number[]) {
  const fast = ema(values, 12);
  const slow = ema(values, 26);
  if (fast === null || slow === null) return null;
  const line = round(fast - slow);
  return { macd: line, signal: null as number | null, histogram: null as number | null };
}

export function bollinger(values: number[], period = 20) {
  if (values.length < period) return null;
  const slice = values.slice(-period);
  const middle = avg(slice);
  const variance = avg(slice.map((value) => (value - middle) ** 2));
  const sd = Math.sqrt(variance);
  return { upper: round(middle + sd * 2), middle: round(middle), lower: round(middle - sd * 2) };
}

export function supportResistance(candles: Candle[], lookback = 30) {
  const slice = candles.slice(-lookback);
  if (!slice.length) return { support: null, resistance: null };
  return { support: round(Math.min(...slice.map((c) => c.low))), resistance: round(Math.max(...slice.map((c) => c.high))) };
}

export function calculateIndicators(candles: Candle[]) {
  const closes = candles.map((c) => c.close);
  const volumes = candles.map((c) => c.volume);
  return {
    sma20: sma(closes, 20),
    sma50: sma(closes, 50),
    sma200: sma(closes, 200),
    ema9: ema(closes, 9),
    ema21: ema(closes, 21),
    rsi14: rsi(closes, 14),
    macd: macd(closes),
    bollingerBands: bollinger(closes, 20),
    atr14: atr(candles, 14),
    volumeAverage20: volumes.length >= 20 ? round(avg(volumes.slice(-20))) : null,
    ...supportResistance(candles),
  };
}

export function recommendation(candles: Candle[]): { recommendation: Recommendation; confidence: number; reason: string } {
  if (candles.length < 20) return { recommendation: "WATCH", confidence: 35, reason: "Insufficient candle history for Omar Smart PRO V3 analysis." };
  const analysis = buildTimeframeAnalysis(candles[0]?.symbol ?? "EGX", "1D", candles, true);
  if (!analysis) return { recommendation: "WATCH", confidence: 35, reason: "No usable candles for Omar Smart PRO V3 analysis." };
  const buyActions: ActionNow[] = ["BUY NOW", "BREAKOUT BUY", "PULLBACK BUY AREA"];
  const watchActions: ActionNow[] = ["WATCH EARLY BUY", "WAIT PULLBACK", "HOLD", "WATCH", "WAIT"];
  const sellActions: ActionNow[] = ["SELL NOW", "REDUCE / TAKE PROFIT"];
  const confidence = Math.min(95, Math.max(35, 42 + analysis.score * 5 + (analysis.riskReward > 1.5 ? 8 : 0)));
  if (buyActions.includes(analysis.actionNow) && analysis.score >= 6) {
    return { recommendation: "BUY", confidence, reason: `Omar Smart PRO V3 ${analysis.signalMode}: ${analysis.advice} Score ${analysis.score}/10, ${analysis.pressure.toLowerCase()}, ${analysis.volumeStatus.toLowerCase()} volume.` };
  }
  if (watchActions.includes(analysis.actionNow) || buyActions.includes(analysis.actionNow)) {
    return { recommendation: "WATCH", confidence, reason: `Omar Smart PRO V3 ${analysis.signalMode}: ${analysis.advice} Score ${analysis.score}/10.` };
  }
  if (sellActions.includes(analysis.actionNow)) {
    return { recommendation: "SELL", confidence, reason: `Omar Smart PRO V3 ${analysis.signalMode}: ${analysis.advice} Score ${analysis.score}/10, ${analysis.pressure.toLowerCase()}.` };
  }
  return { recommendation: "AVOID", confidence, reason: `Omar Smart PRO V3 ${analysis.signalMode}: ${analysis.advice} Score ${analysis.score}/10, ${analysis.pressure.toLowerCase()}.` };
}

type StrategyMode = "Aggressive" | "Balanced" | "Safe";

const STRATEGY_MODE: StrategyMode = "Balanced";
const modeSettings = {
  Aggressive: { fastPer: 8, fastMult: 2.0, volMult: 1.0, pressureMult: 1.0, cooldownBars: 1, minScore: 4 },
  Balanced: { fastPer: 13, fastMult: 2.6, volMult: 1.15, pressureMult: 1.12, cooldownBars: 3, minScore: 5 },
  Safe: { fastPer: 20, fastMult: 3.3, volMult: 1.35, pressureMult: 1.28, cooldownBars: 6, minScore: 6 },
} satisfies Record<StrategyMode, { fastPer: number; fastMult: number; volMult: number; pressureMult: number; cooldownBars: number; minScore: number }>;

const strategyInputs = {
  slowPer: 45,
  slowMult: 5.0,
  emaFastLen: 21,
  emaMidLen: 50,
  emaLongLen: 200,
  htfEmaLen: 50,
  volLen: 20,
  rsiLen: 14,
  rsiEarly: 43,
  rsiConfirm: 50,
  rsiExit: 42,
  rsiOver: 80,
  atrLen: 14,
  buyZoneAtr: 0.4,
  stopAtr: 1.6,
  trailAtr: 2.2,
  targetScalpPct: 2.5,
  targetSwingPct: 6,
  targetLongPct: 12,
};

function emaRawSeries(values: number[], period: number): number[] {
  if (!values.length) return [];
  const k = 2 / (period + 1);
  const output: number[] = [];
  values.forEach((value, index) => output.push(index === 0 ? value : value * k + output[index - 1] * (1 - k)));
  return output;
}

function pressureFromParts(candle: Pick<Candle, "open" | "high" | "low" | "close" | "volume">, pressureMult = 1.12) {
  const candleRange = Math.max(candle.high - candle.low, 0.0001);
  const bodySize = Math.abs(candle.close - candle.open);
  const bodyPct = bodySize / candleRange;
  const closePosition = (candle.close - candle.low) / candleRange;
  const buyPressure = closePosition * candle.volume;
  const sellPressure = (1 - closePosition) * candle.volume;
  const buyersOk = buyPressure >= sellPressure * pressureMult;
  const sellersOk = sellPressure >= buyPressure * pressureMult;
  return {
    bodyPct,
    closePosition,
    buyersOk,
    sellersOk,
    pressure: buyersOk ? "Buy Pressure" as const : sellersOk ? "Sell Pressure" as const : "Neutral" as const,
    strongBuyerCandle: candle.close > candle.open && closePosition > 0.6 && bodyPct > 0.35,
    strongSellerCandle: candle.close < candle.open && closePosition < 0.4 && bodyPct > 0.35,
  };
}

function rsiSeries(closes: number[], period: number): number[] {
  const output = Array(closes.length).fill(50);
  if (closes.length <= period) return output;
  let gains = 0;
  let losses = 0;
  for (let i = 1; i <= period; i += 1) {
    const change = closes[i] - closes[i - 1];
    if (change >= 0) gains += change;
    else losses += Math.abs(change);
  }
  let avgGain = gains / period;
  let avgLoss = losses / period;
  output[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < closes.length; i += 1) {
    const change = closes[i] - closes[i - 1];
    avgGain = (avgGain * (period - 1) + Math.max(change, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-change, 0)) / period;
    output[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return output;
}

function atrRawSeries(candles: Candle[], period: number): number[] {
  if (!candles.length) return [];
  const trueRanges = candles.map((candle, index) => {
    const previousClose = candles[index - 1]?.close ?? candle.close;
    return Math.max(candle.high - candle.low, Math.abs(candle.high - previousClose), Math.abs(candle.low - previousClose));
  });
  const output = Array(candles.length).fill(0);
  for (let i = 0; i < candles.length; i += 1) {
    if (i < period) output[i] = avg(trueRanges.slice(0, i + 1));
    else output[i] = (output[i - 1] * (period - 1) + trueRanges[i]) / period;
  }
  return output;
}

function smoothRangeSeries(values: number[], period: number, multiplier: number): number[] {
  const deltas = values.map((value, index) => Math.abs(value - (values[index - 1] ?? value)));
  const averageRange = emaRawSeries(deltas, period);
  return emaRawSeries(averageRange, period * 2 - 1).map((value) => value * multiplier);
}

function rangeFilterSeries(values: number[], ranges: number[]): number[] {
  const output: number[] = [];
  values.forEach((value, index) => {
    const previous = output[index - 1] ?? value;
    output.push(value > previous + ranges[index] ? value - ranges[index] : value < previous - ranges[index] ? value + ranges[index] : previous);
  });
  return output;
}

function directionCounts(values: number[]) {
  const up: number[] = [];
  const down: number[] = [];
  values.forEach((value, index) => {
    const previous = values[index - 1] ?? value;
    up.push(value > previous ? (up[index - 1] ?? 0) + 1 : value < previous ? 0 : (up[index - 1] ?? 0));
    down.push(value < previous ? (down[index - 1] ?? 0) + 1 : value > previous ? 0 : (down[index - 1] ?? 0));
  });
  return { up, down };
}

function adviceFor(actionNow: ActionNow, isLong: boolean, isSwing: boolean) {
  if (actionNow === "BUY NOW" && isLong) return "Best long setup. Enter near buy zone only.";
  if (actionNow === "BUY NOW" && isSwing) return "Good swing setup. Use target and trailing stop.";
  if (actionNow === "BUY NOW") return "Scalp setup. Take profit fast.";
  if (actionNow === "WAIT PULLBACK") return "Setup is good, but price is high. Wait for buy zone.";
  if (actionNow === "WATCH EARLY BUY") return "Early accumulation. Start small or wait confirmation.";
  if (actionNow === "PULLBACK BUY AREA") return "Price is near buy zone. Watch buyer pressure.";
  if (actionNow === "BREAKOUT BUY") return "Breakout with volume. Risk is higher after jump.";
  if (actionNow === "SELL NOW") return "Exit signal. Sell pressure or trend weakness.";
  if (actionNow === "REDUCE / TAKE PROFIT") return "Sellers active. Reduce or protect profit.";
  if (actionNow === "HOLD") return "Hold while price stays above stop zone.";
  if (actionNow === "DO NOT BUY NOW") return "Avoid new buy. Sellers are stronger now.";
  if (actionNow === "WATCH") return "Watch only. Need stronger confirmation.";
  return "Wait for clearer setup.";
}

function demoteDelayedAction(action: ActionNow): ActionNow {
  return ["BUY NOW", "BREAKOUT BUY", "PULLBACK BUY AREA"].includes(action) ? "WATCH EARLY BUY" : action;
}

export function buildTimeframeAnalysis(symbol: string, timeframe: "15M" | "30M" | "1H" | "4H" | "1D", candles: Candle[], forceDelayedWatch = false): MarketTimeframeAnalysis | null {
  const ordered = [...candles].sort((a, b) => a.time.localeCompare(b.time));
  if (!ordered.length) return null;
  const closes = ordered.map((candle) => candle.close);
  const volumes = ordered.map((candle) => candle.volume);
  const settings = modeSettings[STRATEGY_MODE];
  const emaFastSeries = emaRawSeries(closes, strategyInputs.emaFastLen);
  const emaMidSeries = emaRawSeries(closes, strategyInputs.emaMidLen);
  const emaLongSeries = emaRawSeries(closes, strategyInputs.emaLongLen);
  const htfEmaSeries = emaRawSeries(closes, strategyInputs.htfEmaLen);
  const rsiValues = rsiSeries(closes, strategyInputs.rsiLen);
  const atrValues = atrRawSeries(ordered, strategyInputs.atrLen);
  const fastFiltSeries = rangeFilterSeries(closes, smoothRangeSeries(closes, settings.fastPer, settings.fastMult));
  const slowFiltSeries = rangeFilterSeries(closes, smoothRangeSeries(closes, strategyInputs.slowPer, strategyInputs.slowMult));
  const fastCounts = directionCounts(fastFiltSeries);
  const slowCounts = directionCounts(slowFiltSeries);

  let lastTradeBar: number | null = null;
  let inPosition = false;
  let positionEntry = 0;
  let highestSinceEntry: number | null = null;
  let latestAnalysis: MarketTimeframeAnalysis | null = null;

  for (let index = 0; index < ordered.length; index += 1) {
    const candle = ordered[index];
    const currentPrice = candle.close;
    const emaFast = emaFastSeries[index] ?? currentPrice;
    const emaMid = emaMidSeries[index] ?? currentPrice;
    const emaLong = emaLongSeries[index] ?? currentPrice;
    const htfEMA = htfEmaSeries[index] ?? emaMid;
    const htfBull = currentPrice > htfEMA;
    const htfBear = currentPrice < htfEMA;
    const currentRsi = rsiValues[index] ?? 50;
    const currentAtr = Math.max(atrValues[index] || 0, currentPrice * 0.006);
    const fastFilt = fastFiltSeries[index] ?? currentPrice;
    const slowFilt = slowFiltSeries[index] ?? currentPrice;
    const fastBull = currentPrice > fastFilt && (fastCounts.up[index] ?? 0) > 0;
    const fastBear = currentPrice < fastFilt && (fastCounts.down[index] ?? 0) > 0;
    const fastStatePrevious = index > 0 ? (closes[index - 1] > fastFiltSeries[index - 1] && (fastCounts.up[index - 1] ?? 0) > 0 ? 1 : closes[index - 1] < fastFiltSeries[index - 1] && (fastCounts.down[index - 1] ?? 0) > 0 ? -1 : 0) : 0;
    const fastBuyTurn = fastBull && fastStatePrevious === -1;
    const fastSellTurn = fastBear && fastStatePrevious === 1;
    const slowBull = currentPrice > slowFilt && (slowCounts.up[index] ?? 0) > 0;
    const slowBear = currentPrice < slowFilt && (slowCounts.down[index] ?? 0) > 0;
    const trendScalp = currentPrice > emaFast;
    const trendSwing = currentPrice > emaMid && emaFast > emaMid;
    const trendLong = currentPrice > emaLong && emaMid > emaLong;
    const volMA = avg(volumes.slice(Math.max(0, index - strategyInputs.volLen + 1), index + 1));
    const volOk = candle.volume > volMA * settings.volMult;
    const volStrong = candle.volume > volMA * (settings.volMult + 0.35);
    const volWeak = candle.volume < volMA * 0.75;
    const currentVolumeStatus: VolumeStatus = volStrong ? "Very Strong" : volOk ? "Strong" : volWeak ? "Weak" : "Normal";
    const pressureParts = pressureFromParts(candle, settings.pressureMult);
    const recentHighPrevious = Math.max(...ordered.slice(Math.max(0, index - 10), Math.max(1, index)).map((row) => row.high));
    const priceHolding = currentPrice > fastFilt || currentPrice > emaFast;
    const volBuilding = candle.volume > volMA * 0.9 && candle.volume >= (ordered[index - 1]?.volume ?? candle.volume) * 0.8;
    const rsiEarlyOk = currentRsi > strategyInputs.rsiEarly && currentRsi < strategyInputs.rsiOver;
    const rsiConfirmOk = currentRsi > strategyInputs.rsiConfirm && currentRsi < strategyInputs.rsiOver;
    const rsiWeak = currentRsi < strategyInputs.rsiExit;
    const bestBuyLow = fastFilt - currentAtr * strategyInputs.buyZoneAtr;
    const bestBuyHigh = fastFilt + currentAtr * strategyInputs.buyZoneAtr;
    const inBuyZone = currentPrice >= bestBuyLow && currentPrice <= bestBuyHigh;
    const nearBuyZone = currentPrice <= bestBuyHigh + currentAtr * 0.25 && currentPrice >= bestBuyLow - currentAtr * 0.25;
    const suggestedEntry = inBuyZone ? currentPrice : currentPrice > bestBuyHigh ? bestBuyHigh : bestBuyLow;
    const suggestedStop = suggestedEntry - currentAtr * strategyInputs.stopAtr;
    const earlyAccumulation = priceHolding && volBuilding && pressureParts.buyersOk && rsiEarlyOk && !slowBear;
    const pullbackBuy = nearBuyZone && trendScalp && pressureParts.buyersOk && rsiEarlyOk && !slowBear;
    const confirmedBuy = fastBuyTurn && fastBull && trendScalp && pressureParts.buyersOk && rsiConfirmOk && htfBull;
    const breakoutBuy = currentPrice > recentHighPrevious && volOk && pressureParts.buyersOk && trendScalp && rsiConfirmOk;

    let score = 0;
    if (fastBull) score += 1;
    if (slowBull) score += 1;
    if (trendScalp) score += 1;
    if (trendSwing) score += 1;
    if (trendLong) score += 1;
    if (volOk) score += 1;
    if (pressureParts.buyersOk) score += 1;
    if (rsiConfirmOk) score += 1;
    if (htfBull) score += 1;
    if (pressureParts.strongBuyerCandle) score += 1;

    const isScalp = score >= settings.minScore && !trendSwing;
    const isSwing = score >= settings.minScore + 1 && trendSwing;
    const isLong = score >= settings.minScore + 2 && trendLong && htfBull;
    const mainTrend: MainTrend =
      trendLong && htfBull ? "LONG BULLISH" :
      trendSwing && htfBull ? "SWING BULLISH" :
      trendScalp ? "SHORT BULLISH" :
      slowBear || htfBear ? "BEARISH" :
      "NEUTRAL";
    const tradePlan: Plan = isLong ? "BUY & HOLD" : isSwing ? "SWING TRADE" : isScalp ? "SCALP ONLY" : "WAIT";
    const targetPct = isLong ? strategyInputs.targetLongPct : isSwing ? strategyInputs.targetSwingPct : strategyInputs.targetScalpPct;
    const suggestedTarget = suggestedEntry * (1 + targetPct / 100);
    const canTrade = lastTradeBar === null || index - lastTradeBar > settings.cooldownBars;
    const entrySignal = canTrade && score >= settings.minScore && !pressureParts.sellersOk && (earlyAccumulation || pullbackBuy || confirmedBuy || breakoutBuy);

    if (entrySignal && !inPosition) {
      inPosition = true;
      positionEntry = currentPrice;
      highestSinceEntry = candle.high;
      lastTradeBar = index;
    }

    let activeStop: number | null = null;
    let activeTarget: number | null = null;
    if (inPosition) {
      highestSinceEntry = Math.max(highestSinceEntry ?? candle.high, candle.high);
      const basicStop = positionEntry - currentAtr * strategyInputs.stopAtr;
      const trailStop = highestSinceEntry - currentAtr * strategyInputs.trailAtr;
      activeStop = Math.max(basicStop, trailStop);
      activeTarget = positionEntry * (1 + targetPct / 100);
    }

    const exitScalp = inPosition && (pressureParts.sellersOk || fastSellTurn || rsiWeak || pressureParts.strongSellerCandle);
    const exitSwing = inPosition && (slowBear || currentPrice < emaMid || (pressureParts.sellersOk && pressureParts.strongSellerCandle));
    const exitLong = inPosition && (currentPrice < emaLong || emaMid < emaLong);
    const smartExit = isLong ? exitLong : isSwing ? exitSwing : exitScalp;
    const stoppedOrTargeted = inPosition && activeStop !== null && activeTarget !== null && (candle.low <= activeStop || candle.high >= activeTarget);
    const rawAction: ActionNow =
      inPosition && smartExit ? "SELL NOW" :
      inPosition && pressureParts.sellersOk ? "REDUCE / TAKE PROFIT" :
      inPosition ? "HOLD" :
      entrySignal && inBuyZone ? "BUY NOW" :
      entrySignal && !inBuyZone ? "WAIT PULLBACK" :
      earlyAccumulation && !pressureParts.sellersOk ? "WATCH EARLY BUY" :
      pullbackBuy ? "PULLBACK BUY AREA" :
      breakoutBuy ? "BREAKOUT BUY" :
      pressureParts.sellersOk ? "DO NOT BUY NOW" :
      pressureParts.buyersOk && trendScalp ? "WATCH" :
      "WAIT";
    const actionNow = forceDelayedWatch ? demoteDelayedAction(rawAction) : rawAction;
    const riskReward = (suggestedTarget - suggestedEntry) / Math.max(suggestedEntry - suggestedStop, 0.0001);

    latestAnalysis = {
      id: `${symbol}-${timeframe}`,
      symbol,
      timeframe,
      candleTimeEgypt: candle.time,
      currentPrice: round(currentPrice),
      actionNow,
      mainTrend,
      plan: tradePlan,
      score,
      pressure: pressureParts.pressure,
      volumeStatus: currentVolumeStatus,
      rsi: round(currentRsi, 1),
      atr: round(currentAtr),
      ema21: round(emaFast),
      ema50: round(emaMid),
      ema200: round(emaLong),
      fastRangeFilter: round(fastFilt),
      slowRangeFilter: round(slowFilt),
      buyZoneLow: round(bestBuyLow),
      buyZoneHigh: round(bestBuyHigh),
      suggestedEntry: round(suggestedEntry),
      suggestedTarget: round(suggestedTarget),
      suggestedStop: round(suggestedStop),
      riskReward: round(riskReward, 2),
      breakoutStatus: breakoutBuy,
      pullbackStatus: pullbackBuy,
      earlyAccumulationStatus: earlyAccumulation,
      advice: adviceFor(actionNow, isLong, isSwing),
      signalMode: STRATEGY_MODE,
      activeStop: activeStop === null ? null : round(activeStop),
      activeTarget: activeTarget === null ? null : round(activeTarget),
      positionState: inPosition ? "IN TRADE" : "NO TRADE",
      lastUpdateEgypt: candle.time,
    };

    if (smartExit || stoppedOrTargeted) {
      inPosition = false;
      highestSinceEntry = null;
      if (smartExit) lastTradeBar = index;
    }
  }

  return latestAnalysis;
}
