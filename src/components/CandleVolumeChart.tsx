import { useEffect, useMemo, useRef } from "react";
import { CandlestickSeries, createChart, HistogramSeries, LineSeries, type UTCTimestamp } from "lightweight-charts";
import type { ImportedCandle } from "../types";
import { emaSeries } from "../lib/analysis";
import { compact, money } from "../lib/format";

function toTime(value: string): UTCTimestamp {
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp;
}

function candleDate(value: string) {
  return new Date(value).toLocaleDateString("en-GB", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit" });
}

export default function CandleVolumeChart({ candles, symbol }: { candles: ImportedCandle[]; symbol: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const ordered = useMemo(() => [...candles].sort((a, b) => a.candleTime.localeCompare(b.candleTime)), [candles]);
  const latest = ordered[ordered.length - 1];

  useEffect(() => {
    const container = containerRef.current;
    if (!container || ordered.length < 2) return undefined;

    const chart = createChart(container, {
      autoSize: true,
      height: 430,
      layout: {
        background: { color: "#111827" },
        textColor: "#CBD5E1",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(36, 48, 65, 0.55)" },
        horzLines: { color: "rgba(36, 48, 65, 0.55)" },
      },
      crosshair: {
        mode: 0,
      },
      rightPriceScale: {
        borderColor: "#243041",
        scaleMargins: { top: 0.08, bottom: 0.18 },
      },
      timeScale: {
        borderColor: "#243041",
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22C55E",
      downColor: "#EF4444",
      borderUpColor: "#22C55E",
      borderDownColor: "#EF4444",
      wickUpColor: "#86EFAC",
      wickDownColor: "#FCA5A5",
    });

    const closes = ordered.map((candle) => candle.close);
    const ema9 = emaSeries(closes, 9);
    const ema21 = emaSeries(closes, 21);
    const ema9Series = chart.addSeries(LineSeries, { color: "#38BDF8", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const ema21Series = chart.addSeries(LineSeries, { color: "#F59E0B", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      lastValueVisible: false,
      color: "#14B8A6",
    }, 1);

    candleSeries.setData(ordered.map((candle) => ({
      time: toTime(candle.candleTime),
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    })));
    ema9Series.setData(ordered.map((candle, index) => ({ time: toTime(candle.candleTime), value: Number(ema9[index].toFixed(2)) })));
    ema21Series.setData(ordered.map((candle, index) => ({ time: toTime(candle.candleTime), value: Number(ema21[index].toFixed(2)) })));
    volumeSeries.setData(ordered.map((candle) => ({
      time: toTime(candle.candleTime),
      value: candle.volume,
      color: candle.close >= candle.open ? "rgba(34, 197, 94, 0.42)" : "rgba(239, 68, 68, 0.42)",
    })));

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [ordered]);

  return (
    <section className="rounded-lg border border-terminal-border bg-terminal-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-bold text-white">{symbol} Price Chart</h2>
          <div className="mt-1 text-xs text-slate-500">Daily OHLCV candles with EMA 9 / EMA 21 and volume direction coloring</div>
        </div>
        <div className="flex flex-wrap gap-2 text-[11px]">
          <span className="rounded border border-sky-400/30 bg-sky-500/10 px-2 py-1 text-sky-300">EMA 9</span>
          <span className="rounded border border-amber-400/30 bg-amber-500/10 px-2 py-1 text-amber-300">EMA 21</span>
          <span className="rounded border border-terminal-border bg-[#0C131D] px-2 py-1 text-slate-400">Volume: green up / red down</span>
        </div>
      </div>
      {ordered.length ? (
        <>
          <div ref={containerRef} className="h-[430px] w-full" />
          <div className="mt-3 grid gap-2 text-xs text-slate-400 sm:grid-cols-5">
            <span>Last candle: <b className="text-white">{candleDate(latest.candleTime)}</b></span>
            <span>Last close: <b className="text-white">{money(latest.close)}</b></span>
            <span>High: <b className="text-emerald-300">{money(latest.high)}</b></span>
            <span>Low: <b className="text-red-300">{money(latest.low)}</b></span>
            <span>Volume: <b className="text-slate-200">{compact(latest.volume)}</b></span>
          </div>
        </>
      ) : (
        <div className="grid h-[320px] place-items-center rounded border border-terminal-border bg-[#0C131D] text-sm text-slate-500">
          No candles returned by the active provider for this symbol.
        </div>
      )}
    </section>
  );
}
