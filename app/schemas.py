from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TelegramSourceCreate(BaseModel):
    username: str
    title: str | None = None
    source_type: str = "channel"
    is_active: bool = True
    trust_score: float = Field(default=50.0, ge=0, le=100)
    notes: str | None = None


class TelegramSourceUpdate(BaseModel):
    username: str | None = None
    title: str | None = None
    source_type: str | None = None
    is_active: bool | None = None
    trust_score: float | None = Field(default=None, ge=0, le=100)
    notes: str | None = None


class TelegramSourceRead(OrmModel):
    id: int
    username: str
    title: str | None
    source_type: str
    is_active: bool
    trust_score: float
    last_message_id: int
    notes: str | None
    created_at: datetime
    updated_at: datetime


class StockCreate(BaseModel):
    symbol: str
    name_ar: str | None = None
    name_en: str | None = None
    sector: str | None = None
    tradingview_symbol: str | None = None
    is_active: bool = True


class StockRead(OrmModel):
    id: int
    symbol: str
    name_ar: str | None
    name_en: str | None
    sector: str | None
    tradingview_symbol: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TelegramMessageRead(OrmModel):
    id: int
    source_id: int
    message_id: int
    message_date: datetime | None
    text: str
    image_path: str | None
    image_metadata: dict[str, Any] | None
    parsed: bool
    created_at: datetime


class ExtractedSignalRead(OrmModel):
    id: int
    source_id: int | None
    telegram_message_id: int | None
    stock_symbol: str | None
    direction: str | None
    entry_price: float | None
    targets: list[float] | None
    stop_loss: float | None
    support: float | None
    resistance: float | None
    timeframe: str | None
    hype_words: list[str] | None
    risk_flags: list[str] | None
    sentiment_score: float
    status: str
    created_at: datetime


class FinalAnalysisRead(OrmModel):
    id: int
    symbol: str
    final_decision: str
    confidence_score: float
    entry_zone: str | None
    stop_loss: float | None
    targets: list[float] | None
    reasons: list[str] | None
    warnings: list[str] | None
    invalidation_point: str | None
    position_size_suggestion: str | None
    last_price: float | None
    trend: str | None
    disclaimer: str
    created_at: datetime


class ManualAnalyzeRequest(BaseModel):
    symbol: str
    direction: str = "WATCH"
    entry_price: float | None = None
    stop_loss: float | None = None
    targets: list[float] = Field(default_factory=list)


class ScreenerFilter(BaseModel):
    name: str = "top_volume"
    limit: int = Field(default=25, ge=1, le=200)

