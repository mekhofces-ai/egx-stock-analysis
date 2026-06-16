import { config } from "../config.js";
import type { MarketDataProvider } from "./MarketDataProvider.js";
import { EgxAiApiProvider } from "./EgxAiApiProvider.js";
import { LicensedApiProvider } from "./LicensedApiProvider.js";
import { ManualCsvProvider } from "./ManualCsvProvider.js";
import { MockProvider } from "./MockProvider.js";
import { PublicYahooChartProvider } from "./PublicYahooChartProvider.js";
import { RefinitivWebSocketProvider } from "./RefinitivWebSocketProvider.js";
import { TradingViewWidgetProvider } from "./TradingViewWidgetProvider.js";
import { TwelveDataProvider } from "./TwelveDataProvider.js";

export function createProvider(): MarketDataProvider {
  if (config.MARKET_DATA_PROVIDER === "licensed-api") return new LicensedApiProvider();
  if (config.MARKET_DATA_PROVIDER === "twelve-data") return new TwelveDataProvider();
  if (config.MARKET_DATA_PROVIDER === "egx-ai-api") return new EgxAiApiProvider();
  if (config.MARKET_DATA_PROVIDER === "refinitiv-websocket") return new RefinitivWebSocketProvider();
  if (config.MARKET_DATA_PROVIDER === "public-yahoo-chart") return new PublicYahooChartProvider();
  if (config.MARKET_DATA_PROVIDER === "tradingview-widget") return new TradingViewWidgetProvider();
  if (config.MARKET_DATA_PROVIDER === "mock") return new MockProvider();
  if (config.MARKET_DATA_PROVIDER === "manual-csv") return new ManualCsvProvider();
  return new PublicYahooChartProvider();
}
