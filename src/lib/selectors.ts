import { bestStocks, stocks, timeframeAnalyses, timeframes } from "../data/mockData";
import type { ActionNow, BestStock, Timeframe, TimeframeAnalysis } from "../types";

export function getCompany(symbol: string) {
  return stocks.find((stock) => stock.symbol === symbol);
}

export function analysesFor(symbol: string): TimeframeAnalysis[] {
  return timeframes.map((tf) => timeframeAnalyses.find((row) => row.symbol === symbol && row.timeframe === tf)!);
}

export function bestFor(symbol: string): BestStock {
  return bestStocks.find((row) => row.symbol === symbol)!;
}

export function timeframeMap(symbol: string): Record<Timeframe, TimeframeAnalysis> {
  return Object.fromEntries(analysesFor(symbol).map((row) => [row.timeframe, row])) as Record<Timeframe, TimeframeAnalysis>;
}

export function actionTone(action: ActionNow): string {
  if (["BUY NOW", "BREAKOUT BUY", "PULLBACK BUY AREA"].includes(action)) return "buy";
  if (["WATCH", "WATCH EARLY BUY", "WAIT PULLBACK", "REDUCE / TAKE PROFIT"].includes(action)) return "watch";
  if (["SELL NOW", "DO NOT BUY NOW"].includes(action)) return "sell";
  if (action === "HOLD") return "neutral";
  return "wait";
}
