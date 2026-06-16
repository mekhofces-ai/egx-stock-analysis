export type Timeframe = "1M" | "5M" | "15M" | "30M" | "1H" | "4H" | "1D";
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

export interface Stock {
  id: string;
  symbol: string;
  companyName: string;
  sector: string;
  market: string;
  isActive: boolean;
  notes: string;
}

export interface ImportedCandle {
  id: string;
  symbol: string;
  timeframe: Timeframe;
  candleTime: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  source: "sample" | "csv" | "api" | "webhook";
  importedAt: string;
}

export interface VolumeDirectionAlert {
  id: string;
  symbol: string;
  companyName: string;
  timeframe: Timeframe;
  direction: "Accumulation" | "Distribution" | "Neutral";
  severity: "High" | "Medium" | "Low";
  pressure: Pressure;
  volumeStatus: VolumeStatus;
  score: number;
  message: string;
}

export interface SmartEarlyAlert {
  id: string;
  symbol: string;
  companyName: string;
  timeframe: Timeframe;
  alertType: "Accumulation Watch" | "Breakout Watch" | "Pullback Near Buy Zone" | "Distribution Risk" | "Volume Spike";
  side: "Bullish" | "Bearish" | "Neutral";
  severity: "High" | "Medium" | "Low";
  urgencyScore: number;
  action: ActionNow;
  price: number;
  entryZone: string;
  trigger: string;
  invalidation: string;
  reason: string;
  dataFreshness: string;
  createdAtEgypt: string;
}

export interface TimeframeAnalysis {
  id: string;
  symbol: string;
  timeframe: Timeframe;
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

export interface BestStock {
  id: string;
  rank: number;
  symbol: string;
  companyName: string;
  bestAction: ActionNow;
  bestFrame: Timeframe;
  overallScore: number;
  plan: Plan;
  entry: number;
  target: number;
  stop: number;
  riskReward: number;
  pressure: Pressure;
  volumeStatus: VolumeStatus;
  reason: string;
  lastUpdateEgypt: string;
  currentPrice?: number;
  changePercent?: number;
  volume?: number;
  providerUpdatedAt?: string;
  sector?: string;
  bid?: number;
  ask?: number;
  spreadPercent?: number;
  orderBookStatus?: "real" | "estimated" | "unavailable";
  orderBookNote?: string;
  bidAskExpectation?: string;
  dataQuality?: "real" | "partial" | "unavailable";
}

export interface WatchlistItem {
  id: string;
  symbol: string;
  companyName: string;
  userNotes: string;
  alertEnabled: boolean;
}

export interface Signal {
  id: string;
  symbol: string;
  timeframe: Timeframe;
  signalType: string;
  action: ActionNow;
  price: number;
  score: number;
  message: string;
  createdAtEgypt: string;
}

export interface Settings {
  id: string;
  dataSourceType: "Public delayed API" | "Twelve Data API key" | "EGX-AI API" | "Licensed real-time API" | "External API" | "TradingView Webhook";
  apiEndpoint: string;
  defaultMode: "Aggressive" | "Balanced" | "Safe";
  defaultRisk: string;
  egyptTimezone: string;
  minScore: number;
}
