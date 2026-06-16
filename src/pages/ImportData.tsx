import { useEffect, useState } from "react";
import { Download, ExternalLink, KeyRound, PlayCircle, RefreshCw, Server, ShieldAlert, Upload } from "lucide-react";
import type { MarketDataSnapshot } from "../data/mockData";
import { backendBaseUrl, fetchTradingViewWebhookHealth, parseCandleCsv, type TradingViewWebhookHealth, webhookBaseUrl } from "../lib/dataAdapters";

export default function ImportData({ data }: { data: MarketDataSnapshot }) {
  const [csvCandles, setCsvCandles] = useState(0);
  const [warnings, setWarnings] = useState<string[]>(["No sample prices are active. Import CSV/API/webhook candles to create analysis."]);
  const [lastImport, setLastImport] = useState("No real import yet");
  const [webhookHealth, setWebhookHealth] = useState<TradingViewWebhookHealth | null>(null);
  const [webhookStatus, setWebhookStatus] = useState("Checking local webhook server...");
  const [actionStatus, setActionStatus] = useState("");
  const webhookUrl = `${webhookBaseUrl()}/api/tradingview/webhook`;
  const backendImportUrl = `${backendBaseUrl()}/api/import/candles`;
  const missingRows = data.screenerRows.filter((row) => row.dataQuality === "unavailable");
  const payloadTemplate = `{
  "symbol": "{{ticker}}",
  "exchange": "{{exchange}}",
  "timeframe": "{{interval}}",
  "time": "{{time}}",
  "open": "{{open}}",
  "high": "{{high}}",
  "low": "{{low}}",
  "close": "{{close}}",
  "volume": "{{volume}}",
  "signalType": "TradingView Alert",
  "action": "ALERT",
  "message": "{{ticker}} {{interval}} close={{close}} volume={{volume}}"
}`;
  const authorizedWsTemplate = `AUTHORIZED_MARKET_WS_URL="wss://your-authorized-provider.example/feed"
AUTHORIZED_MARKET_WS_SUBSCRIPTIONS='[{"symbol":"EGX:COMI","timeframe":"1D"}]'`;

  const exportMissingCoverage = () => {
    const header = "symbol,company,reason\n";
    const rows = missingRows.map((row) => [
      row.symbol,
      `"${row.companyName.replace(/"/g, "\"\"")}"`,
      `"${(row.reason ?? "").replace(/"/g, "\"\"")}"`,
    ].join(","));
    const blob = new Blob([[header, ...rows].join("\n")], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "egx-missing-provider-coverage.csv";
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const refreshWebhookStatus = async () => {
    try {
      const health = await fetchTradingViewWebhookHealth();
      setWebhookHealth(health);
      setWebhookStatus(`Online on port ${health.port}`);
    } catch {
      setWebhookHealth(null);
      setWebhookStatus("Offline. Start it with npm run webhook.");
    }
  };

  useEffect(() => {
    void refreshWebhookStatus();
  }, []);

  const onCsvFile = async (file?: File) => {
    if (!file) return;
    const text = await file.text();
    const result = parseCandleCsv(text);
    setCsvCandles(result.candles.length);
    if (result.warnings.length) {
      setWarnings(result.warnings);
      return;
    }
    try {
      const response = await fetch(backendImportUrl, { method: "POST", headers: { "Content-Type": "text/csv" }, body: text });
      const payload = await response.json() as { imported?: number; errors?: string[]; reason?: string };
      if (!response.ok && response.status !== 207) throw new Error(payload.reason ?? `Backend import failed with ${response.status}`);
      setWarnings(payload.errors?.length ? payload.errors : [`Imported ${payload.imported ?? result.candles.length} real candle rows into the backend database.`]);
      setActionStatus("CSV imported. The dashboard refreshes from backend data automatically.");
      setLastImport(new Date().toLocaleString("en-GB", { timeZone: "Africa/Cairo" }));
    } catch (error) {
      setWarnings([error instanceof Error ? error.message : "Backend import failed. Make sure npm run backend:dev is running."]);
    }
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
      <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
        <h2 className="text-lg font-bold text-white">Import Data</h2>
        <p className="mt-1 text-sm text-slate-400">Supported sources: CSV import, licensed external API endpoint, and TradingView webhook/alert payloads where available. No fake live prices are used.</p>
        <div className="mt-4 rounded border border-terminal-border bg-[#0C131D] p-4">
          <div className="text-xs font-semibold uppercase text-slate-500">CSV Format</div>
          <pre className="mt-2 overflow-x-auto rounded bg-[#080D13] p-3 text-xs text-slate-300">Symbol,Timeframe,Time,Open,High,Low,Close,Volume{"\n"}COMI,15M,2026-05-18 10:00,82.5,83.1,82.2,82.9,150000{"\n"}EGTS,1D,2026-05-18,18.1,18.7,17.9,18.5,900000</pre>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="rounded border border-dashed border-terminal-border bg-terminal-card p-4 text-center text-sm text-slate-400">
              <Upload className="mx-auto mb-2 text-teal-300" size={24} />
              Select CSV file
              <input type="file" accept=".csv" className="hidden" onChange={(event) => void onCsvFile(event.target.files?.[0])} />
            </label>
            <div className="rounded border border-terminal-border bg-terminal-card p-4">
              <label className="text-xs uppercase text-slate-500">External API Endpoint</label>
              <input placeholder="https://provider.example/egx/candles" className="mt-2 h-10 w-full rounded border border-terminal-border bg-[#0B111A] px-3 text-sm text-slate-100 outline-none focus:border-teal-400" />
            </div>
          </div>
          <div className="mt-4 grid gap-2 sm:grid-cols-3">
            <button type="button" onClick={() => setActionStatus("Analysis is processed by the backend as candles are imported or fetched. Open Dashboard/Screener to see the latest rows.")} className="inline-flex items-center justify-center gap-2 rounded bg-teal-500 px-4 py-2 text-sm font-semibold text-[#04110F]"><PlayCircle size={16} /> Process Analysis</button>
            <button type="button" onClick={() => { window.location.href = `/dashboard?last=${Date.now()}`; }} className="inline-flex items-center justify-center gap-2 rounded border border-terminal-border bg-[#0B111A] px-4 py-2 text-sm text-slate-200"><RefreshCw size={16} /> Refresh Dashboard</button>
            <button onClick={() => void refreshWebhookStatus()} className="inline-flex items-center justify-center gap-2 rounded border border-terminal-border bg-[#0B111A] px-4 py-2 text-sm text-slate-200"><Server size={16} /> Test Endpoint</button>
          </div>
          {actionStatus && <div className="mt-3 rounded border border-emerald-400/30 bg-emerald-500/10 p-3 text-sm text-emerald-300">{actionStatus}</div>}
        </div>
        <div className="mt-4 rounded border border-teal-400/25 bg-teal-500/8 p-4">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase text-teal-300">TradingView Webhook Receiver</div>
              <div className="mt-1 break-all text-sm text-slate-200">{webhookUrl}</div>
              <div className="mt-1 break-all text-xs text-slate-500">Backend CSV import: {backendImportUrl}</div>
            </div>
            <div className={`rounded border px-3 py-2 text-xs font-semibold ${webhookHealth?.ok ? "border-emerald-400/40 bg-emerald-500/10 text-emerald-300" : "border-amber-400/40 bg-amber-500/10 text-amber-300"}`}>{webhookStatus}</div>
          </div>
          <div className="mt-3 text-xs leading-5 text-slate-400">Use this URL as the TradingView alert webhook URL when the server is reachable from the internet. For local testing, post to it from this machine; for TradingView cloud alerts, expose the receiver through a secure HTTPS deployment or tunnel.</div>
          <div className="mt-4 text-xs font-semibold uppercase text-slate-500">TradingView Alert Message JSON</div>
          <pre className="mt-2 overflow-x-auto rounded bg-[#080D13] p-3 text-xs text-slate-300">{payloadTemplate}</pre>
        </div>

        <div className="mt-4 rounded border border-terminal-border bg-[#0C131D] p-4">
          <div className="flex items-start gap-3">
            <ShieldAlert className="mt-1 shrink-0 text-amber-300" size={20} />
            <div>
              <h3 className="font-bold text-white">Compliant Live Data Path</h3>
              <p className="mt-1 text-sm leading-6 text-slate-400">
                TradingView does not provide a public backend market-data API for this app to pull full live EGX data. The safe routes are TradingView alerts/webhooks that you configure, embeddable widgets for display, or a licensed provider API.
              </p>
            </div>
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-3">
            <div className="rounded border border-terminal-border bg-terminal-card p-3">
              <div className="flex items-center gap-2 text-sm font-bold text-teal-300"><Server size={16} /> TradingView Webhooks</div>
              <p className="mt-2 text-xs leading-5 text-slate-400">Allowed push model. Alerts send selected values to your endpoint when your alert fires. Requires a reachable HTTPS endpoint for cloud alerts.</p>
            </div>
            <div className="rounded border border-terminal-border bg-terminal-card p-3">
              <div className="flex items-center gap-2 text-sm font-bold text-cyan-300"><ExternalLink size={16} /> Embeddable Widgets</div>
              <p className="mt-2 text-xs leading-5 text-slate-400">Useful for visual chart display only. The Stock Details screen now embeds the official TradingView Advanced Chart widget without extracting feed data.</p>
            </div>
            <div className="rounded border border-terminal-border bg-terminal-card p-3">
              <div className="flex items-center gap-2 text-sm font-bold text-emerald-300"><KeyRound size={16} /> Licensed API</div>
              <p className="mt-2 text-xs leading-5 text-slate-400">Best production path for live bid/ask, candles, market depth, and full EGX coverage. API keys stay in the backend `.env` file.</p>
            </div>
          </div>
          <div className="mt-4 rounded border border-terminal-border bg-terminal-card p-3">
            <div className="text-sm font-bold text-white">EGX-AI API Adapter</div>
            <p className="mt-2 text-xs leading-5 text-slate-400">
              The backend now runs an EGX-AI-compatible stock API inside this Node project. It exposes `/api/v1/stocks` for quotes/scanner rows and `/api/v1/historical-stocks/:symbol` for stored OHLCV snapshots. The embedded source follows the repo's public Mubasher page model and does not fabricate bid/ask because that page does not expose order book fields.
            </p>
            <pre className="mt-2 overflow-x-auto rounded bg-[#080D13] p-3 text-xs text-slate-300">MARKET_DATA_PROVIDER=egx-ai-api{"\n"}EGX_AI_API_BASE_URL="http://localhost:8788"</pre>
          </div>
          <div className="mt-4 rounded border border-terminal-border bg-terminal-card p-3">
            <div className="text-sm font-bold text-white">Refinitiv WebSocket Provider</div>
            <p className="mt-2 text-xs leading-5 text-slate-400">
              Licensed LSEG/Refinitiv path for real-time quotes and top-of-book fields. It uses the official Refinitiv WebSocket JSON flow: login, market-price snapshot request, and ping/pong health handling. It requires your own Refinitiv endpoint, service name, username, app id, and EGX exchange entitlements.
            </p>
            <pre className="mt-2 overflow-x-auto rounded bg-[#080D13] p-3 text-xs text-slate-300">MARKET_DATA_PROVIDER=refinitiv-websocket{"\n"}REFINITIV_AUTH_MODE="rto-password"{"\n"}REFINITIV_WS_USERNAME="machine-account-user"{"\n"}REFINITIV_WS_PASSWORD="machine-account-password"{"\n"}REFINITIV_WS_CLIENT_ID="app-key"{"\n"}REFINITIV_WS_SERVICE="ELEKTRON_DD"{"\n"}REFINITIV_RIC_SUFFIX=".CA"</pre>
          </div>
          <div className="mt-4 rounded border border-terminal-border bg-terminal-card p-3">
            <div className="text-sm font-bold text-white">Authorized WebSocket Bridge</div>
            <p className="mt-2 text-xs leading-5 text-slate-400">
              For licensed or broker-approved websocket feeds, set `AUTHORIZED_MARKET_WS_URL` in `.env` and run `npm run provider-ws`. The bridge normalizes OHLCV messages and imports them into the backend.
            </p>
            <pre className="mt-2 overflow-x-auto rounded bg-[#080D13] p-3 text-xs text-slate-300">{authorizedWsTemplate}</pre>
          </div>
          <div className="mt-4 rounded border border-terminal-border bg-terminal-card p-3">
            <div className="text-sm font-bold text-white">Official TradingView GitHub Check</div>
            <ul className="mt-2 space-y-2 text-xs leading-5 text-slate-400">
              <li><b className="text-slate-200">tradingview/lightweight-charts:</b> already used for our internal OHLCV chart rendering.</li>
              <li><b className="text-slate-200">tradingview/charting-library-tutorial:</b> shows how to connect your own datafeed; it does not provide EGX market data.</li>
              <li><b className="text-slate-200">Official widgets:</b> used as display-only TradingView charts on Stock Details.</li>
            </ul>
          </div>
          <div className="mt-4 rounded border border-amber-400/25 bg-amber-500/8 p-3 text-xs leading-5 text-amber-100">
            Not supported: hidden TradingView websockets, login/session scraping, private endpoints, cookies, CAPTCHA bypass, or paid/protected data extraction. The system will accept data only through configured alerts, uploads, or authorized provider APIs.
          </div>
        </div>
      </section>

      <aside className="space-y-4">
        <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
          <h3 className="font-bold text-white">Data Source Status</h3>
          <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <div className="rounded border border-terminal-border bg-[#0C131D] p-3"><div className="text-xs text-slate-500">Current Source</div><div className="font-semibold text-teal-300">{data.sourceLabel}</div></div>
            <div className="rounded border border-terminal-border bg-[#0C131D] p-3"><div className="text-xs text-slate-500">Last Import</div><div className="font-semibold text-slate-100">{lastImport}</div></div>
            <div className="rounded border border-terminal-border bg-[#0C131D] p-3"><div className="text-xs text-slate-500">Stocks Imported</div><div className="font-semibold text-slate-100">{data.stocks.length}</div></div>
            <div className="rounded border border-terminal-border bg-[#0C131D] p-3"><div className="text-xs text-slate-500">Candles Imported</div><div className="font-semibold text-slate-100">{(csvCandles || data.importedCandles.length).toLocaleString()}</div></div>
            <div className="rounded border border-terminal-border bg-[#0C131D] p-3"><div className="text-xs text-slate-500">Webhook Candles</div><div className="font-semibold text-slate-100">{webhookHealth?.candles ?? 0}</div></div>
            <div className="rounded border border-terminal-border bg-[#0C131D] p-3"><div className="text-xs text-slate-500">Webhook Signals</div><div className="font-semibold text-slate-100">{webhookHealth?.signals ?? 0}</div></div>
          </div>
        </section>
        <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="font-bold text-white">Coverage Gaps</h3>
              <p className="mt-1 text-xs leading-5 text-slate-400">Export this list when testing a provider. A good live provider should return OHLCV and quote data for these symbols too.</p>
            </div>
            <button onClick={exportMissingCoverage} className="inline-flex h-9 items-center gap-2 rounded border border-terminal-border bg-[#0B111A] px-3 text-xs font-semibold text-slate-200 hover:border-teal-400/50">
              <Download size={14} /> Export
            </button>
          </div>
          <div className="mt-3 rounded border border-terminal-border bg-[#0C131D] p-3 text-sm">
            <div className="text-xs text-slate-500">Unavailable from active provider</div>
            <div className="mt-1 text-2xl font-bold text-red-300">{missingRows.length}</div>
            <div className="mt-2 line-clamp-4 text-xs leading-5 text-slate-400">{missingRows.map((row) => row.symbol).join(", ") || "None"}</div>
          </div>
        </section>
        <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
          <h3 className="font-bold text-white">Warnings</h3>
          <ul className="mt-3 space-y-2 text-sm text-slate-400">
            <li className="rounded border border-amber-400/20 bg-amber-500/8 p-3 text-amber-200">CSV rows with missing OHLCV values will be rejected.</li>
            <li className="rounded border border-terminal-border bg-[#0C131D] p-3">Webhook mode expects normalized OHLCV payloads before analysis.</li>
            {warnings.map((warning) => <li key={warning} className="rounded border border-terminal-border bg-[#0C131D] p-3">{warning}</li>)}
          </ul>
        </section>
      </aside>
    </div>
  );
}
