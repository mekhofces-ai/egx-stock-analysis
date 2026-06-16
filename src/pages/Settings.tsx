import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Bell, RefreshCw, RotateCcw, Save, Server, ShieldCheck } from "lucide-react";
import { settings, type BackendDataStatus } from "../data/mockData";
import { backendBaseUrl, fetchBackendDataStatus } from "../lib/dataAdapters";

type LocalSettings = {
  defaultMode: "Aggressive" | "Balanced" | "Safe";
  minScore: number;
  defaultRisk: string;
  scalpTargetPct: number;
  swingTargetPct: number;
  longTargetPct: number;
  atrStopMultiplier: number;
  egyptTimezone: string;
  dataSourceType: string;
  apiEndpoint: string;
  alertsEnabled: boolean;
  webhookEnabled: boolean;
};

const storageKey = "egx-smart-screener-settings-v2";

const defaults: LocalSettings = {
  defaultMode: settings.defaultMode,
  minScore: settings.minScore,
  defaultRisk: settings.defaultRisk,
  scalpTargetPct: 2.5,
  swingTargetPct: 6,
  longTargetPct: 12,
  atrStopMultiplier: 1.6,
  egyptTimezone: settings.egyptTimezone,
  dataSourceType: "EGX-AI API",
  apiEndpoint: backendBaseUrl(),
  alertsEnabled: true,
  webhookEnabled: true,
};

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function loadLocalSettings(): LocalSettings {
  try {
    const parsed = JSON.parse(localStorage.getItem(storageKey) ?? "null") as Partial<LocalSettings> | null;
    return { ...defaults, ...(parsed ?? {}) };
  } catch {
    return defaults;
  }
}

function statusTone(status?: string) {
  if (status === "available") return "border-emerald-400/35 bg-emerald-500/10 text-emerald-200";
  if (status === "degraded") return "border-amber-400/35 bg-amber-500/10 text-amber-200";
  return "border-red-400/35 bg-red-500/10 text-red-200";
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <span className="text-xs font-semibold uppercase tracking-normal text-slate-500">{children}</span>;
}

export default function SettingsPage() {
  const [form, setForm] = useState<LocalSettings>(() => loadLocalSettings());
  const [saved, setSaved] = useState("");
  const [runtimeStatus, setRuntimeStatus] = useState<BackendDataStatus | null>(null);
  const [runtimeMessage, setRuntimeMessage] = useState("Checking backend status...");
  const [isChecking, setIsChecking] = useState(false);

  const activeProvider = runtimeStatus?.activeProvider ?? "unknown";
  const activeProviderStatus = useMemo(
    () => runtimeStatus?.providers?.find((provider) => provider.provider === activeProvider),
    [activeProvider, runtimeStatus?.providers],
  );
  const currentPrices = runtimeStatus?.symbolsWithCurrentPrices ?? 0;
  const providerRows = runtimeStatus?.symbolsWithProviderData ?? 0;
  const strategyRows = runtimeStatus?.symbolsWithStrategyAnalysis ?? 0;
  const totalSymbols = runtimeStatus?.totalSymbols ?? 0;

  const update = <K extends keyof LocalSettings>(key: K, value: LocalSettings[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setSaved("");
  };

  const refreshRuntimeStatus = async () => {
    setIsChecking(true);
    setRuntimeMessage("Checking backend status...");
    try {
      const status = await fetchBackendDataStatus();
      setRuntimeStatus(status);
      setRuntimeMessage(`Runtime provider checked: ${status.activeProvider ?? "unknown"}.`);
    } catch (error) {
      setRuntimeMessage(error instanceof Error ? error.message : "Unable to check backend status.");
      setRuntimeStatus(null);
    } finally {
      setIsChecking(false);
    }
  };

  useEffect(() => {
    void refreshRuntimeStatus();
  }, []);

  const saveSettings = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const sanitized: LocalSettings = {
      ...form,
      minScore: clamp(Number(form.minScore) || 0, 0, 10),
      scalpTargetPct: clamp(Number(form.scalpTargetPct) || defaults.scalpTargetPct, 0.1, 50),
      swingTargetPct: clamp(Number(form.swingTargetPct) || defaults.swingTargetPct, 0.1, 80),
      longTargetPct: clamp(Number(form.longTargetPct) || defaults.longTargetPct, 0.1, 150),
      atrStopMultiplier: clamp(Number(form.atrStopMultiplier) || defaults.atrStopMultiplier, 0.1, 10),
      apiEndpoint: form.apiEndpoint.trim() || backendBaseUrl(),
      egyptTimezone: form.egyptTimezone.trim() || "Africa/Cairo",
    };
    setForm(sanitized);
    localStorage.setItem(storageKey, JSON.stringify(sanitized));
    window.dispatchEvent(new CustomEvent("egx-settings-saved", { detail: sanitized }));
    setSaved(`Settings saved locally at ${new Date().toLocaleTimeString("en-GB", { timeZone: "Africa/Cairo" })}.`);
  };

  const resetSettings = () => {
    localStorage.removeItem(storageKey);
    setForm(defaults);
    setSaved("Settings reset to app defaults.");
  };

  const exportSettings = () => {
    const blob = new Blob([JSON.stringify(form, null, 2)], { type: "application/json;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "egx-smart-screener-settings.json";
    link.click();
    URL.revokeObjectURL(link.href);
  };

  return (
    <form onSubmit={saveSettings} className="space-y-4">
      <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck size={18} className="text-teal-300" />
              <h2 className="text-lg font-bold text-white">Settings</h2>
            </div>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-400">
              These settings are saved locally in this browser and control the app preferences panel. Backend provider changes still require editing `.env` and restarting the backend, so the app will not pretend a provider was changed when the server is still running another source.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="submit" className="inline-flex h-10 items-center justify-center gap-2 rounded bg-teal-500 px-4 text-sm font-semibold text-[#04110F]">
              <Save size={16} /> Save Settings
            </button>
            <button type="button" onClick={resetSettings} className="inline-flex h-10 items-center justify-center gap-2 rounded border border-terminal-border bg-[#0B111A] px-4 text-sm text-slate-200">
              <RotateCcw size={16} /> Reset
            </button>
          </div>
        </div>
        {saved && <div className="mt-3 rounded border border-emerald-400/30 bg-emerald-500/10 p-3 text-sm text-emerald-300">{saved}</div>}
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
          <h3 className="text-base font-bold text-white">Trading Defaults</h3>
          <div className="mt-4 grid gap-4">
            <label className="grid gap-2 text-sm text-slate-400">
              <FieldLabel>Default mode</FieldLabel>
              <select value={form.defaultMode} onChange={(event) => update("defaultMode", event.target.value as LocalSettings["defaultMode"])} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-slate-100">
                <option>Aggressive</option>
                <option>Balanced</option>
                <option>Safe</option>
              </select>
            </label>
            <label className="grid gap-2 text-sm text-slate-400">
              <FieldLabel>Minimum score</FieldLabel>
              <input type="number" value={form.minScore} min={0} max={10} onChange={(event) => update("minScore", Number(event.target.value))} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-slate-100" />
            </label>
            <label className="grid gap-2 text-sm text-slate-400">
              <FieldLabel>Risk profile</FieldLabel>
              <select value={form.defaultRisk} onChange={(event) => update("defaultRisk", event.target.value)} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-slate-100">
                <option>0.5% per trade</option>
                <option>1.0% per trade</option>
                <option>1.5% per trade</option>
                <option>2.0% per trade</option>
              </select>
            </label>
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="grid gap-2 text-sm text-slate-400">
                <FieldLabel>Scalp target %</FieldLabel>
                <input type="number" step="0.1" value={form.scalpTargetPct} onChange={(event) => update("scalpTargetPct", Number(event.target.value))} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-slate-100" />
              </label>
              <label className="grid gap-2 text-sm text-slate-400">
                <FieldLabel>Swing target %</FieldLabel>
                <input type="number" step="0.1" value={form.swingTargetPct} onChange={(event) => update("swingTargetPct", Number(event.target.value))} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-slate-100" />
              </label>
              <label className="grid gap-2 text-sm text-slate-400">
                <FieldLabel>Long target %</FieldLabel>
                <input type="number" step="0.1" value={form.longTargetPct} onChange={(event) => update("longTargetPct", Number(event.target.value))} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-slate-100" />
              </label>
            </div>
            <label className="grid gap-2 text-sm text-slate-400">
              <FieldLabel>ATR stop multiplier</FieldLabel>
              <input type="number" step="0.1" value={form.atrStopMultiplier} onChange={(event) => update("atrStopMultiplier", Number(event.target.value))} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-slate-100" />
            </label>
            <div className="rounded border border-amber-400/30 bg-amber-500/8 p-3 text-xs leading-5 text-amber-100">
              Current backend strategy calculations still run with the server-side Balanced Omar Smart PRO V3 constants. These local defaults are ready for UI workflows and future provider/runtime wiring.
            </div>
          </div>
        </section>

        <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
          <div className="flex items-center gap-2">
            <Server size={18} className="text-cyan-300" />
            <h3 className="text-base font-bold text-white">Data Source & Runtime</h3>
          </div>
          <div className="mt-4 grid gap-4">
            <label className="grid gap-2 text-sm text-slate-400">
              <FieldLabel>Egypt timezone</FieldLabel>
              <input value={form.egyptTimezone} onChange={(event) => update("egyptTimezone", event.target.value)} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-slate-100" />
            </label>
            <label className="grid gap-2 text-sm text-slate-400">
              <FieldLabel>Preferred data source type</FieldLabel>
              <select value={form.dataSourceType} onChange={(event) => update("dataSourceType", event.target.value)} className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-slate-100">
                <option>Public delayed API</option>
                <option>Twelve Data API key</option>
                <option>EGX-AI API</option>
                <option>Refinitiv WebSocket</option>
                <option>Licensed real-time API</option>
                <option>TradingView Webhook</option>
              </select>
            </label>
            <label className="grid gap-2 text-sm text-slate-400">
              <FieldLabel>Preferred API endpoint</FieldLabel>
              <input value={form.apiEndpoint} onChange={(event) => update("apiEndpoint", event.target.value)} placeholder="http://localhost:8788 or https://provider.example/api" className="h-10 rounded border border-terminal-border bg-[#0B111A] px-3 text-slate-100" />
            </label>

            <div className="rounded border border-terminal-border bg-[#0C131D] p-3">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="text-xs font-bold uppercase tracking-normal text-slate-500">Active runtime provider</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <span className="rounded border border-cyan-400/35 bg-cyan-500/10 px-2 py-1 text-sm font-bold text-cyan-200">{activeProvider}</span>
                    <span className={`rounded border px-2 py-1 text-xs font-semibold ${statusTone(activeProviderStatus?.status)}`}>{activeProviderStatus?.status ?? "not checked"}</span>
                    <span className="rounded border border-terminal-border bg-terminal-card px-2 py-1 text-xs text-slate-300">Bid/ask: {runtimeStatus?.bidAskStatus ?? "unknown"}</span>
                  </div>
                </div>
                <button type="button" onClick={refreshRuntimeStatus} disabled={isChecking} className="inline-flex h-9 items-center justify-center gap-2 rounded border border-terminal-border bg-[#0B111A] px-3 text-xs font-semibold text-slate-200 disabled:opacity-60">
                  <RefreshCw size={14} className={isChecking ? "animate-spin" : ""} /> Refresh Status
                </button>
              </div>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div className="text-xs leading-5 text-slate-400">Current priced symbols: {runtimeStatus ? `${currentPrices} / ${totalSymbols}` : "-"}</div>
                <div className="text-xs leading-5 text-slate-400">Provider rows usable: {runtimeStatus ? `${providerRows} / ${totalSymbols}` : "-"}</div>
                <div className="text-xs leading-5 text-slate-400">Strategy analysis coverage: {runtimeStatus ? `${strategyRows} / ${totalSymbols}` : "-"}</div>
                <div className="text-xs leading-5 text-slate-400">Historical candle coverage: {runtimeStatus?.symbolsWithCandles ?? "-"} / {runtimeStatus?.totalSymbols ?? "-"}</div>
                <div className="text-xs leading-5 text-slate-400">Latest candle: {runtimeStatus?.latestCompletedCandleAt ? new Date(runtimeStatus.latestCompletedCandleAt).toLocaleDateString("en-GB", { timeZone: "Africa/Cairo" }) : "-"}</div>
                <div className="text-xs leading-5 text-slate-400">Latest scanner row: {runtimeStatus?.latestScannerAt ? new Date(runtimeStatus.latestScannerAt).toLocaleTimeString("en-GB", { timeZone: "Africa/Cairo", hour12: false }) : "-"}</div>
                <div className="text-xs leading-5 text-slate-400">Auto refresh: {runtimeStatus?.autoRefreshEnabled ? "ON" : "OFF"} {runtimeStatus?.autoRefreshIntervalMs ? `/${Math.round(runtimeStatus.autoRefreshIntervalMs / 60000)}m` : ""}</div>
                <div className="text-xs leading-5 text-slate-400">Real bid/ask snapshots: {runtimeStatus?.realBidAskSnapshots ?? 0}</div>
              </div>
              <div className="mt-3 text-xs leading-5 text-slate-500">{runtimeMessage}</div>
              {runtimeStatus?.scannerReason && <div className="mt-2 text-xs leading-5 text-slate-400">Scanner: {runtimeStatus.scannerStatus ?? "unknown"} - {runtimeStatus.scannerReason}</div>}
              {activeProviderStatus?.reason && <div className="mt-2 text-xs leading-5 text-slate-400">{activeProviderStatus.reason}</div>}
            </div>

            <div className="rounded border border-red-400/30 bg-red-500/8 p-3 text-xs leading-5 text-red-100">
              <div className="font-bold text-red-200">TradingView Screener package check</div>
              <p className="mt-2">
                `shner-elmo/TradingView-Screener` wraps TradingView's scanner endpoint. Its real-time path requires TradingView session cookies, and this app will not use copied cookies, browser sessions, login automation, hidden endpoints, or TradingView-protected feeds as a backend data source.
              </p>
              <p className="mt-2 text-slate-300">
                Safe TradingView routes remain enabled: user-configured alerts/webhooks and official embeddable chart widgets for display. Real backend data must come from CSV, Twelve Data, EGX-AI-compatible snapshots, Refinitiv/LSEG, or another authorized provider.
              </p>
            </div>
          </div>
        </section>
      </div>

      <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
        <div className="flex items-center gap-2">
          <Bell size={18} className="text-amber-300" />
          <h3 className="text-base font-bold text-white">Alerts & Export</h3>
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-3">
          <label className="flex items-center justify-between rounded border border-terminal-border bg-[#0C131D] p-3 text-sm text-slate-300">
            Enable browser alerts
            <input type="checkbox" checked={form.alertsEnabled} onChange={(event) => update("alertsEnabled", event.target.checked)} className="h-5 w-5 accent-teal-400" />
          </label>
          <label className="flex items-center justify-between rounded border border-terminal-border bg-[#0C131D] p-3 text-sm text-slate-300">
            Webhook ingestion enabled
            <input type="checkbox" checked={form.webhookEnabled} onChange={(event) => update("webhookEnabled", event.target.checked)} className="h-5 w-5 accent-teal-400" />
          </label>
          <button type="button" onClick={exportSettings} className="inline-flex h-12 items-center justify-center gap-2 rounded border border-terminal-border bg-[#0B111A] px-4 text-sm font-semibold text-slate-200">
            Export Settings JSON
          </button>
        </div>
      </section>
    </form>
  );
}
