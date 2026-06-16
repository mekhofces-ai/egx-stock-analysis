import type { Candle } from "../types.js";
import { calculateIndicators, recommendation } from "./technicalAnalysis.js";

export class AIAnalysisService {
  analyze(symbol: string, candles: Candle[]) {
    if (!candles.length) {
      return {
        summary: `${symbol} has no real candle data available.`,
        trend: "Unavailable",
        entryZone: null,
        stopLoss: null,
        takeProfit1: null,
        takeProfit2: null,
        riskLevel: "Unknown",
        explanation: "No licensed real-time or imported candle feed is available for this symbol.",
        disclaimer: "Not financial advice.",
      };
    }
    const latest = candles[candles.length - 1];
    const indicators = calculateIndicators(candles);
    const rec = recommendation(candles);
    const atr = indicators.atr14 ?? latest.close * 0.02;
    return {
      summary: `${symbol} is classified as ${rec.recommendation} with ${rec.confidence}% confidence.`,
      trend: latest.close > (indicators.ema21 ?? latest.close) ? "Bullish / improving" : "Weak / below short-term average",
      entryZone: { low: Number((latest.close - atr * 0.4).toFixed(2)), high: Number((latest.close + atr * 0.2).toFixed(2)) },
      stopLoss: Number((latest.close - atr * 1.6).toFixed(2)),
      takeProfit1: Number((latest.close + atr * 2).toFixed(2)),
      takeProfit2: Number((latest.close + atr * 3.2).toFixed(2)),
      riskLevel: rec.confidence >= 70 ? "Medium" : "High",
      explanation: rec.reason,
      disclaimer: "Not financial advice.",
    };
  }
}
