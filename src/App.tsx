import { useEffect, useMemo, useState } from "react";
import Shell from "./components/Shell";
import Dashboard from "./pages/Dashboard";
import BestStocks from "./pages/BestStocks";
import Screener from "./pages/Screener";
import StockDetails from "./pages/StockDetails";
import AnalystBot from "./pages/AnalystBot";
import TradeSignals from "./pages/TradeSignals";
import ImportData from "./pages/ImportData";
import Signals from "./pages/Signals";
import SettingsPage from "./pages/Settings";
import { buildMarketData, defaultMarketData } from "./data/mockData";
import { fetchBackendDataStatus, fetchBackendScanner, fetchBackendSymbols, refreshBackendMarket } from "./lib/dataAdapters";

function LoadingMarketData() {
  return (
    <div className="rounded-lg border border-terminal-border bg-terminal-card p-6">
      <div className="text-sm font-semibold uppercase tracking-[0.08em] text-cyan-300">Loading EGX market data</div>
      <div className="mt-2 text-2xl font-bold text-white">Fetching delayed daily candles and technical analysis</div>
      <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-400">
        The first scan can take a few seconds because the provider checks the full EGX symbol universe. No fake live prices or fake bid/ask are shown while the provider response is loading.
      </p>
    </div>
  );
}

export default function App() {
  const pageFromPath = () => {
    const path = window.location.pathname;
    if (path.startsWith("/best-stocks")) return "best-stocks";
    if (path.startsWith("/screener")) return "screener";
    if (path.startsWith("/stock/")) return "stock-details";
    if (path.startsWith("/analyst-bot")) return "analyst-bot";
    if (path.startsWith("/trade-signals")) return "trade-signals";
    if (path.startsWith("/import-data")) return "import-data";
    if (path.startsWith("/signals")) return "signals";
    if (path.startsWith("/settings")) return "settings";
    return "dashboard";
  };
  const symbolFromPath = () => window.location.pathname.split("/stock/")[1]?.toUpperCase() ?? "COMI";
  const [activePage, setActivePageState] = useState(pageFromPath);
  const [selectedSymbol, setSelectedSymbolState] = useState(symbolFromPath);
  const [marketData, setMarketData] = useState(defaultMarketData);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");

  const setActivePage = (page: string) => {
    setActivePageState(page);
    const path = page === "dashboard" ? "/dashboard" : page === "stock-details" ? `/stock/${selectedSymbol}` : `/${page}`;
    window.history.pushState(null, "", path);
  };

  const setSelectedSymbol = (symbol: string) => {
    setSelectedSymbolState(symbol);
    if (activePage === "stock-details") window.history.replaceState(null, "", `/stock/${symbol || "COMI"}`);
  };

  useEffect(() => {
    const onPopState = () => {
      setActivePageState(pageFromPath());
      setSelectedSymbolState(symbolFromPath());
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const loadProviderData = async (cancelledRef?: { cancelled: boolean }) => {
      try {
        const [backendSymbols, backendScanner, backendStatus] = await Promise.all([
          fetchBackendSymbols().catch(() => undefined),
          fetchBackendScanner().catch(() => []),
          fetchBackendDataStatus().catch(() => undefined),
        ]);
        if (!cancelledRef?.cancelled) {
          setMarketData(buildMarketData([], [], true, backendSymbols, backendScanner, backendStatus));
          setIsLoading(false);
        }
      } catch {
        if (!cancelledRef?.cancelled) {
          setMarketData(defaultMarketData);
          setIsLoading(false);
        }
      }
    };

  const handleManualRefresh = async () => {
    setIsRefreshing(true);
    setRefreshMessage("Checking provider data now...");
    try {
      const symbolsPromise = fetchBackendSymbols().catch(() => undefined);
      const refreshResult = await refreshBackendMarket();
      const [symbols, status] = await Promise.all([
        symbolsPromise,
        fetchBackendDataStatus().catch(() => undefined),
      ]);
      setMarketData(buildMarketData([], [], true, symbols, refreshResult.data ?? [], status));
      const summary = refreshResult.summary;
      const priced = summary?.priced ?? summary?.available;
      setRefreshMessage(summary ? `Refresh complete: ${priced}/${summary.total} priced, ${summary.unavailable} unavailable.` : "Refresh complete.");
    } catch (error) {
      setRefreshMessage(error instanceof Error ? error.message : "Manual refresh failed.");
    } finally {
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    const cancelledRef = { cancelled: false };
    void loadProviderData(cancelledRef);
    const timer = window.setInterval(() => void loadProviderData(cancelledRef), 15000);
    return () => {
      cancelledRef.cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const normalizedSymbol = useMemo(() => {
    const exact = marketData.stocks.find((stock) => stock.symbol === selectedSymbol.trim().toUpperCase());
    return exact?.symbol ?? "COMI";
  }, [marketData.stocks, selectedSymbol]);

  const openStock = (symbol: string) => {
    setSelectedSymbolState(symbol);
    setActivePageState("stock-details");
    window.history.pushState(null, "", `/stock/${symbol}`);
  };

  const waitsForMarketData = ["dashboard", "best-stocks", "screener", "stock-details", "analyst-bot", "trade-signals", "signals"].includes(activePage);

  return (
    <Shell activePage={activePage} setActivePage={setActivePage} selectedSymbol={selectedSymbol} setSelectedSymbol={setSelectedSymbol}>
      {isLoading && waitsForMarketData ? <LoadingMarketData /> : (
        <>
          {activePage === "dashboard" && <Dashboard data={marketData} onOpenStock={openStock} onManualRefresh={handleManualRefresh} isRefreshing={isRefreshing} refreshMessage={refreshMessage} />}
          {activePage === "best-stocks" && <BestStocks data={marketData} onOpenStock={openStock} />}
          {activePage === "screener" && <Screener data={marketData} onOpenStock={openStock} />}
          {activePage === "stock-details" && <StockDetails data={marketData} symbol={normalizedSymbol} />}
          {activePage === "analyst-bot" && <AnalystBot data={marketData} selectedSymbol={normalizedSymbol} onManualRefresh={handleManualRefresh} isRefreshing={isRefreshing} refreshMessage={refreshMessage} />}
          {activePage === "trade-signals" && <TradeSignals data={marketData} onOpenStock={openStock} onManualRefresh={handleManualRefresh} isRefreshing={isRefreshing} refreshMessage={refreshMessage} />}
          {activePage === "import-data" && <ImportData data={marketData} />}
          {activePage === "signals" && <Signals data={marketData} />}
          {activePage === "settings" && <SettingsPage />}
        </>
      )}
    </Shell>
  );
}
