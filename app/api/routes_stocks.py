from __future__ import annotations

import io
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import ExtractedSignal, FinalAnalysis, Stock, TelegramMessage, TelegramSource
from app.schemas import StockCreate, StockRead
from app.services.market_data.base import build_provider_chain
from app.services.screener_recommendations import build_final_recommendations


router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("", response_model=list[StockRead])
def list_stocks(db: Session = Depends(get_db)) -> list[Stock]:
    return db.scalars(select(Stock).order_by(Stock.symbol)).all()


@router.post("", response_model=StockRead)
def create_stock(payload: StockCreate, db: Session = Depends(get_db)) -> Stock:
    symbol = payload.symbol.upper()
    if db.scalar(select(Stock).where(Stock.symbol == symbol)):
        raise HTTPException(status_code=409, detail="Stock already exists")
    stock = Stock(**payload.model_dump(exclude={"symbol"}), symbol=symbol)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


@router.post("/import-csv")
async def import_stocks_csv(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    content = await file.read()
    df = pd.read_csv(io.BytesIO(content))
    df.columns = [str(col).strip().lower() for col in df.columns]
    if "symbol" not in df.columns:
        raise HTTPException(status_code=400, detail="CSV must include a symbol column")
    inserted = 0
    updated = 0
    for _, row in df.iterrows():
        symbol = str(row["symbol"]).upper().strip()
        if not symbol:
            continue
        stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
        data = {
            "name_ar": row.get("name_ar"),
            "name_en": row.get("name_en"),
            "sector": row.get("sector"),
            "tradingview_symbol": row.get("tradingview_symbol") or f"EGX:{symbol}",
            "is_active": bool(row.get("is_active", True)),
        }
        if stock:
            for key, value in data.items():
                if pd.notna(value):
                    setattr(stock, key, value)
            updated += 1
        else:
            db.add(Stock(symbol=symbol, **data))
            inserted += 1
    db.commit()
    return {"inserted": inserted, "updated": updated}


@router.get("/screener")
def stock_screener(
    filter_name: str = Query(default="top_volume"),
    limit: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    provider_chain = build_provider_chain(get_settings())
    df = provider_chain.screen_stocks({"limit": max(limit, 100)})
    df = _apply_screener_filter(df, filter_name, db)
    return df.head(limit).where(pd.notna(df), None).to_dict(orient="records")


@router.get("/{symbol}/detail")
def stock_detail(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    normalized = symbol.upper()
    stock = db.scalar(select(Stock).where(Stock.symbol == normalized))
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")
    run = build_final_recommendations(db, limit=500)
    recommendation = next((row for row in run.rows if row["symbol"] == normalized), None)
    signals = db.scalars(
        select(ExtractedSignal)
        .where(ExtractedSignal.stock_symbol == normalized)
        .order_by(ExtractedSignal.created_at.desc())
        .limit(25)
    ).all()
    analyses = db.scalars(
        select(FinalAnalysis)
        .where(FinalAnalysis.symbol == normalized)
        .order_by(FinalAnalysis.created_at.desc())
        .limit(10)
    ).all()
    message_ids = [signal.telegram_message_id for signal in signals if signal.telegram_message_id]
    images: list[TelegramMessage] = []
    if message_ids:
        images = db.scalars(
            select(TelegramMessage)
            .where(TelegramMessage.id.in_(message_ids))
            .where(TelegramMessage.image_path.is_not(None))
            .order_by(TelegramMessage.created_at.desc())
            .limit(10)
        ).all()
    return {
        "stock": _model_to_dict(stock),
        "recommendation": recommendation,
        "recent_signals": [_with_channel(row, db) for row in signals],
        "recent_analyses": [_with_channel(row, db) for row in analyses],
        "recent_images": [_with_channel(row, db) for row in images],
        "provider_status": run.provider_status,
        "provider_warning": run.provider_warning,
    }


def _apply_screener_filter(df: pd.DataFrame, filter_name: str, db: Session) -> pd.DataFrame:
    if df.empty:
        return df
    frame = df.copy()
    for col in ["volume", "change_percent", "RSI", "Recommend.All"]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    name = filter_name.lower()
    if name == "strong_technical_buy" and "Recommend.All" in frame.columns:
        return frame[frame["Recommend.All"].fillna(0) >= 0.3].sort_values("Recommend.All", ascending=False)
    if name == "rsi_oversold" and "RSI" in frame.columns:
        return frame[frame["RSI"].fillna(100) <= 30].sort_values("RSI")
    if name == "rsi_overbought" and "RSI" in frame.columns:
        return frame[frame["RSI"].fillna(0) >= 70].sort_values("RSI", ascending=False)
    if name in {"breakout_candidates", "unusual_volume"}:
        if "change_percent" in frame.columns and "volume" in frame.columns:
            return frame[frame["change_percent"].fillna(0) > 1.5].sort_values(["volume", "change_percent"], ascending=False)
    if name in {"near_support", "near_resistance"}:
        return frame.sort_values("change_percent", ascending=(name == "near_support")) if "change_percent" in frame.columns else frame
    if name == "telegram_hype_technical_confirmation":
        hype_symbols = db.scalars(select(ExtractedSignal.stock_symbol).where(ExtractedSignal.hype_words.is_not(None))).all()
        if hype_symbols:
            return frame[frame["symbol"].astype(str).str.upper().isin({symbol for symbol in hype_symbols if symbol})]
    if "volume" in frame.columns:
        return frame.sort_values("volume", ascending=False)
    return frame


def _model_to_dict(row: Any) -> dict[str, Any]:
    return {key: value for key, value in vars(row).items() if not key.startswith("_")}


def _with_channel(row: Any, db: Session) -> dict[str, Any]:
    data = _model_to_dict(row)
    source_id = data.pop("source_id", None)
    if source_id:
        source = db.get(TelegramSource, int(source_id))
        data["channel"] = (source.title or source.username) if source else None
        data["channel_username"] = source.username if source else None
    else:
        data["channel"] = None
        data["channel_username"] = None
    return data
