import { Bell, BellRing, ShieldAlert, Target, TrendingDown, TrendingUp } from "lucide-react";
import { useMemo, useState } from "react";
import type { SmartEarlyAlert } from "../types";
import { money } from "../lib/format";
import ActionBadge from "./ActionBadge";

function toneFor(alert: SmartEarlyAlert) {
  if (alert.side === "Bearish") return "border-red-400/30 bg-red-500/10 text-red-100";
  if (alert.severity === "High") return "border-emerald-400/30 bg-emerald-500/10 text-emerald-100";
  if (alert.severity === "Medium") return "border-amber-400/30 bg-amber-500/10 text-amber-100";
  return "border-cyan-400/30 bg-cyan-500/10 text-cyan-100";
}

function iconFor(alert: SmartEarlyAlert) {
  if (alert.side === "Bearish") return <TrendingDown size={16} />;
  if (alert.alertType === "Pullback Near Buy Zone") return <Target size={16} />;
  return <TrendingUp size={16} />;
}

export default function SmartAlertsPanel({
  alerts,
  onOpenStock,
  compact = false,
}: {
  alerts: SmartEarlyAlert[];
  onOpenStock?: (symbol: string) => void;
  compact?: boolean;
}) {
  const [notificationState, setNotificationState] = useState<"idle" | "enabled" | "denied" | "unsupported">("idle");
  const visibleAlerts = alerts.slice(0, compact ? 5 : 12);
  const highUrgencyCount = useMemo(() => alerts.filter((alert) => alert.severity === "High").length, [alerts]);

  const enableBrowserAlerts = async () => {
    if (!("Notification" in window)) {
      setNotificationState("unsupported");
      return;
    }
    const permission = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
    if (permission !== "granted") {
      setNotificationState("denied");
      return;
    }
    setNotificationState("enabled");
    const topAlert = alerts.find((alert) => alert.severity === "High") ?? alerts[0];
    if (topAlert) {
      new Notification(`${topAlert.symbol} ${topAlert.alertType}`, {
        body: `${topAlert.action} | urgency ${topAlert.urgencyScore}/100 | ${topAlert.dataFreshness}`,
      });
    }
  };

  return (
    <section className="rounded-lg border border-terminal-border bg-terminal-card">
      <div className="flex flex-col gap-2 border-b border-terminal-border px-3 py-2 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <BellRing size={16} className="text-amber-300" />
            <h2 className="text-sm font-bold text-white">Smart Early Alerts</h2>
            <span className="rounded border border-amber-400/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-bold text-amber-200">{highUrgencyCount} high</span>
          </div>
          <p className="mt-1 text-xs text-slate-500">Ranked by setup quality, volume pressure, buy-zone distance, and risk control</p>
        </div>
        <button
          type="button"
          onClick={enableBrowserAlerts}
          className="inline-flex h-8 items-center justify-center gap-2 rounded border border-terminal-border bg-[#0C131D] px-3 text-xs font-semibold text-slate-200 hover:border-amber-400/50 hover:text-amber-200"
        >
          <Bell size={14} />
          {notificationState === "enabled" ? "Browser Alerts On" : notificationState === "denied" ? "Alerts Blocked" : notificationState === "unsupported" ? "Alerts Unsupported" : "Enable Browser Alerts"}
        </button>
      </div>

      <div className={compact ? "divide-y divide-terminal-border/70" : "grid gap-2 p-3 xl:grid-cols-2"}>
        {visibleAlerts.map((alert) => (
          <button
            key={alert.id}
            type="button"
            onClick={() => onOpenStock?.(alert.symbol)}
            className={`w-full rounded border px-3 py-2 text-left transition hover:brightness-110 ${toneFor(alert)}`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2 text-xs font-bold">
                {iconFor(alert)}
                <span className="text-white">{alert.symbol}</span>
                <span className="text-[11px] text-slate-400">{alert.timeframe}</span>
                <span className="truncate">{alert.alertType}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="rounded bg-black/20 px-2 py-0.5 text-[10px] font-bold">{alert.severity}</span>
                <span className="rounded bg-black/20 px-2 py-0.5 text-[10px] font-bold">{alert.urgencyScore}/100</span>
              </div>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-300">
              <span className="truncate">{alert.companyName}</span>
              <ActionBadge action={alert.action} />
              <span>Price {money(alert.price)}</span>
              <span>Zone {alert.entryZone}</span>
            </div>
            <div className="mt-2 grid gap-2 text-xs leading-5 text-slate-200 lg:grid-cols-2">
              <div><b className="text-slate-400">Trigger:</b> {alert.trigger}</div>
              <div><b className="text-slate-400">Invalidation:</b> {alert.invalidation}</div>
            </div>
            {!compact && <div className="mt-2 text-xs leading-5 text-slate-300">{alert.reason}</div>}
            <div className="mt-2 flex items-start gap-2 rounded border border-terminal-border/70 bg-black/15 px-2 py-1 text-[11px] leading-4 text-slate-400">
              <ShieldAlert size={13} className="mt-0.5 shrink-0 text-amber-300" />
              <span>{alert.dataFreshness}</span>
            </div>
          </button>
        ))}
        {!visibleAlerts.length && (
          <div className="p-4 text-sm text-slate-500">
            No smart early alerts from the active provider candles. The system will not create fake alarms without usable OHLCV data.
          </div>
        )}
      </div>
    </section>
  );
}
