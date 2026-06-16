import express from "express";
import cors from "cors";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import { pinoHttp } from "pino-http";
import { WebSocketServer } from "ws";
import { config } from "./config.js";
import { createProvider } from "./providers/index.js";
import { MarketDataService } from "./services/marketDataService.js";
import { startMarketAutoRefresh } from "./services/autoRefreshService.js";
import { createRoutes } from "./routes.js";
import { createEgxAiCompatibleRoutes } from "./egxAiCompatibleRoutes.js";

const app = express();
const marketData = new MarketDataService(createProvider());
const autoRefresh = startMarketAutoRefresh(marketData);

function isLoopbackRequest(req: express.Request) {
  const addresses = [req.ip, req.socket.remoteAddress, req.headers["x-forwarded-for"]]
    .flat()
    .filter(Boolean)
    .map((value) => String(value).split(",")[0].trim());

  return addresses.some(
    (address) =>
      address === "127.0.0.1" ||
      address === "::1" ||
      address === "::ffff:127.0.0.1" ||
      address === "localhost",
  );
}

app.disable("etag");
app.use(helmet());
app.use(cors());
app.use(express.json({ limit: "1mb" }));
app.use(express.text({ type: ["text/csv", "text/plain"], limit: "5mb" }));
app.use("/api", (_req, res, next) => {
  res.setHeader("Cache-Control", "no-store");
  next();
});
app.use(pinoHttp());
app.use(rateLimit({ windowMs: config.RATE_LIMIT_WINDOW_MS, limit: config.RATE_LIMIT_MAX, skip: isLoopbackRequest }));
app.use("/api/v1", createEgxAiCompatibleRoutes());
app.get("/api/auto-refresh/status", (_req, res) => {
  res.json(autoRefresh?.status() ?? { enabled: false, intervalMs: config.AUTO_REFRESH_INTERVAL_MS });
});
app.use("/api", createRoutes(marketData));

app.get("/api/live", (_req, res) => {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  const timer = setInterval(async () => {
    const status = await marketData.getScanner();
    res.write(`event: scanner\n`);
    res.write(`data: ${JSON.stringify(status)}\n\n`);
  }, 15000);
  res.on("close", () => clearInterval(timer));
});

const server = app.listen(config.PORT, () => {
  console.log(`EGX market backend listening on http://localhost:${config.PORT}`);
});

const wss = new WebSocketServer({ server, path: "/ws/market" });
wss.on("connection", (socket) => {
  socket.send(JSON.stringify({ type: "connected", message: "EGX market data WebSocket connected" }));
  const timer = setInterval(async () => {
    socket.send(JSON.stringify({ type: "scanner", payload: await marketData.getScanner() }));
  }, 15000);
  socket.on("close", () => clearInterval(timer));
});
