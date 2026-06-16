from __future__ import annotations

import argparse
import email.utils
import html
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings, get_settings
from app.data.data_cleaner import normalize_symbol
from app.database import SessionLocal, init_db
from app.financial.financial_engine import analyze_financial
from app.models import FinancialData, MarketPrice, NewsSignal, OHLCVData, Opportunity, Stock, StockNews
from app.news.news_engine import analyze_news
from app.news.sentiment_engine import score_sentiment
from app.services.dynamic_settings import get_int, get_setting, seed_dynamic_settings
from app.services.market_data.base import ProviderUnavailable
from app.services.market_data.tradingview_websocket import TradingViewWebSocketProvider

logger = logging.getLogger(__name__)


FINANCIAL_COLUMNS = [
    "name",
    "description",
    "close",
    "total_revenue_ttm",
    "total_revenue_fy",
    "gross_profit_ttm",
    "gross_profit_fy",
    "net_income_ttm",
    "net_income_fy",
    "EBITDA_ttm",
    "EBITDA_fy",
    "earnings_per_share_basic_ttm",
    "earnings_per_share_diluted_ttm",
    "total_assets_fy",
    "total_liabilities_fy",
    "total_equity_fy",
    "total_debt_fy",
    "cash_f_operating_activities_ttm",
    "price_earnings_ttm",
    "price_book_fq",
    "market_cap_basic",
    "sector",
]


def _num(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and not (isinstance(value, float) and pd.isna(value)):
            return value
    return None


def _today_period(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc):%Y-%m-%d}"


def _to_naive_utc(value: Any) -> datetime | None:
    dt = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(dt):
        return None
    return dt.to_pydatetime().replace(tzinfo=None)


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", html.unescape(str(text)))
    return re.sub(r"\s+", " ", clean).strip()


def select_dynamic_symbols(db: Session, limit: int = 25, symbols: list[str] | None = None) -> list[str]:
    if symbols:
        return [normalize_symbol(symbol) for symbol in symbols if normalize_symbol(symbol)][:limit]
    chosen: list[str] = []
    for row in db.scalars(select(Opportunity).order_by(Opportunity.final_score.desc(), Opportunity.updated_at.desc()).limit(limit)).all():
        symbol = normalize_symbol(row.symbol)
        if symbol and symbol not in chosen:
            chosen.append(symbol)
    if len(chosen) < limit:
        for stock in db.scalars(select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.symbol)).all():
            symbol = normalize_symbol(stock.symbol)
            if symbol and symbol not in chosen:
                chosen.append(symbol)
            if len(chosen) >= limit:
                break
    return chosen[:limit]


def _scan_financial_rows(symbols: list[str], settings: Settings) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
        "columns": FINANCIAL_COLUMNS,
        "symbols": {"tickers": [f"EGX:{symbol}" for symbol in symbols], "query": {"types": []}},
        "range": [0, max(1, len(symbols))],
    }
    headers = {"User-Agent": "egx-intelligence/1.0", "Content-Type": "application/json"}
    verify = not settings.allow_insecure_market_data_tls
    try:
        response = httpx.post("https://scanner.tradingview.com/egypt/scan", json=payload, headers=headers, timeout=25, verify=verify)
    except Exception as exc:
        if verify and ("ssl" in str(exc).lower() or "certificate" in str(exc).lower()):
            response = httpx.post("https://scanner.tradingview.com/egypt/scan", json=payload, headers=headers, timeout=25, verify=False)
        else:
            raise
    response.raise_for_status()
    rows: list[dict[str, Any]] = []
    for item in response.json().get("data", []) or []:
        raw_symbol = str(item.get("s") or "")
        symbol = normalize_symbol(raw_symbol.split(":")[-1])
        values = item.get("d") or []
        row = dict(zip(FINANCIAL_COLUMNS, values, strict=False))
        row["symbol"] = symbol
        rows.append(row)
    return rows


def refresh_financial_from_tradingview(
    db: Session,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    settings = settings or get_settings()
    symbol_list = select_dynamic_symbols(db, limit=limit, symbols=symbols)
    if not symbol_list:
        return {"source": "tradingview_financials", "requested": 0, "inserted": 0, "updated": 0, "errors": ["No active symbols found."]}
    rows = _scan_financial_rows(symbol_list, settings)
    period = _today_period("TradingView_TTM")
    inserted = 0
    updated = 0
    analyzed = 0
    errors: list[str] = []
    for row in rows:
        symbol = normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        close = _num(row.get("close"))
        market_cap = _num(row.get("market_cap_basic"))
        shares = market_cap / close if market_cap and close else None
        payload = {
            "symbol": symbol,
            "period": period,
            "revenue": _num(_first(row, "total_revenue_ttm", "total_revenue_fy")),
            "gross_profit": _num(_first(row, "gross_profit_ttm", "gross_profit_fy")),
            "net_profit": _num(_first(row, "net_income_ttm", "net_income_fy")),
            "ebitda": _num(_first(row, "EBITDA_ttm", "EBITDA_fy")),
            "eps": _num(_first(row, "earnings_per_share_basic_ttm", "earnings_per_share_diluted_ttm")),
            "assets": _num(row.get("total_assets_fy")),
            "liabilities": _num(row.get("total_liabilities_fy")),
            "equity": _num(row.get("total_equity_fy")),
            "debt": _num(row.get("total_debt_fy")),
            "cash_flow": _num(row.get("cash_f_operating_activities_ttm")),
            "market_price": close,
            "shares_outstanding": shares,
            "raw_json": {
                "source": "tradingview_screener_financial_snapshot",
                "period": period,
                "pe_ratio": _num(row.get("price_earnings_ttm")),
                "pb_ratio": _num(row.get("price_book_fq")),
                "market_cap": market_cap,
                "sector": row.get("sector"),
                "raw": {key: (None if pd.isna(value) else value) for key, value in row.items()},
            },
        }
        existing = db.scalar(select(FinancialData).where(FinancialData.symbol == symbol, FinancialData.period == period))
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
            updated += 1
        else:
            db.add(FinancialData(**payload))
            inserted += 1
        try:
            analyze_financial(db, symbol, persist=True)
            analyzed += 1
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
    db.commit()
    return {
        "source": "tradingview_financials",
        "requested": len(symbol_list),
        "received": len(rows),
        "inserted": inserted,
        "updated": updated,
        "analyzed": analyzed,
        "period": period,
        "errors": errors[:10],
    }


def _stock_query(stock: Stock, template: str) -> str:
    name = stock.name_en or stock.name or stock.name_ar or stock.symbol
    return template.format(symbol=stock.symbol, name=name, sector=stock.sector or "")


def _rss_url(query: str, language: str = "en") -> str:
    if language == "ar":
        return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ar&gl=EG&ceid=EG:ar"
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=EG&ceid=EG:en"


def _parse_rss_items(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = _strip_html(item.findtext("title"))
        body = _strip_html(item.findtext("description"))
        link = item.findtext("link")
        pub_text = item.findtext("pubDate")
        source_node = item.find("source")
        published = None
        if pub_text:
            try:
                published = email.utils.parsedate_to_datetime(pub_text).astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                published = None
        items.append(
            {
                "title": title,
                "body": body,
                "link": link,
                "published_at": published,
                "source": source_node.text if source_node is not None else "Google News RSS",
            }
        )
    return items


def refresh_news_from_rss(
    db: Session,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    limit: int = 20,
    items_per_symbol: int = 5,
    language: str = "en",
) -> dict[str, Any]:
    settings = settings or get_settings()
    seed_dynamic_settings(db)
    template = str(get_setting(db, "news_rss_query_template", "{symbol} EGX OR {name} Egypt", "string"))
    symbol_list = select_dynamic_symbols(db, limit=limit, symbols=symbols)
    stocks = {
        stock.symbol: stock
        for stock in db.scalars(select(Stock).where(Stock.symbol.in_(symbol_list))).all()
    }
    inserted = 0
    duplicates = 0
    analyzed = 0
    errors: list[str] = []
    with httpx.Client(timeout=18, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 EGX Intelligence"}) as client:
        for symbol in symbol_list:
            stock = stocks.get(symbol) or Stock(symbol=symbol)
            query = _stock_query(stock, template)
            try:
                response = client.get(_rss_url(query, language=language))
                response.raise_for_status()
                items = _parse_rss_items(response.text)[:items_per_symbol]
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
                continue
            for item in items:
                if not item.get("title"):
                    continue
                exists = db.scalar(
                    select(func.count())
                    .select_from(StockNews)
                    .where(
                        StockNews.symbol == symbol,
                        StockNews.title == item["title"][:500],
                        StockNews.published_at == item.get("published_at"),
                    )
                )
                if exists:
                    duplicates += 1
                    continue
                sentiment = score_sentiment(" ".join([item.get("title") or "", item.get("body") or ""]))
                db.add(
                    StockNews(
                        symbol=symbol,
                        title=item["title"][:500],
                        body=item.get("body") or "",
                        source=item.get("source") or "Google News RSS",
                        published_at=item.get("published_at"),
                        sentiment=str(sentiment["sentiment"]),
                        sentiment_score=float(sentiment["sentiment_score"]),
                        impact_score=min(100.0, abs(float(sentiment["sentiment_score"])) + 20.0),
                        expected_impact_duration="short",
                        raw_json={"source": "google_news_rss", "query": query, "link": item.get("link")},
                    )
                )
                inserted += 1
            try:
                analyze_news(db, symbol, persist=True)
                analyzed += 1
            except Exception as exc:
                errors.append(f"{symbol}: analyze failed: {exc}")
    db.commit()
    return {
        "source": "google_news_rss",
        "requested": len(symbol_list),
        "inserted": inserted,
        "duplicates": duplicates,
        "analyzed": analyzed,
        "errors": errors[:10],
    }


def _normalize_timeframe(timeframe: str) -> str:
    value = str(timeframe or "1d").strip().lower()
    aliases = {"d": "1d", "1D": "1d", "60": "1h", "240": "4h", "15": "15m", "30": "30m"}
    return aliases.get(value, value)


def _upsert_market_price(db: Session, symbol: str, timeframe: str, row: dict[str, Any], provider: str) -> bool:
    timestamp = _to_naive_utc(row.get("datetime") or row.get("date") or row.get("timestamp"))
    if timestamp is None:
        return False
    existing = db.scalar(
        select(MarketPrice).where(
            MarketPrice.symbol == symbol,
            MarketPrice.timeframe == timeframe,
            MarketPrice.timestamp == timestamp,
            MarketPrice.provider == provider,
        )
    )
    values = {
        "open": _num(row.get("open")),
        "high": _num(row.get("high")),
        "low": _num(row.get("low")),
        "close": _num(row.get("close")),
        "volume": _num(row.get("volume")),
        "raw": {"source": provider, "timeframe": timeframe},
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    db.add(MarketPrice(symbol=symbol, timestamp=timestamp, timeframe=timeframe, provider=provider, **values))
    return True


def _upsert_daily_ohlcv(db: Session, symbol: str, row: dict[str, Any], provider: str) -> bool:
    timestamp = _to_naive_utc(row.get("datetime") or row.get("date") or row.get("timestamp"))
    if timestamp is None:
        return False
    existing = db.scalar(select(OHLCVData).where(OHLCVData.symbol == symbol, OHLCVData.datetime == timestamp))
    values = {
        "open": _num(row.get("open")),
        "high": _num(row.get("high")),
        "low": _num(row.get("low")),
        "close": _num(row.get("close")),
        "volume": _num(row.get("volume")),
        "provider": provider,
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return False
    db.add(OHLCVData(symbol=symbol, datetime=timestamp, **values))
    return True


def refresh_ohlcv_from_tradingview(
    db: Session,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    limit: int = 10,
    timeframes: list[str] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    symbol_list = select_dynamic_symbols(db, limit=limit, symbols=symbols)
    frames = [_normalize_timeframe(frame) for frame in (timeframes or ["1d", "1h", "4h"]) if str(frame).strip()]
    provider = TradingViewWebSocketProvider(settings)
    inserted = 0
    updated_or_existing = 0
    errors: list[str] = []
    for symbol in symbol_list:
        for timeframe in frames:
            try:
                if timeframe == "1d":
                    frame = provider.get_daily_ohlcv(symbol)
                else:
                    frame = provider.get_intraday_ohlcv(symbol, timeframe)
                frame = frame.tail(settings.strategy_backtest_bars).copy()
                for _, row in frame.iterrows():
                    row_dict = row.to_dict()
                    added = _upsert_market_price(db, symbol, timeframe, row_dict, provider.provider_name)
                    if timeframe == "1d":
                        _upsert_daily_ohlcv(db, symbol, row_dict, provider.provider_name)
                    inserted += 1 if added else 0
                    updated_or_existing += 0 if added else 1
                db.commit()
            except ProviderUnavailable as exc:
                errors.append(f"{symbol} {timeframe}: {exc}")
            except Exception as exc:
                errors.append(f"{symbol} {timeframe}: {exc}")
    return {
        "source": "tradingview_chart_ohlcv",
        "requested_symbols": len(symbol_list),
        "timeframes": frames,
        "inserted_market_rows": inserted,
        "updated_or_existing_rows": updated_or_existing,
        "errors": errors[:20],
    }


def run_dynamic_data_refresh(
    db: Session,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    limit: int | None = None,
    refresh_financial: bool = True,
    refresh_news: bool = True,
    refresh_ohlcv: bool = True,
    timeframes: list[str] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    seed_dynamic_settings(db)
    max_symbols = limit or get_int(db, "dynamic_data_symbol_limit", 5, minimum=1)
    results: dict[str, Any] = {}
    if refresh_financial:
        results["financial"] = refresh_financial_from_tradingview(db, settings=settings, symbols=symbols, limit=max_symbols)
    if refresh_news:
        items = get_int(db, "news_rss_max_items_per_symbol", 5, minimum=1)
        results["news"] = refresh_news_from_rss(db, settings=settings, symbols=symbols, limit=max_symbols, items_per_symbol=items)
    if refresh_ohlcv:
        configured = str(get_setting(db, "dynamic_data_timeframes", "1d,1h", "string") or "")
        selected_timeframes = timeframes or [part.strip() for part in configured.split(",") if part.strip()]
        results["ohlcv"] = refresh_ohlcv_from_tradingview(db, settings=settings, symbols=symbols, limit=max_symbols, timeframes=selected_timeframes)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh dynamic EGX data sources.")
    parser.add_argument("--symbol", action="append", help="Symbol to refresh. Can be passed more than once.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeframes", default=None, help="Comma-separated OHLCV timeframes, e.g. 1d,1h,4h,15m.")
    parser.add_argument("--financial", action="store_true")
    parser.add_argument("--news", action="store_true")
    parser.add_argument("--ohlcv", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    init_db(seed=True)
    selected = args.all or not (args.financial or args.news or args.ohlcv)
    timeframes = [part.strip() for part in args.timeframes.split(",")] if args.timeframes else None
    with SessionLocal() as db:
        result = run_dynamic_data_refresh(
            db,
            symbols=args.symbol,
            limit=args.limit,
            refresh_financial=selected or args.financial,
            refresh_news=selected or args.news,
            refresh_ohlcv=selected or args.ohlcv,
            timeframes=timeframes,
        )
    print(result)


if __name__ == "__main__":
    main()
