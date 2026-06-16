from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Stock, TradingViewScreeningResult, TradingViewScreeningRun


def latest_market_heatmap_data(db: Session, *, limit: int = 500) -> dict[str, Any]:
    run = db.scalar(select(TradingViewScreeningRun).order_by(TradingViewScreeningRun.created_at.desc(), TradingViewScreeningRun.id.desc()))
    if not run:
        return {"run": None, "stocks": pd.DataFrame(), "sectors": pd.DataFrame(), "top_gainers": pd.DataFrame(), "top_losers": pd.DataFrame()}
    rows = db.scalars(
        select(TradingViewScreeningResult)
        .where(TradingViewScreeningResult.run_id == run.id)
        .order_by(TradingViewScreeningResult.change_percent.desc().nullslast())
        .limit(limit)
    ).all()
    sector_map = {row.symbol: row.sector or "Unknown" for row in db.scalars(select(Stock)).all()}
    name_map = {row.symbol: row.name or row.name_en or row.name_ar or row.symbol for row in db.scalars(select(Stock)).all()}
    data = pd.DataFrame(
        [
            {
                "symbol": row.symbol,
                "company_name": name_map.get(row.symbol, row.symbol),
                "sector": sector_map.get(row.symbol, "Unknown"),
                "change_percent": row.change_percent,
                "close": row.close,
                "volume": row.volume,
                "final_score": row.final_score,
                "recommendation": row.recommendation,
                "tv_vote": row.tv_vote,
                "technical_rating": row.technical_rating,
                "rsi": row.rsi,
            }
            for row in rows
        ]
    )
    if data.empty:
        return {"run": run, "stocks": data, "sectors": pd.DataFrame(), "top_gainers": pd.DataFrame(), "top_losers": pd.DataFrame()}

    numeric_cols = ["change_percent", "close", "volume", "final_score", "technical_rating", "rsi"]
    for col in numeric_cols:
        if col in data:
            data[col] = pd.to_numeric(data[col], errors="coerce")
    data["heatmap_size"] = data["volume"].fillna(0)
    fallback_size = data["change_percent"].abs().fillna(0) + 1
    data.loc[data["heatmap_size"] <= 0, "heatmap_size"] = fallback_size
    data["direction"] = data["change_percent"].apply(lambda value: "GAINER" if pd.notna(value) and value > 0 else "LOSER" if pd.notna(value) and value < 0 else "FLAT")

    sectors = (
        data.groupby("sector", dropna=False)
        .agg(
            symbols=("symbol", "count"),
            avg_change_percent=("change_percent", "mean"),
            median_change_percent=("change_percent", "median"),
            total_volume=("volume", "sum"),
            avg_score=("final_score", "mean"),
            gainers=("direction", lambda values: int((values == "GAINER").sum())),
            losers=("direction", lambda values: int((values == "LOSER").sum())),
        )
        .reset_index()
        .sort_values("avg_change_percent", ascending=False)
    )
    top_gainers = data.dropna(subset=["change_percent"]).sort_values("change_percent", ascending=False).head(15)
    top_losers = data.dropna(subset=["change_percent"]).sort_values("change_percent", ascending=True).head(15)
    return {"run": run, "stocks": data, "sectors": sectors, "top_gainers": top_gainers, "top_losers": top_losers}


def top_gainer_loser_summary(data: dict[str, Any]) -> dict[str, Any]:
    gainers = data.get("top_gainers")
    losers = data.get("top_losers")
    top_gainer = gainers.iloc[0].to_dict() if isinstance(gainers, pd.DataFrame) and not gainers.empty else None
    top_loser = losers.iloc[0].to_dict() if isinstance(losers, pd.DataFrame) and not losers.empty else None
    return {"top_gainer": top_gainer, "top_loser": top_loser}
