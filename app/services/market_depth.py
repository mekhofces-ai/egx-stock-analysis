from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from app.config import DISCLAIMER, Settings, get_settings


REQUIRED_DEPTH_COLUMNS = {"symbol", "side", "price", "quantity"}


def _read_depth_files(settings: Settings) -> pd.DataFrame:
    data_dir = Path(settings.market_depth_data_dir)
    frames: list[pd.DataFrame] = []
    if data_dir.exists():
        for path in sorted(data_dir.glob("*.csv")):
            frame = pd.read_csv(path)
            frame["source_file"] = path.name
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def normalize_market_depth(df: pd.DataFrame, default_source: str = "csv") -> pd.DataFrame:
    if df.empty:
        return df
    frame = df.copy()
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    rename_map = {
        "ticker": "symbol",
        "stock": "symbol",
        "bid_ask": "side",
        "qty": "quantity",
        "shares": "quantity",
        "orders": "num_orders",
        "order_count": "num_orders",
        "time": "timestamp",
        "date": "timestamp",
    }
    frame = frame.rename(columns=rename_map)
    missing = REQUIRED_DEPTH_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Market-depth CSV missing columns: {sorted(missing)}")
    frame["symbol"] = frame["symbol"].astype(str).str.upper().str.replace("EGX:", "", regex=False).str.strip()
    frame["side"] = frame["side"].astype(str).str.lower().str.strip()
    frame["side"] = frame["side"].replace({"buy": "bid", "bids": "bid", "sell": "ask", "asks": "ask", "offer": "ask", "offers": "ask"})
    frame = frame[frame["side"].isin({"bid", "ask"})]
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")
    if "num_orders" in frame.columns:
        frame["num_orders"] = pd.to_numeric(frame["num_orders"], errors="coerce").fillna(0)
    else:
        frame["num_orders"] = 0
    if "level" in frame.columns:
        frame["level"] = pd.to_numeric(frame["level"], errors="coerce")
    else:
        frame["level"] = None
    if "timestamp" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    else:
        frame["timestamp"] = pd.Timestamp.utcnow()
    if "source" not in frame.columns:
        frame["source"] = default_source
    frame["notional"] = frame["price"] * frame["quantity"]
    frame = frame.dropna(subset=["symbol", "side", "price", "quantity"])
    return frame.reset_index(drop=True)


def load_market_depth(settings: Settings | None = None) -> pd.DataFrame:
    settings = settings or get_settings()
    return normalize_market_depth(_read_depth_files(settings))


def build_market_depth_screener(settings: Settings | None = None, limit: int = 100) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        depth = load_market_depth(settings)
    except Exception as exc:
        return {"status": "error", "warning": str(exc), "rows": [], "disclaimer": DISCLAIMER}
    if depth.empty:
        return {
            "status": "empty",
            "warning": f"No market-depth CSV files found in {settings.market_depth_data_dir}.",
            "rows": [],
            "disclaimer": DISCLAIMER,
        }

    rows: list[dict[str, Any]] = []
    for symbol, group in depth.groupby("symbol"):
        bids = group[group["side"] == "bid"].sort_values("price", ascending=False)
        asks = group[group["side"] == "ask"].sort_values("price", ascending=True)
        if bids.empty or asks.empty:
            continue
        top_bids = bids.head(5)
        top_asks = asks.head(5)
        best_bid = float(top_bids.iloc[0]["price"])
        best_ask = float(top_asks.iloc[0]["price"])
        bid_value = float(top_bids["notional"].sum())
        ask_value = float(top_asks["notional"].sum())
        bid_qty = float(top_bids["quantity"].sum())
        ask_qty = float(top_asks["quantity"].sum())
        spread = best_ask - best_bid
        mid = (best_ask + best_bid) / 2 if best_ask and best_bid else None
        spread_pct = spread / mid * 100 if mid else None
        pressure_ratio = bid_value / ask_value if ask_value else None
        if pressure_ratio is not None and pressure_ratio >= 1.5 and (spread_pct or 0) <= 2.5:
            depth_signal = "BID_SUPPORT"
        elif pressure_ratio is not None and pressure_ratio <= 0.67:
            depth_signal = "ASK_SUPPLY"
        elif spread_pct is not None and spread_pct > 3:
            depth_signal = "WIDE_SPREAD"
        else:
            depth_signal = "BALANCED"
        rows.append(
            {
                "symbol": symbol,
                "best_bid": round(best_bid, 4),
                "best_ask": round(best_ask, 4),
                "spread": round(spread, 4),
                "spread_pct": round(float(spread_pct), 2) if spread_pct is not None else None,
                "top5_bid_quantity": round(bid_qty, 2),
                "top5_ask_quantity": round(ask_qty, 2),
                "top5_bid_value": round(bid_value, 2),
                "top5_ask_value": round(ask_value, 2),
                "bid_ask_pressure_ratio": round(float(pressure_ratio), 2) if pressure_ratio is not None else None,
                "depth_signal": depth_signal,
                "latest_timestamp": group["timestamp"].max().isoformat() if pd.notna(group["timestamp"].max()) else None,
                "sources": ", ".join(sorted(set(group["source"].astype(str).tolist()))),
            }
        )

    rows.sort(
        key=lambda row: (
            row["depth_signal"] == "BID_SUPPORT",
            row["bid_ask_pressure_ratio"] or 0,
            -(row["spread_pct"] or 999),
        ),
        reverse=True,
    )
    return {"status": "available", "warning": None, "rows": rows[:limit], "disclaimer": DISCLAIMER}


def build_market_depth_report(settings: Settings | None = None, limit: int = 10) -> str:
    data = build_market_depth_screener(settings=settings, limit=limit)
    lines = ["EGX Bid/Ask Depth Screener"]
    if data["status"] != "available":
        lines.extend([data.get("warning") or "No market depth available.", "", f"Disclaimer: {DISCLAIMER}"])
        return "\n".join(lines)
    rows = data.get("rows", [])
    if not rows:
        lines.extend(["No complete bid/ask symbols found.", "", f"Disclaimer: {DISCLAIMER}"])
        return "\n".join(lines)
    for idx, row in enumerate(rows[:limit], start=1):
        lines.append(
            f"{idx}. {row['symbol']} | {row['depth_signal']} | bid {row['best_bid']} / ask {row['best_ask']} | "
            f"spread {row['spread_pct']}% | pressure {row['bid_ask_pressure_ratio']}"
        )
    lines.extend(["", "Uses official/exported depth CSV snapshots only.", f"Disclaimer: {DISCLAIMER}"])
    return "\n".join(lines)[:3900]
