from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import ExtractedSignal, FinalAnalysis, TelegramMessage, TelegramSource
from app.schemas import ExtractedSignalRead, FinalAnalysisRead, ManualAnalyzeRequest, TelegramMessageRead
from app.services.analysis_runner import analyze_pending_signals, analyze_symbol_manually, format_alert


router = APIRouter(tags=["signals"])


@router.get("/messages")
def list_messages(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(TelegramMessage).order_by(TelegramMessage.created_at.desc()).limit(limit)).all()
    return [_with_channel(row, db) for row in rows]


@router.get("/signals")
def list_signals(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ExtractedSignal).order_by(ExtractedSignal.created_at.desc()).limit(limit)).all()
    return [_with_channel(row, db) for row in rows]


@router.get("/signals/latest")
def latest_signals(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ExtractedSignal).order_by(ExtractedSignal.created_at.desc()).limit(limit)).all()
    return [_with_channel(row, db) for row in rows]


@router.get("/analysis/latest")
def latest_analysis(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(FinalAnalysis).order_by(FinalAnalysis.created_at.desc()).limit(limit)).all()
    return [_with_channel(row, db) for row in rows]


@router.post("/signals/analyze-pending", response_model=list[FinalAnalysisRead])
def analyze_pending(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)) -> list[FinalAnalysis]:
    return analyze_pending_signals(db, limit=limit)


@router.post("/analyze", response_model=FinalAnalysisRead)
def analyze_manual(payload: ManualAnalyzeRequest, db: Session = Depends(get_db)) -> FinalAnalysis:
    return analyze_symbol_manually(
        db,
        symbol=payload.symbol,
        direction=payload.direction,
        entry_price=payload.entry_price,
        stop_loss=payload.stop_loss,
        targets=payload.targets,
    )


@router.get("/analyze/{symbol}", response_model=FinalAnalysisRead)
def analyze_symbol_endpoint(symbol: str, db: Session = Depends(get_db)) -> FinalAnalysis:
    try:
        return analyze_symbol_manually(db, symbol=symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/analysis/{analysis_id}/alert")
def alert_text(analysis_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    final = db.scalar(
        select(FinalAnalysis)
        .options(joinedload(FinalAnalysis.extracted_signal), joinedload(FinalAnalysis.technical_analysis))
        .where(FinalAnalysis.id == analysis_id)
    )
    if not final:
        raise HTTPException(status_code=404, detail="Analysis not found")
    source = final.extracted_signal.source.username if final.extracted_signal and final.extracted_signal.source else None
    return {"text": format_alert(final, final.extracted_signal, source_username=source)}


def _model_to_dict(row) -> dict:
    return {key: value for key, value in vars(row).items() if not key.startswith("_")}


def _with_channel(row, db: Session) -> dict:
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
