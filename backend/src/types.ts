export type Timeframe = "1m" | "5m" | "15m" | "15M" | "30M" | "1h" | "1H" | "4H" | "1D";
export type ProviderStatus = "available" | "degraded" | "unavailable";
export type ActionNow =
  | "BUY NOW"
  | "BREAKOUT BUY"
  | "PULLBACK BUY AREA"
  | "WATCH EARLY BUY"
  | "WAIT PULLBACK"
  | "HOLD"
  | "REDUCE / TAKE PROFIT"
  | "SELL NOW"
  | "DO NOT BUY NOW"
  | "WATCH"
  | "WAIT";
export type Pressure = "Buy Pressure" | "Sell Pressure" | "Neutral";
export type VolumeStatus = "Very Strong" | "Strong" | "Normal" | "Weak";
export type MainTrend = "SHORT BULLISH" | "SWING BULLISH" | "LONG BULLISH" | "BEARISH" | "NEUTRAL";
export type Plan = "BUY & HOLD" | "SWING TRADE" | "SCALP ONLY" | "WAIT";

export interface ProviderResult<T> {
  status: ProviderStatus;
  data?: T;
  reason?: string;
  source: string;
  meta?: Record<string, unknown>;
}

export interface Quote {
  symbol: string;
  price: number;
  previousClose?: number;
  changePercent?: number;
  volume?: number;
  marketCap?: number;
  sector?: string | null;
  industry?: string | null;
  bid?: number;
  ask?: number;
  spreadPercent?: number;
  orderBookStatus: "real" | "estimated" | "unavailable";
  orderBookNote?: string;
  bidAskExpectation?: string;
  capturedAt: string;
}

export interface Candle {
  symbol: string;
  timeframe: Timeframe;
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  source: string;
}

export interface ScannerRow {
  symbol: string;
  companyName: string;
  sector?: string | null;
  price?: number;
  changePercent?: number;
  volume?: number;
  capturedAt?: string;
  marketCap?: number;
  bid?: number;
  ask?: number;
  spreadPercent?: number;
  orderBookStatus?: "real" | "estimated" | "unavailable";
  orderBookNote?: string;
  bidAskExpectation?: string;
  recommendation?: Recommendation;
  confidence?: number;
  dataQuality: "real" | "partial" | "unavailable";
  reason?: string;
  analysis?: MarketTimeframeAnalysis;
}

export type Recommendation = "BUY" | "WATCH" | "SELL" | "AVOID";

export interface MarketTimeframeAnalysis {
  id: string;
  symbol: string;
  timeframe: "15M" | "30M" | "1H" | "4H" | "1D";
  candleTimeEgypt: string;
  currentPrice: number;
  actionNow: ActionNow;
  mainTrend: MainTrend;
  plan: Plan;
  score: number;
  pressure: Pressure;
  volumeStatus: VolumeStatus;
  rsi: number;
  atr: number;
  ema21: number;
  ema50: number;
  ema200: number;
  fastRangeFilter: number;
  slowRangeFilter: number;
  buyZoneLow: number;
  buyZoneHigh: number;
  suggestedEntry: number;
  suggestedTarget: number;
  suggestedStop: number;
  riskReward: number;
  breakoutStatus: boolean;
  pullbackStatus: boolean;
  earlyAccumulationStatus: boolean;
  advice?: string;
  signalMode?: "Aggressive" | "Balanced" | "Safe";
  activeStop?: number | null;
  activeTarget?: number | null;
  positionState?: "IN TRADE" | "NO TRADE";
  lastUpdateEgypt: string;
}
