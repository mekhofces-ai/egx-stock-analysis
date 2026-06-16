from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.config import get_settings
from app.services.market_depth import build_market_depth_screener


router = APIRouter(prefix="/market-depth", tags=["market-depth"])


@router.get("/screener")
def market_depth_screener(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    return build_market_depth_screener(settings=get_settings(), limit=limit)
