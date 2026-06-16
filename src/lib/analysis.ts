import type { ActionNow, ImportedCandle, MainTrend, Plan, Pressure, TimeframeAnalysis, VolumeStatus } from "../types";

const round = (value: number, digits = 2) => Number(value.toFixed(digits));
const average = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / Math.max(values.length, 1);

export function sma(values: number[], period: number): number {
  if (!values.length) return 0;
  return average(values.slice(-period));
}

export function ema(values: number[], period: number): number {
  if (!values.length) return 0;
  const k = 2 / (period + 1);
  return values.reduce((prev, price, index) => (index === 0 ? price : price * k + prev * (1 - k)), values[0]);
}

export function emaSeries(values: number[], period: number): number[] {
  if (!values.length) return [];
  const k = 2 / (period + 1);
  const output: number[] = [];
  values.forEach((price, index) => {
    output.push(index === 0 ? price : price * k + output[index - 1] * (1 - k));
  });
  return output;
}

export function rsi(closes: number[], period = 14): number {
  if (closes.length <= period) return 50;
  const slice = closes.slice(-period - 1);
  let gains = 0;
  let losses = 0;
  for (let i = 1; i < slice.length; i += 1) {
    const change = slice[i] - slice[i - 1];
    if (change >= 0) gains += change;
    else losses += Math.abs(change);
  }
  if (losses === 0) return 100;
  const rs = gains / period / (losses / period);
  return 100 - 100 / (1 + rs);
}

export function atr(candles: ImportedCandle[], period = 14): number {
  const slice = candles.slice(-period - 1);
  const ranges = slice.slice(1).map((candle, index) => {
    const previousClose = slice[index].close;
    return Math.max(candle.high - candle.low, Math.abs(candle.high - previousClose), Math.abs(candle.low - previousClose));
  });
  return average(ranges);
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

function pressureFromParts(candle: Pick<ImportedCandle, "open" | "high" | "low" | "close" | "volume">, pressureMult = 1.12) {
  const candleRange = Math.max(candle.high - candle.low, 0.0001);
  const bodySize = Math.abs(candle.close - candle.open);
  const bodyPct = bodySize / candleRange;
  const closePosition = (candle.close - candle.low) / candleRange;
  const buyPressure = closePosition * candle.volume;
  const sellPressure = (1 - closePosition) * candle.volume;
  const buyersOk = buyPressure >= sellPressure * pressureMult;
  const sellersOk = sellPressure >= buyPressure * pressureMult;
  return {
    candleRange,
    bodyPct,
    closePosition,
    buyPressure,
    sellPressure,
    buyersOk,
    sellersOk,
    pressure: buyersOk ? "Buy Pressure" as const : sellersOk ? "Sell Pressure" as const : "Neutral" as const,
    strongBuyerCandle: candle.close > candle.open && closePosition > 0.6 && bodyPct > 0.35,
    strongSellerCandle: candle.close < candle.open && closePosition < 0.4 && bodyPct > 0.35,
  };
}

function pressure(candle: ImportedCandle): Pressure {
  return pressureFromParts(candle).pressure;
}

export function candlePressure(candle: ImportedCandle): Pressure {
  return pressure(candle);
}

export function candleVolumeStatus(volume: number, volumeMA: number): VolumeStatus {
  return volumeStatus(volume, volumeMA);
}

export interface TechnicalSummary {
  sma20: number;
  sma50: number;
  sma200: number;
  ema9: number;
  ema21: number;
  rsi14: number;
  atr14: number;
  macd: number;
  macdSignal: number;
  macdHistogram: number;
  bollingerUpper: number;
  bollingerMiddle: number;
  bollingerLower: number;
  volumeAverage20: number;
  support: number;
  resistance: number;
  volumeDirection: "Accumulation" | "Distribution" | "Neutral";
  volumeAlert: string;
}

export function buildTechnicalSummary(candles: ImportedCandle[]): TechnicalSummary | null {
  const ordered = [...candles].sort((a, b) => a.candleTime.localeCompare(b.candleTime));
  if (ordered.length < 20) return null;
  const latest = ordered[ordered.length - 1];
  const closes = ordered.map((candle) => candle.close);
  const highs = ordered.map((candle) => candle.high);
  const lows = ordered.map((candle) => candle.low);
  const volumes = ordered.map((candle) => candle.volume);
  const ema12 = emaSeries(closes, 12);
  const ema26 = emaSeries(closes, 26);
  const macdSeries = closes.map((_, index) => (ema12[index] ?? closes[index]) - (ema26[index] ?? closes[index]));
  const signalSeries = emaSeries(macdSeries, 9);
  const middle = sma(closes, 20);
  const bbSlice = closes.slice(-20);
  const deviation = Math.sqrt(average(bbSlice.map((close) => Math.pow(close - middle, 2))));
  const volumeAverage20 = average(volumes.slice(-20));
  const latestPressure = pressure(latest);
  const latestVolumeStatus = volumeStatus(latest.volume, volumeAverage20);
  const volumeDirection = latestPressure === "Buy Pressure" ? "Accumulation" : latestPressure === "Sell Pressure" ? "Distribution" : "Neutral";
  const volumeAlert =
    latestVolumeStatus === "Very Strong" || latestVolumeStatus === "Strong"
      ? `${volumeDirection} volume: ${latestVolumeStatus.toLowerCase()} activity versus the 20-day average.`
      : `${volumeDirection} volume: ${latestVolumeStatus.toLowerCase()} activity versus the 20-day average.`;

  return {
    sma20: round(sma(closes, 20)),
    sma50: round(sma(closes, 50)),
    sma200: round(sma(closes, 200)),
    ema9: round(ema(closes, 9)),
    ema21: round(ema(closes, 21)),
    rsi14: round(rsi(closes), 1),
    atr14: round(Math.max(atr(ordered), latest.close * 0.006)),
    macd: round(macdSeries[macdSeries.length - 1]),
    macdSignal: round(signalSeries[signalSeries.length - 1]),
    macdHistogram: round(macdSeries[macdSeries.length - 1] - signalSeries[signalSeries.length - 1]),
    bollingerUpper: round(middle + deviation * 2),
    bollingerMiddle: round(middle),
    bollingerLower: round(middle - deviation * 2),
    volumeAverage20: round(volumeAverage20, 0),
    support: round(Math.min(...lows.slice(-30))),
    resistance: round(Math.max(...highs.slice(-30))),
    volumeDirection,
    volumeAlert,
  };
}

function volumeStatus(volume: number, volumeMA: number, volMult = 1.15): VolumeStatus {
  if (volume > volumeMA * (volMult + 0.35)) return "Very Strong";
  if (volume > volumeMA * volMult) return "Strong";
  if (volume < volumeMA * 0.75) return "Weak";
  return "Normal";
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

function atrSeries(candles: ImportedCandle[], period: number): number[] {
  if (!candles.length) return [];
  const trueRanges = candles.map((candle, index) => {
    const previousClose = candles[index - 1]?.close ?? candle.close;
    return Math.max(candle.high - candle.low, Math.abs(candle.high - previousClose), Math.abs(candle.low - previousClose));
  });
  const output = Array(candles.length).fill(0);
  for (let i = 0; i < candles.length; i += 1) {
    if (i < period) output[i] = average(trueRanges.slice(0, i + 1));
    else output[i] = (output[i - 1] * (period - 1) + trueRanges[i]) / period;
  }
  return output;
}

function smoothRangeSeries(values: number[], period: number, multiplier: number): number[] {
  const deltas = values.map((value, index) => Math.abs(value - (values[index - 1] ?? value)));
  const averageRange = emaSeries(deltas, period);
  return emaSeries(averageRange, period * 2 - 1).map((value) => value * multiplier);
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

export function analyzeCandles(symbol: string, timeframe: TimeframeAnalysis["timeframe"], candles: ImportedCandle[], forceDelayedWatch = false): TimeframeAnalysis {
  const ordered = [...candles].sort((a, b) => a.candleTime.localeCompare(b.candleTime));
  const closes = ordered.map((candle) => candle.close);
  const volumes = ordered.map((candle) => candle.volume);
  const settings = modeSettings[STRATEGY_MODE];
  const emaFastSeries = emaSeries(closes, strategyInputs.emaFastLen);
  const emaMidSeries = emaSeries(closes, strategyInputs.emaMidLen);
  const emaLongSeries = emaSeries(closes, strategyInputs.emaLongLen);
  const htfEmaSeries = emaSeries(closes, strategyInputs.htfEmaLen);
  const rsiValues = rsiSeries(closes, strategyInputs.rsiLen);
  const atrValues = atrSeries(ordered, strategyInputs.atrLen);
  const fastFiltSeries = rangeFilterSeries(closes, smoothRangeSeries(closes, settings.fastPer, settings.fastMult));
  const slowFiltSeries = rangeFilterSeries(closes, smoothRangeSeries(closes, strategyInputs.slowPer, strategyInputs.slowMult));
  const fastCounts = directionCounts(fastFiltSeries);
  const slowCounts = directionCounts(slowFiltSeries);

  let lastTradeBar: number | null = null;
  let inPosition = false;
  let positionEntry = 0;
  let highestSinceEntry: number | null = null;
  let latestAnalysis: TimeframeAnalysis | null = null;

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
    const volMA = average(volumes.slice(Math.max(0, index - strategyInputs.volLen + 1), index + 1));
    const volOk = candle.volume > volMA * settings.volMult;
    const volStrong = candle.volume > volMA * (settings.volMult + 0.35);
    const volWeak = candle.volume < volMA * 0.75;
    const currentVolumeStatus: VolumeStatus = volStrong ? "Very Strong" : volOk ? "Strong" : volWeak ? "Weak" : "Normal";
    const pressureParts = pressureFromParts(candle, settings.pressureMult);
    const recentLowPrevious = Math.min(...ordered.slice(Math.max(0, index - 10), Math.max(1, index)).map((row) => row.low));
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
      candleTimeEgypt: candle.candleTime,
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
      lastUpdateEgypt: candle.candleTime,
    };

    if (smartExit || stoppedOrTargeted) {
      inPosition = false;
      highestSinceEntry = null;
      if (smartExit) lastTradeBar = index;
    }
  }

  return latestAnalysis!;
}
