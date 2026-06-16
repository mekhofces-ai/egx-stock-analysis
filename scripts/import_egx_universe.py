from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, init_db
from app.models import Stock
from sqlalchemy import select


OBJECT_RE = re.compile(
    r'"symbol":\s*"(?P<symbol>[^"]+)".*?'
    r'"companyName":\s*"(?P<company>[^"]+)".*?'
    r'"sector":\s*"(?P<sector>[^"]+)"',
    re.DOTALL,
)


def main() -> None:
    universe_path = ROOT / "src" / "data" / "egxUniverse.ts"
    if not universe_path.exists():
        raise SystemExit(f"Missing {universe_path}")
    content = universe_path.read_text(encoding="utf-8")
    rows = list(OBJECT_RE.finditer(content))
    init_db(seed=True)
    inserted = 0
    updated = 0
    with SessionLocal() as db:
        for match in rows:
            symbol = match.group("symbol").upper()
            company = match.group("company")
            sector = match.group("sector")
            stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
            if stock:
                stock.name_en = stock.name_en or company
                stock.sector = stock.sector or sector
                stock.tradingview_symbol = stock.tradingview_symbol or f"EGX:{symbol}"
                stock.is_active = True
                updated += 1
            else:
                db.add(
                    Stock(
                        symbol=symbol,
                        name_en=company,
                        sector=sector,
                        tradingview_symbol=f"EGX:{symbol}",
                        is_active=True,
                    )
                )
                inserted += 1
        db.commit()
    print(f"Imported EGX universe: inserted={inserted}, updated={updated}, total_seen={len(rows)}")


if __name__ == "__main__":
    main()
