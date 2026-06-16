from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services.image_analyzer import analyze_media_for_message, is_supported_image, ocr_image, should_skip_media


def analyze_image_file(path: str | Path) -> dict[str, Any]:
    if should_skip_media(path):
        return {"status": "skipped_non_image", "ocr_text": "", "reason": "unsupported extension"}
    if not is_supported_image(path):
        return {"status": "skipped_non_image", "ocr_text": "", "reason": "not an image"}
    result = ocr_image(path)
    return {"status": result.get("status"), "ocr_text": result.get("text") or "", "error": result.get("error")}


def analyze_message_image(db: Session, message: object) -> dict[str, Any]:
    return analyze_media_for_message(db, message)

