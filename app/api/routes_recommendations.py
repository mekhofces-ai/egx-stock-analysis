from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.screener_recommendations import build_final_recommendations


router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/screener")
def screener_recommendations(
    limit: int = Query(default=500, ge=1, le=800),
    db: Session = Depends(get_db),
) -> dict:
    run = build_final_recommendations(db, limit=limit)
    return {
        "provider": run.provider,
        "provider_status": run.provider_status,
        "provider_warning": run.provider_warning,
        "generated_at": run.generated_at.isoformat(),
        "rows": run.rows,
    }
