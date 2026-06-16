import "dotenv/config";
import { z } from "zod";

const envBoolean = z.preprocess((value) => {
  if (typeof value === "string") return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
  return value;
}, z.boolean());

const envSchema = z.object({
  NODE_ENV: z.string().default("development"),
  PORT: z.coerce.number().default(8788),
  DATABASE_URL: z.string().default("file:./dev.db"),
  MARKET_DATA_PROVIDER: z.enum(["licensed-api", "manual-csv", "public-yahoo-chart", "twelve-data", "egx-ai-api", "refinitiv-websocket", "tradingview-widget", "mock"]).default("public-yahoo-chart"),
  LICENSED_API_BASE_URL: z.string().default(""),
  LICENSED_API_KEY: z.string().default(""),
  LICENSED_API_TIMEOUT_MS: z.coerce.number().default(8000),
  EGX_AI_API_BASE_URL: z.string().default("http://localhost:8100"),
  EGX_AI_API_TIMEOUT_MS: z.coerce.number().default(8000),
  TWELVE_DATA_API_KEY: z.string().default(""),
  TWELVE_DATA_EXCHANGE: z.string().default("EGX"),
  TWELVE_DATA_SYMBOL_MAP_JSON: z.string().default("{}"),
  REFINITIV_WS_URL: z.string().default(""),
  REFINITIV_AUTH_MODE: z.enum(["ads", "rto-password", "rto-client-credentials"]).default("ads"),
  REFINITIV_WS_USERNAME: z.string().default(""),
  REFINITIV_WS_PASSWORD: z.string().default(""),
  REFINITIV_WS_CLIENT_ID: z.string().default(""),
  REFINITIV_WS_CLIENT_SECRET: z.string().default(""),
  REFINITIV_WS_APP_ID: z.string().default("256"),
  REFINITIV_WS_POSITION: z.string().default("127.0.0.1/net"),
  REFINITIV_WS_SERVICE: z.string().default(""),
  REFINITIV_RIC_SUFFIX: z.string().default(".CA"),
  REFINITIV_WS_VIEW_FIELDS: z.string().default(""),
  REFINITIV_WS_TIMEOUT_MS: z.coerce.number().default(10000),
  REFINITIV_WS_MAX_BATCH_SIZE: z.coerce.number().default(80),
  REFINITIV_AUTH_URL_V1: z.string().default("https://api.refinitiv.com:443/auth/oauth2/v1/token"),
  REFINITIV_AUTH_URL_V2: z.string().default("https://api.refinitiv.com/auth/oauth2/v2/token"),
  REFINITIV_DISCOVERY_URL: z.string().default("https://api.refinitiv.com/streaming/pricing/v1/"),
  REFINITIV_REGION: z.string().default("us-east-1"),
  REFINITIV_SCOPE: z.string().default("trapi.streaming.pricing.read"),
  CACHE_TTL_MS: z.coerce.number().default(15000),
  RATE_LIMIT_WINDOW_MS: z.coerce.number().default(60000),
  RATE_LIMIT_MAX: z.coerce.number().default(300),
  AUTO_REFRESH_ENABLED: envBoolean.default(true),
  AUTO_REFRESH_ON_START: envBoolean.default(true),
  AUTO_REFRESH_INTERVAL_MS: z.coerce.number().default(300000),
  ENABLE_MOCK_PROVIDER: envBoolean.default(false),
  ALLOW_INSECURE_MARKET_DATA_TLS: envBoolean.default(false),
});

export const config = envSchema.parse(process.env);

if (config.ALLOW_INSECURE_MARKET_DATA_TLS) {
  process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
}
