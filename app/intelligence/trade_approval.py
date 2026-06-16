from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TradeApproval


def create_trade_approval(db: Session, proposal: dict[str, Any], requested_by: str = "system") -> TradeApproval:
    existing = db.scalar(
        select(TradeApproval).where(
            TradeApproval.symbol == proposal.get("symbol"),
            TradeApproval.side == proposal.get("side", "BUY"),
            TradeApproval.status == "pending",
        )
    )
    row = existing or TradeApproval(symbol=proposal["symbol"], side=proposal.get("side", "BUY"))
    row.proposed_price = proposal.get("price") or proposal.get("entry_price")
    row.quantity = proposal.get("quantity")
    row.total_value = proposal.get("total_value") or proposal.get("total_cost")
    row.final_score = proposal.get("final_score")
    row.signal = proposal.get("signal") or proposal.get("final_signal")
    row.reason = proposal.get("reason")
    row.status = "pending"
    row.requested_by = requested_by
    db.add(row)
    return row


def set_trade_approval_status(db: Session, approval_id: int, status: str, approved_by: str = "dashboard") -> TradeApproval | None:
    row = db.get(TradeApproval, approval_id)
    if not row:
        return None
    row.status = status
    row.approved_by = approved_by
    db.add(row)
    return row

