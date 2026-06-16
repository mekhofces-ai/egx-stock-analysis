import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";

const dbPath = join(process.cwd(), "prisma", "dev.db");
mkdirSync(dirname(dbPath), { recursive: true });

const db = new DatabaseSync(dbPath);
db.exec("PRAGMA foreign_keys = ON;");

db.exec(`
CREATE TABLE IF NOT EXISTS egx_symbols (
  symbol_code TEXT PRIMARY KEY NOT NULL,
  tradingview_symbol TEXT NOT NULL,
  company_name_en TEXT NOT NULL,
  company_name_ar TEXT,
  sector TEXT,
  industry TEXT,
  is_active BOOLEAN NOT NULL DEFAULT 1,
  is_placeholder BOOLEAN NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candles (
  id TEXT PRIMARY KEY NOT NULL,
  symbol_code TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  candle_time DATETIME NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  source TEXT NOT NULL,
  quality TEXT NOT NULL DEFAULT 'real',
  raw_payload JSONB,
  imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT candles_symbol_code_fkey FOREIGN KEY (symbol_code) REFERENCES egx_symbols (symbol_code) ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS candles_symbol_code_timeframe_candle_time_key ON candles(symbol_code, timeframe, candle_time);
CREATE INDEX IF NOT EXISTS candles_symbol_code_timeframe_candle_time_idx ON candles(symbol_code, timeframe, candle_time);

CREATE TABLE IF NOT EXISTS quote_snapshots (
  id TEXT PRIMARY KEY NOT NULL,
  symbol_code TEXT NOT NULL,
  price REAL NOT NULL,
  previous_close REAL,
  change_percent REAL,
  volume REAL,
  market_cap REAL,
  bid REAL,
  ask REAL,
  order_book_status TEXT NOT NULL DEFAULT 'unavailable',
  order_book_note TEXT,
  source TEXT NOT NULL,
  raw_payload JSONB,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT quote_snapshots_symbol_code_fkey FOREIGN KEY (symbol_code) REFERENCES egx_symbols (symbol_code) ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS quote_snapshots_symbol_code_captured_at_idx ON quote_snapshots(symbol_code, captured_at);

CREATE TABLE IF NOT EXISTS raw_data_snapshots (
  id TEXT PRIMARY KEY NOT NULL,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  symbol_code TEXT,
  status TEXT NOT NULL,
  payload JSONB,
  error TEXT,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS raw_data_snapshots_provider_captured_at_idx ON raw_data_snapshots(provider, captured_at);

CREATE TABLE IF NOT EXISTS watchlist (
  symbol_code TEXT PRIMARY KEY NOT NULL,
  notes TEXT,
  alert_enabled BOOLEAN NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT watchlist_symbol_code_fkey FOREIGN KEY (symbol_code) REFERENCES egx_symbols (symbol_code) ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS provider_status (
  provider TEXT PRIMARY KEY NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  checked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
`);

db.close();
console.log(`SQLite database ready at ${dbPath}`);
