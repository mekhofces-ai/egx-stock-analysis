from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TelegramSource
from app.schemas import TelegramSourceCreate, TelegramSourceRead, TelegramSourceUpdate
from app.services.ingestion import run_ingestion_cycle
from app.services.source_importer import import_sources_from_df, read_sources_file


router = APIRouter(prefix="/sources", tags=["telegram sources"])


def _normalize(username: str) -> str:
    username = username.strip()
    return username if username.startswith("@") else f"@{username}"


@router.get("", response_model=list[TelegramSourceRead])
def list_sources(db: Session = Depends(get_db)) -> list[TelegramSource]:
    return db.scalars(select(TelegramSource).order_by(TelegramSource.username)).all()


@router.post("", response_model=TelegramSourceRead)
def create_source(
    payload: TelegramSourceCreate,
    fetch_now: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> TelegramSource:
    username = _normalize(payload.username)
    existing = db.scalar(select(TelegramSource).where(TelegramSource.username == username))
    if existing:
        raise HTTPException(status_code=409, detail="Source already exists")
    source = TelegramSource(**payload.model_dump(exclude={"username"}), username=username)
    db.add(source)
    db.commit()
    db.refresh(source)
    if fetch_now:
        run_ingestion_cycle()
    return source


@router.post("/import-file")
async def import_sources_file(
    file: UploadFile = File(...),
    fetch_now: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict:
    content = await file.read()
    df = read_sources_file(file.filename or "sources.csv", content)
    result = import_sources_from_df(db, df)
    ingestion = run_ingestion_cycle().to_dict() if fetch_now else None
    return {"import": result.to_dict(), "ingestion": ingestion}


@router.post("/refresh-now")
def refresh_sources_now() -> dict:
    return run_ingestion_cycle().to_dict()


@router.patch("/{source_id}", response_model=TelegramSourceRead)
def update_source(source_id: int, payload: TelegramSourceUpdate, db: Session = Depends(get_db)) -> TelegramSource:
    source = db.get(TelegramSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    updates = payload.model_dump(exclude_unset=True)
    if "username" in updates and updates["username"]:
        updates["username"] = _normalize(updates["username"])
    for key, value in updates.items():
        setattr(source, key, value)
    db.commit()
    db.refresh(source)
    return source


@router.delete("/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    source = db.get(TelegramSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    db.delete(source)
    db.commit()
    return {"status": "deleted"}


@router.post("/{source_id}/pause", response_model=TelegramSourceRead)
def pause_source(source_id: int, db: Session = Depends(get_db)) -> TelegramSource:
    source = db.get(TelegramSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    source.is_active = False
    db.commit()
    db.refresh(source)
    return source


@router.post("/{source_id}/activate", response_model=TelegramSourceRead)
def activate_source(source_id: int, db: Session = Depends(get_db)) -> TelegramSource:
    source = db.get(TelegramSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    source.is_active = True
    db.commit()
    db.refresh(source)
    return source
