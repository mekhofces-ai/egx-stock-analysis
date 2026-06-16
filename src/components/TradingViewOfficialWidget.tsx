import { useEffect, useRef } from "react";
import { ExternalLink, ShieldAlert } from "lucide-react";

function normalizeTradingViewSymbol(symbol: string) {
  const clean = symbol.replace(/^EGX:/i, "").toUpperCase();
  return `EGX:${clean}`;
}

export default function TradingViewOfficialWidget({ symbol }: { symbol: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const tvSymbol = normalizeTradingViewSymbol(symbol);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    container.innerHTML = "";

    const widget = document.createElement("div");
    widget.className = "tradingview-widget-container__widget";
    widget.style.height = "520px";
    widget.style.width = "100%";

    const copyright = document.createElement("div");
    copyright.className = "tradingview-widget-copyright";
    copyright.innerHTML = `<a href="https://www.tradingview.com/symbols/${tvSymbol.replace(":", "-")}/" rel="noopener nofollow" target="_blank"><span class="blue-text">${tvSymbol} chart</span></a> by TradingView`;

    const script = document.createElement("script");
    script.type = "text/javascript";
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: tvSymbol,
      interval: "D",
      timezone: "Africa/Cairo",
      theme: "dark",
      style: "1",
      locale: "en",
      allow_symbol_change: true,
      calendar: false,
      support_host: "https://www.tradingview.com",
      hide_side_toolbar: false,
      withdateranges: true,
      save_image: false,
      details: true,
      hotlist: false,
      studies: ["Volume@tv-basicstudies"],
    });

    container.append(widget, copyright);
    const scriptTimer = window.setTimeout(() => {
      if (containerRef.current !== container || !container.isConnected) return;
      container.append(script);
    }, 0);

    return () => {
      window.clearTimeout(scriptTimer);
      container.innerHTML = "";
    };
  }, [tvSymbol]);

  return (
    <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
      <div className="mb-3 flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h2 className="text-base font-bold text-white">Official TradingView Display</h2>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            Embeddable TradingView widget for visual review only. App analysis, rankings, and alerts do not extract data from this widget.
          </p>
        </div>
        <a
          href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol)}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-8 items-center justify-center gap-2 rounded border border-terminal-border bg-[#0C131D] px-3 text-xs font-semibold text-slate-200 hover:border-teal-400/50 hover:text-teal-200"
        >
          <ExternalLink size={14} /> Open {tvSymbol}
        </a>
      </div>
      <div className="overflow-hidden rounded border border-terminal-border bg-[#0C131D]">
        <div ref={containerRef} className="tradingview-widget-container h-[560px] w-full" />
      </div>
      <div className="mt-3 flex items-start gap-2 rounded border border-amber-400/25 bg-amber-500/8 px-3 py-2 text-xs leading-5 text-amber-100">
        <ShieldAlert size={14} className="mt-0.5 shrink-0" />
        <span>Display-only widget. If TradingView has a chart for this symbol, it may show data here, but the app does not copy, scrape, or rank from widget content.</span>
      </div>
    </section>
  );
}
