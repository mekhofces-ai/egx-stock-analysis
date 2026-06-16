import { Bell, BarChart3, Bot, Database, Gauge, LineChart, Radar, Search, Settings, Signal, Star, Table2 } from "lucide-react";
import type { ReactNode } from "react";
import { egyptNow } from "../lib/format";

const navItems = [
  { id: "dashboard", label: "Dashboard", icon: Gauge },
  { id: "best-stocks", label: "Best Stocks", icon: Star },
  { id: "screener", label: "Screener", icon: Table2 },
  { id: "stock", label: "Stock Details", icon: LineChart },
  { id: "analyst-bot", label: "Analyst Bot", icon: Bot },
  { id: "trade-signals", label: "Trade Signals", icon: Radar },
  { id: "import-data", label: "Import Data", icon: Database },
  { id: "signals", label: "Signals", icon: Signal },
  { id: "settings", label: "Settings", icon: Settings },
];

export default function Shell({
  activePage,
  setActivePage,
  selectedSymbol,
  setSelectedSymbol,
  children,
}: {
  activePage: string;
  setActivePage: (page: string) => void;
  selectedSymbol: string;
  setSelectedSymbol: (symbol: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-terminal-bg text-slate-100">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-terminal-border bg-[#081018] lg:block">
        <div className="border-b border-terminal-border px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded bg-teal-500/15 text-teal-300">
              <BarChart3 size={20} />
            </div>
            <div>
              <div className="text-sm font-bold text-white">StockSignalPro</div>
              <div className="text-xs text-slate-500">EGX Smart Screener</div>
            </div>
          </div>
        </div>
        <nav className="space-y-1 px-3 py-4">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activePage === item.id || (item.id === "stock" && activePage === "stock-details");
            return (
              <button key={item.id} onClick={() => setActivePage(item.id === "stock" ? "stock-details" : item.id)} className={`flex w-full items-center gap-3 rounded px-3 py-2 text-left text-sm transition ${isActive ? "bg-teal-500/14 text-teal-200" : "text-slate-400 hover:bg-white/5 hover:text-slate-100"}`}>
                <Icon size={17} />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="absolute bottom-0 left-0 right-0 border-t border-terminal-border p-4 text-xs text-slate-500">
          Educational analysis only. This is not financial advice. No profit is guaranteed. Trading involves risk.
        </div>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 border-b border-terminal-border bg-[#0B0F14]/95 backdrop-blur">
          <div className="flex flex-col gap-3 px-4 py-3 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <h1 className="text-lg font-bold text-white sm:text-xl">EGX Smart Screener</h1>
              <div className="text-xs text-slate-500">Egypt time: {egyptNow()} - Multi-timeframe technical ranking</div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded border border-teal-400/30 bg-teal-500/10 px-3 py-2 text-xs font-semibold text-teal-300">Provider Data Mode</span>
              <label className="relative min-w-[220px] flex-1 sm:flex-none">
                <Search className="absolute left-3 top-2.5 text-slate-500" size={15} />
                <input value={selectedSymbol} onChange={(event) => setSelectedSymbol(event.target.value.toUpperCase())} className="h-9 w-full rounded border border-terminal-border bg-[#0D1520] pl-9 pr-3 text-sm text-slate-100 outline-none focus:border-teal-400" placeholder="Search symbol" />
              </label>
              <button className="grid h-9 w-9 place-items-center rounded border border-terminal-border bg-terminal-card text-slate-300 hover:text-white" aria-label="Notifications">
                <Bell size={16} />
              </button>
            </div>
          </div>
          <div className="flex gap-1 overflow-x-auto px-3 pb-3 lg:hidden">
            {navItems.map((item) => (
              <button key={item.id} onClick={() => setActivePage(item.id === "stock" ? "stock-details" : item.id)} className={`whitespace-nowrap rounded px-3 py-2 text-xs ${activePage === item.id || (item.id === "stock" && activePage === "stock-details") ? "bg-teal-500/20 text-teal-200" : "bg-terminal-card text-slate-400"}`}>
                {item.label}
              </button>
            ))}
          </div>
        </header>
        <main className="px-4 py-4 sm:px-5 xl:px-6">{children}</main>
      </div>
    </div>
  );
}
