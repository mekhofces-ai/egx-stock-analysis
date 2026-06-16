from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.database import sqlite_write_lock
from app.models import TelegramMediaAnalysis, TelegramMessage

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
SKIP_EXTENSIONS = {".pdf", ".xls", ".xlsx", ".xlsm", ".m4a", ".mp3", ".mp4", ".doc", ".docx", ".zip"}


def is_supported_image(path_or_name: str | Path | None) -> bool:
    if not path_or_name:
        return False
    suffix = Path(str(path_or_name)).suffix.lower()
    return suffix in IMAGE_EXTENSIONS


def should_skip_media(path_or_name: str | Path | None) -> bool:
    if not path_or_name:
        return False
    return Path(str(path_or_name)).suffix.lower() in SKIP_EXTENSIONS


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def ocr_image(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        from PIL import Image
    except Exception as exc:
        return {"status": "ocr_not_available", "text": "", "error": f"Pillow unavailable: {exc}"}
    try:
        import pytesseract
    except Exception as exc:
        return {"status": "ocr_not_available", "text": "", "error": f"pytesseract unavailable: {exc}"}
    try:
        with Image.open(path) as image:
            text = pytesseract.image_to_string(image)
        return {"status": "ok", "text": text.strip(), "error": None}
    except Exception as exc:
        return {"status": "failed", "text": "", "error": str(exc)}


def analyze_image(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    metadata: dict[str, Any] = {
        "path": str(path),
        "ocr_status": "not_requested",
        "detected_text": "",
        "likely_candlestick_chart": False,
        "chart_likelihood_score": 0,
        "image_learning_status": "metadata_extracted",
        "vision_integration_status": "placeholder_for_future_ai_vision",
    }
    try:
        from PIL import Image, ImageFilter, ImageStat

        with Image.open(path) as image:
            image = image.convert("RGB")
            width, height = image.size
            metadata.update({"width": width, "height": height, "format": image.format})
            aspect_ratio = width / height if height else 0
            small = image.resize((min(width, 640), max(1, int(height * min(width, 640) / width)))) if width > 640 else image
            edges = small.convert("L").filter(ImageFilter.FIND_EDGES)
            edge_stat = ImageStat.Stat(edges)
            edge_density = float(edge_stat.mean[0]) / 255.0
            colors = small.getcolors(maxcolors=256_000) or []
            total_pixels = small.width * small.height
            non_white = sum(count for count, color in colors if sum(color) < 720)
            dark_pixels = sum(count for count, color in colors if sum(color) < 210)
            colored_pixels = sum(count for count, color in colors if max(color) - min(color) > 45)
            non_white_ratio = non_white / total_pixels if total_pixels else 0
            dark_ratio = dark_pixels / total_pixels if total_pixels else 0
            colored_ratio = colored_pixels / total_pixels if total_pixels else 0

            score = 0
            if width >= 450 and height >= 250:
                score += 20
            if 0.75 <= aspect_ratio <= 2.6:
                score += 18
            if edge_density >= 0.035:
                score += 20
            if 0.08 <= non_white_ratio <= 0.75:
                score += 15
            if colored_ratio >= 0.02:
                score += 12
            if dark_ratio >= 0.03:
                score += 10
            metadata.update(
                {
                    "edge_density": round(edge_density, 4),
                    "non_white_ratio": round(non_white_ratio, 4),
                    "dark_ratio": round(dark_ratio, 4),
                    "colored_ratio": round(colored_ratio, 4),
                    "chart_likelihood_score": min(100, score),
                }
            )
            metadata["likely_candlestick_chart"] = bool(score >= 55)
    except Exception as exc:
        metadata["error"] = str(exc)
        logger.info("Image analysis placeholder could not inspect %s: %s", path, exc)
    if _env_bool("TELEGRAM_ANALYZE_IMAGES", False):
        ocr = ocr_image(path)
        metadata["ocr_status"] = ocr.get("status")
        metadata["detected_text"] = ocr.get("text") or ""
        if ocr.get("error"):
            metadata["ocr_error"] = ocr.get("error")
    return metadata


def analyze_existing_images() -> list[dict[str, Any]]:
    settings = get_settings()
    image_root = Path(settings.image_download_dir)
    if not image_root.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in image_root.rglob("*"):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            continue
        results.append(analyze_image(path))
    return sorted(results, key=lambda item: item.get("chart_likelihood_score", 0), reverse=True)


def analyze_media_for_message(db, message: TelegramMessage) -> dict[str, Any]:  # noqa: ANN001
    media_path = message.media_path or message.image_path
    media_type = message.media_type or (Path(media_path).suffix.lower().lstrip(".") if media_path else None)
    if not media_path:
        payload = {
            "telegram_message_id": message.id,
            "media_path": None,
            "media_type": media_type,
            "ocr_text": "",
            "detected_symbols": "",
            "analysis_json": {},
            "status": "skipped_no_media",
            "error_message": None,
        }
    elif not is_supported_image(media_path):
        payload = {
            "telegram_message_id": message.id,
            "media_path": media_path,
            "media_type": media_type,
            "ocr_text": "",
            "detected_symbols": "",
            "analysis_json": {"reason": "unsupported_extension"},
            "status": "skipped_non_image",
            "error_message": None,
        }
    else:
        analysis = analyze_image(media_path)
        ocr_text = analysis.get("detected_text") or ""
        symbols: list[str] = []
        if ocr_text:
            try:
                from app.services.message_understanding import extract_symbols, store_message_symbols

                detected = extract_symbols(ocr_text, db)
                symbols = [item["symbol"] for item in detected]
                store_message_symbols(
                    db,
                    telegram_message_id=message.id,
                    symbols=detected,
                    intent="image_ocr",
                    source="telegram_image_ocr",
                    queue_reason="Telegram image OCR mention",
                )
            except Exception as exc:
                analysis["symbol_extraction_error"] = str(exc)
        payload = {
            "telegram_message_id": message.id,
            "media_path": media_path,
            "media_type": media_type,
            "ocr_text": ocr_text,
            "detected_symbols": ",".join(symbols),
            "analysis_json": analysis,
            "status": analysis.get("ocr_status") or "metadata_only",
            "error_message": analysis.get("ocr_error") or analysis.get("error"),
        }
    row = TelegramMediaAnalysis(**payload)
    with sqlite_write_lock():
        db.add(row)
        db.commit()
    return payload


def analyze_pending_media(db, limit: int = 100) -> dict[str, Any]:  # noqa: ANN001
    rows = db.query(TelegramMessage).filter(TelegramMessage.image_path.isnot(None)).order_by(TelegramMessage.created_at.desc()).limit(limit).all()
    processed = 0
    skipped = 0
    errors: list[str] = []
    for message in rows:
        try:
            existing = db.query(TelegramMediaAnalysis).filter(TelegramMediaAnalysis.telegram_message_id == message.id).first()
            if existing:
                skipped += 1
                continue
            analyze_media_for_message(db, message)
            processed += 1
        except Exception as exc:
            db.rollback()
            errors.append(f"{message.id}: {exc}")
    return {"processed": processed, "skipped": skipped, "errors": errors}


async def download_telegram_image(client: Any, message: Any, source_username: str) -> tuple[str | None, dict[str, Any] | None]:
    settings = get_settings()
    if not (_env_bool("TELEGRAM_DOWNLOAD_MEDIA", False) or _env_bool("TELEGRAM_ANALYZE_IMAGES", False)):
        return None, {"status": "media_download_disabled"}
    file_obj = getattr(message, "file", None)
    ext = (getattr(file_obj, "ext", "") or "").lower()
    name = getattr(file_obj, "name", "") or ""
    if not ext and "." in name:
        ext = "." + name.rsplit(".", 1)[-1].lower()
    if ext and ext in SKIP_EXTENSIONS:
        return None, {"status": "skipped_non_image", "extension": ext}
    if ext and ext not in IMAGE_EXTENSIONS:
        return None, {"status": "skipped_unsupported_media", "extension": ext}
    target_dir = Path(settings.image_download_dir) / source_username.strip("@")
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        downloaded = await client.download_media(message, file=str(target_dir))
    except Exception as exc:
        logger.warning("Failed to download media from %s message %s: %s", source_username, getattr(message, "id", None), exc)
        return None, {"download_error": str(exc)}
    if not downloaded:
        return None, None
    if not is_supported_image(downloaded):
        return None, {"status": "skipped_non_image_after_download", "path": str(downloaded)}
    metadata = analyze_image(downloaded)
    metadata["downloaded_at"] = datetime.utcnow().isoformat()
    return str(downloaded), metadata
