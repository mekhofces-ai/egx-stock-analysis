import type { ReactNode } from "react";

export default function KPICard({ label, value, detail, icon, tone }: { label: string; value: string | number; detail: string; icon: ReactNode; tone: string }) {
  return (
    <section className="rounded-lg border border-terminal-border bg-terminal-card p-4 shadow-glow">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-normal text-slate-500">{label}</div>
          <div className="mt-1 text-2xl font-bold text-white">{value}</div>
        </div>
        <div className={`grid h-9 w-9 place-items-center rounded ${tone}`}>{icon}</div>
      </div>
      <div className="mt-3 text-xs text-slate-400">{detail}</div>
    </section>
  );
}
