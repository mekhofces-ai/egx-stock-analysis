import { prisma } from "../db.js";
import { config } from "../config.js";

type EmbeddedEquity = {
  id?: number;
  reutersCode: string;
  name: string;
  ISN?: string | null;
  sector?: string | null;
  listingDate?: string | null;
};

export type EmbeddedUpdatedStock = {
  id?: number;
  currPrice: number;
  rateOfChange: number;
  percentageOfChange: number;
  open: number;
  prevClose: number;
  highest: number;
  lowest: number;
  volume: number;
  value: number;
  time: string;
  equity: EmbeddedEquity;
};

export type EmbeddedOhclv = {
  time: string;
  currPrice: number;
  open: number;
  highest: number;
  prevClose: number;
  lowest: number;
  volume: number;
};

const SOURCE = "egx-ai-api";
const MUBASHER_BASE_URL = "https://www.mubasher.info/markets/EGX/stocks/";
const FETCH_TIMEOUT_MS = 10000;
const MAX_CONCURRENCY = 6;
const memoryCache = new Map<string, { value: EmbeddedUpdatedStock; expiresAt: number }>();

type EmbeddedFetchOptions = {
  forceRefresh?: boolean;
};

function decodeHtml(value: string) {
  return value
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#x2F;/g, "/")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;/g, "'")
    .trim();
}

function stripTags(value: string) {
  return decodeHtml(value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " "));
}

function normalizeDigits(value: string) {
  const arabic = "٠١٢٣٤٥٦٧٨٩";
  const persian = "۰۱۲۳۴۵۶۷۸۹";
  return value.replace(/[٠-٩۰-۹]/g, (digit) => {
    const arabicIndex = arabic.indexOf(digit);
    if (arabicIndex >= 0) return String(arabicIndex);
    const persianIndex = persian.indexOf(digit);
    return persianIndex >= 0 ? String(persianIndex) : digit;
  });
}

function parseNumber(value: string | undefined) {
  if (!value) return undefined;
  const normalized = normalizeDigits(stripTags(value))
    .replace(/,/g, "")
    .replace(/%/g, "")
    .replace(/[^\d.+-]/g, "");
  const number = Number(normalized);
  return Number.isFinite(number) ? number : undefined;
}

function roundPrice(value: number) {
  return Number(value.toFixed(4));
}

function deriveCurrentPrice(rawPrice: number | undefined, prevClose: number | undefined, rateOfChange: number | undefined, percentageOfChange: number | undefined) {
  if (rawPrice !== undefined && rawPrice > 0) return rawPrice;
  if (prevClose === undefined || prevClose <= 0) return undefined;
  if (rateOfChange !== undefined && rateOfChange !== 0) return roundPrice(prevClose + rateOfChange);
  if (percentageOfChange !== undefined && percentageOfChange !== 0) return roundPrice(prevClose * (1 + percentageOfChange / 100));
  return prevClose;
}

function extractClassText(html: string, className: string) {
  const pattern = new RegExp(`<[^>]*class=["'][^"']*${className.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&")}[^"']*["'][^>]*>([\\s\\S]*?)<\\/[^>]+>`, "i");
  return stripTags(pattern.exec(html)?.[1] ?? "");
}

function extractClassNumbers(html: string, className: string) {
  const pattern = new RegExp(`<[^>]*class=["'][^"']*${className.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&")}[^"']*["'][^>]*>([\\s\\S]*?)<\\/[^>]+>`, "gi");
  const numbers: number[] = [];
  for (;;) {
    const match = pattern.exec(html);
    if (!match) break;
    const parsed = parseNumber(match[1]);
    if (parsed !== undefined) numbers.push(parsed);
  }
  return numbers;
}

function page<T>(content: T[], pageNumber: number, size: number, totalElements: number) {
  const totalPages = size > 0 ? Math.ceil(totalElements / size) : 1;
  return {
    content,
    pageable: {
      pageNumber,
      pageSize: size,
      offset: pageNumber * size,
      paged: true,
      unpaged: false,
    },
    totalElements,
    totalPages,
    number: pageNumber,
    size,
    numberOfElements: content.length,
    first: pageNumber === 0,
    last: pageNumber >= totalPages - 1,
    empty: content.length === 0,
  };
}

async function mapLimit<T, R>(items: T[], limit: number, worker: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = [];
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    for (;;) {
      const index = cursor;
      cursor += 1;
      if (index >= items.length) return;
      results[index] = await worker(items[index]);
    }
  });
  await Promise.all(workers);
  return results;
}

async function safePersist(work: () => Promise<unknown>) {
  try {
    await work();
  } catch {
    // Local snapshot storage must not block the public-compatible API response.
  }
}

function cairoNow() {
  return new Date();
}

function dayKey(date: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Africa/Cairo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function cacheTtlMs() {
  return Math.max(60_000, Math.min(config.CACHE_TTL_MS, 300_000));
}

export class EgxAiEmbeddedService {
  private async symbols(sectorFilter = "", nameFilter = "") {
    const symbols = await prisma.egxSymbol.findMany({ where: { isActive: true }, orderBy: { symbolCode: "asc" } });
    const sector = sectorFilter.trim().toLowerCase();
    const name = nameFilter.trim().toLowerCase();
    return symbols.filter((symbol) => {
      const sectorOk = !sector || (symbol.sector ?? "").toLowerCase().includes(sector);
      const nameOk = !name || symbol.companyNameEn.toLowerCase().includes(name) || symbol.symbolCode.toLowerCase().includes(name);
      return sectorOk && nameOk;
    });
  }

  private async fetchHtml(symbol: string) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    const endpoint = `${MUBASHER_BASE_URL}${encodeURIComponent(symbol)}`;
    try {
      const response = await fetch(endpoint, {
        headers: {
          "User-Agent": "EGX Smart Screener embedded EGX-AI compatible adapter",
          "Accept-Language": "ar,en;q=0.8",
        },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`Mubasher page returned HTTP ${response.status}`);
      return { endpoint, html: await response.text() };
    } finally {
      clearTimeout(timer);
    }
  }

  private parseStockPage(symbol: string, html: string, endpoint: string, fallbackName: string, fallbackSector?: string | null): EmbeddedUpdatedStock {
    const rawCurrPrice = parseNumber(extractClassText(html, "market-summary__last-price"));
    const rateOfChange = parseNumber(extractClassText(html, "market-summary__change number"));
    const percentageOfChange = parseNumber(extractClassText(html, "market-summary__change-percentage"));
    const marketSummary = extractClassNumbers(html, "market-summary__block-number");
    const titleText = stripTags(/<h1[^>]*class=["'][^"']*mi-section__title[^"']*["'][^>]*>([\s\S]*?)<\/h1>/i.exec(html)?.[1] ?? "");

    if (marketSummary.length < 6) {
      throw new Error(`Mubasher page for ${symbol} did not include a complete market-summary block`);
    }

    const [open, prevClose, highest, lowest, volume, value] = marketSummary;
    const currPrice = deriveCurrentPrice(rawCurrPrice, prevClose, rateOfChange, percentageOfChange);
    if (currPrice === undefined) {
      throw new Error(`Mubasher page for ${symbol} did not include a usable current price`);
    }
    const now = cairoNow();
    return {
      currPrice,
      rateOfChange: rateOfChange ?? 0,
      percentageOfChange: percentageOfChange ?? 0,
      open,
      prevClose,
      highest,
      lowest,
      volume,
      value,
      time: now.toISOString(),
      equity: {
        reutersCode: symbol,
        name: titleText || fallbackName,
        sector: fallbackSector,
      },
      id: undefined,
    };
  }

  private async persist(stock: EmbeddedUpdatedStock, rawEndpoint: string) {
    const symbol = stock.equity.reutersCode;
    const capturedAt = new Date(stock.time);
    const candleDate = new Date(`${dayKey(capturedAt)}T10:00:00+02:00`);
    await prisma.quoteSnapshot.create({
      data: {
        symbolCode: symbol,
        price: stock.currPrice,
        previousClose: stock.prevClose,
        changePercent: stock.percentageOfChange,
        volume: stock.volume,
        source: SOURCE,
        orderBookStatus: "unavailable",
        orderBookNote: "Embedded EGX-AI-compatible public page source does not expose true bid/ask or market depth.",
        rawPayload: { endpoint: rawEndpoint, stock },
        capturedAt,
      },
    });
    await prisma.candle.upsert({
      where: { symbolCode_timeframe_candleTime: { symbolCode: symbol, timeframe: "1D", candleTime: candleDate } },
      update: {
        open: stock.open,
        high: stock.highest,
        low: stock.lowest,
        close: stock.currPrice,
        volume: stock.volume,
        source: SOURCE,
        quality: "real",
        rawPayload: { endpoint: rawEndpoint, stock },
        importedAt: capturedAt,
      },
      create: {
        symbolCode: symbol,
        timeframe: "1D",
        candleTime: candleDate,
        open: stock.open,
        high: stock.highest,
        low: stock.lowest,
        close: stock.currPrice,
        volume: stock.volume,
        source: SOURCE,
        quality: "real",
        rawPayload: { endpoint: rawEndpoint, stock },
        importedAt: capturedAt,
      },
    });
  }

  async getStock(symbol: string, options: EmbeddedFetchOptions = {}): Promise<EmbeddedUpdatedStock> {
    const normalized = symbol.toUpperCase();
    const cached = memoryCache.get(normalized);
    if (!options.forceRefresh && cached && cached.expiresAt > Date.now()) return cached.value;

    const meta = await prisma.egxSymbol.findUnique({ where: { symbolCode: normalized } });
    if (!meta) throw new Error(`No equity with code: ${normalized}`);
    const { endpoint, html } = await this.fetchHtml(normalized);
    const stock = this.parseStockPage(normalized, html, endpoint, meta.companyNameEn, meta.sector);
    memoryCache.set(normalized, { value: stock, expiresAt: Date.now() + cacheTtlMs() });
    await safePersist(() => this.persist(stock, endpoint));
    return stock;
  }

  async getStocks(sectorFilter = "", nameFilter = "", pageNumber = 0, size = 10, options: EmbeddedFetchOptions = {}) {
    const symbols = await this.symbols(sectorFilter, nameFilter);
    const start = Math.max(0, pageNumber) * Math.max(1, size);
    const selected = symbols.slice(start, start + Math.max(1, size));
    const rows = await mapLimit(selected, MAX_CONCURRENCY, async (symbol) => {
      try {
        return await this.getStock(symbol.symbolCode, options);
      } catch (error) {
        await safePersist(() => prisma.rawDataSnapshot.create({
          data: {
            provider: SOURCE,
            endpoint: `${MUBASHER_BASE_URL}${symbol.symbolCode}`,
            symbolCode: symbol.symbolCode,
            status: "unavailable",
            error: error instanceof Error ? error.message : "Embedded EGX-AI fetch failed",
          },
        }));
        return null;
      }
    });
    return page(rows.filter((row): row is EmbeddedUpdatedStock => Boolean(row)), pageNumber, size, symbols.length);
  }

  async getHistorical(symbol: string, pageNumber = 0, size = 500, options: EmbeddedFetchOptions = {}) {
    const normalized = symbol.toUpperCase();
    try {
      await this.getStock(normalized, options);
    } catch {
      // Return any stored snapshots below if the live page is temporarily unreachable.
    }
    const rows = await prisma.candle.findMany({
      where: { symbolCode: normalized, timeframe: "1D", source: SOURCE },
      orderBy: { candleTime: "desc" },
      skip: Math.max(0, pageNumber) * Math.max(1, size),
      take: Math.max(1, size),
    });
    const total = await prisma.candle.count({ where: { symbolCode: normalized, timeframe: "1D", source: SOURCE } });
    const content: EmbeddedOhclv[] = rows
      .map((row) => ({
        time: row.candleTime.toISOString(),
        currPrice: row.close,
        open: row.open,
        highest: row.high,
        prevClose: row.rawPayload && typeof row.rawPayload === "object" && "stock" in row.rawPayload
          ? Number((row.rawPayload as { stock?: { prevClose?: number } }).stock?.prevClose ?? row.open)
          : row.open,
        lowest: row.low,
        volume: row.volume,
      }))
      .sort((a, b) => a.time.localeCompare(b.time));
    return page(content, pageNumber, size, total);
  }
}

export const egxAiEmbeddedService = new EgxAiEmbeddedService();
