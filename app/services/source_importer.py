from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TelegramSource


USERNAME_COLUMNS = ["username", "channel", "source", "telegram", "telegram_source", "link", "url"]
TITLE_COLUMNS = ["title", "name", "channel_name"]
TYPE_COLUMNS = ["source_type", "type"]
TRUST_COLUMNS = ["trust_score", "trust", "score"]
ACTIVE_COLUMNS = ["is_active", "active", "enabled"]
NOTES_COLUMNS = ["notes", "note", "description"]


@dataclass
class SourceImportResult:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    usernames: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "usernames": self.usernames or [],
        }


def _first_value(row: pd.Series, candidates: list[str]) -> Any:
    for name in candidates:
        if name in row and pd.notna(row[name]):
            value = row[name]
            if str(value).strip():
                return value
    return None


def normalize_username(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(?:https?://)?(?:www\.)?t\.me/([A-Za-z0-9_]+)", text)
    if match:
        text = match.group(1)
    text = text.strip().split()[0].strip("/")
    if not text:
        return None
    if text.startswith("@"):
        return text
    if re.fullmatch(r"[A-Za-z0-9_]{4,}", text):
        return f"@{text}"
    return None


def _bool_value(value: Any, default: bool = True) -> bool:
    if value is None or pd.isna(value):
        return default
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "active", "enabled"}


def read_sources_file(filename: str, content: bytes) -> pd.DataFrame:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else "csv"
    if suffix in {"xlsx", "xls"}:
        df = pd.read_excel(io.BytesIO(content))
    else:
        df = pd.read_csv(io.BytesIO(content))
    df.columns = [str(col).strip().lower() for col in df.columns]
    return df


def import_sources_from_df(db: Session, df: pd.DataFrame, default_trust: float = 50.0) -> SourceImportResult:
    result = SourceImportResult(usernames=[])
    for _, row in df.iterrows():
        username = normalize_username(_first_value(row, USERNAME_COLUMNS))
        if not username:
            result.skipped += 1
            continue
        title = _first_value(row, TITLE_COLUMNS) or username
        source_type = str(_first_value(row, TYPE_COLUMNS) or "channel").strip().lower()
        trust_raw = _first_value(row, TRUST_COLUMNS)
        try:
            trust_score = float(trust_raw) if trust_raw is not None else default_trust
        except (TypeError, ValueError):
            trust_score = default_trust
        trust_score = max(0.0, min(100.0, trust_score))
        is_active = _bool_value(_first_value(row, ACTIVE_COLUMNS), default=True)
        notes = _first_value(row, NOTES_COLUMNS)

        source = db.scalar(select(TelegramSource).where(TelegramSource.username == username))
        if source:
            source.title = str(title)
            source.source_type = source_type or source.source_type
            source.trust_score = trust_score
            source.is_active = is_active
            if notes is not None:
                source.notes = str(notes)
            result.updated += 1
        else:
            db.add(
                TelegramSource(
                    username=username,
                    title=str(title),
                    source_type=source_type or "channel",
                    trust_score=trust_score,
                    is_active=is_active,
                    notes=str(notes) if notes is not None else None,
                )
            )
            result.inserted += 1
        result.usernames.append(username)
    db.commit()
    return result

