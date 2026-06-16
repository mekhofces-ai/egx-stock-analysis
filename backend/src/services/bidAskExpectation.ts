import type { MarketTimeframeAnalysis, Quote } from "../types.js";

function round(value: number) {
  return Number(value.toFixed(2));
}

export function spreadPercent(bid?: number, ask?: number) {
  if (!bid || !ask || ask <= 0 || bid <= 0 || ask < bid) return undefined;
  const mid = (bid + ask) / 2;
  return round(((ask - bid) / mid) * 100);
}

export function buildBidAskExpectation(quote: Pick<Quote, "bid" | "ask" | "price" | "orderBookStatus">, analysis?: MarketTimeframeAnalysis | null) {
  const spread = spreadPercent(quote.bid, quote.ask);
  if (quote.orderBookStatus !== "real" || !quote.bid || !quote.ask || spread === undefined) {
    if (!analysis) return "Real bid/ask is unavailable from the active free provider.";
    return `Real bid/ask is unavailable. Expectation falls back to ${analysis.mainTrend.toLowerCase()}, ${analysis.pressure.toLowerCase()}, ${analysis.volumeStatus.toLowerCase()} volume, and score ${analysis.score}/10.`;
  }

  const price = quote.price;
  const mid = (quote.bid + quote.ask) / 2;
  const nearAsk = price >= quote.ask * 0.995;
  const nearBid = price <= quote.bid * 1.005;
  const tightSpread = spread <= 0.75;
  const wideSpread = spread >= 1.75;
  const trend = analysis?.mainTrend ?? "NEUTRAL";
  const pressure = analysis?.pressure ?? "Neutral";

  if (nearAsk && pressure === "Buy Pressure" && tightSpread) {
    return `Bullish order-flow expectation: last price is lifting near ask ${quote.ask}, spread is tight at ${spread}%, and technical pressure is buy-side.`;
  }
  if (nearBid && pressure === "Sell Pressure") {
    return `Bearish order-flow expectation: last price is trading near bid ${quote.bid}, sellers are pressing, and spread is ${spread}%.`;
  }
  if (wideSpread) {
    return `Caution: real bid/ask spread is wide at ${spread}%, so entries need limit-order discipline even if the technical trend is ${trend.toLowerCase()}.`;
  }
  if (price >= mid && trend.includes("BULLISH")) {
    return `Constructive expectation: price is above the bid/ask midpoint ${round(mid)} with ${trend.toLowerCase()} structure and ${spread}% spread.`;
  }
  if (price < mid && pressure === "Sell Pressure") {
    return `Weak expectation: price is below the bid/ask midpoint ${round(mid)} while sell pressure is present.`;
  }
  return `Neutral bid/ask expectation: spread is ${spread}% and price is close to the midpoint ${round(mid)}.`;
}
