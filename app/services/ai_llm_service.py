from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import AiStockOpinion, OHLCVData, Stock, StockCombinedAnalysis
from app.services.ai_analysis_service import get_ai_analysis_for_symbol
from app.services.dynamic_settings import get_setting

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a decisive EGX (Egyptian Exchange) stock analyst. Analyze the provided data and return ONLY valid JSON with no markdown or code blocks.

SCORING RUBRIC:
>=80 → STRONG BUY (exceptional)
65-79 → BUY (bullish)
50-64 → WATCH (mixed)
35-49 → NEUTRAL (no edge)
20-34 → AVOID (bearish)
<20  → SELL (strong sell)

CRITICAL: The data below contains system scores but you MUST make your own independent judgment. Different stocks MUST get different signals. Do not default to WATCH.

Return exactly this JSON (use null, never the string "null"):
{
  "ai_score": <0-100>,
  "ai_signal": "STRONG BUY|BUY|WATCH|NEUTRAL|AVOID|SELL",
  "ai_opinion": "2-3 sentence summary",
  "ai_reasoning": "detailed analysis",
  "ai_key_drivers": ["driver 1", "driver 2"],
  "ai_risks": ["risk 1", "risk 2"],
  "ai_catalyst": "main catalyst or null",
  "ai_entry_zone": "entry price range or null",
  "ai_stop_loss": <float or null>,
  "ai_target_1": <float or null>,
  "ai_target_2": <float or null>,
  "ai_time_horizon": "short_term|medium_term|long_term",
  "ai_confidence": "low|medium|high"
}"""


def _build_user_prompt(symbol: str, analysis: dict[str, Any]) -> str:
    parts = [f"Stock: {symbol}"]
    if analysis.get("name"): parts.append(f"Name: {analysis['name']}")
    if analysis.get("sector"): parts.append(f"Sector: {analysis['sector']}")
    if analysis.get("last_price"): parts.append(f"Last Price: {analysis['last_price']}")

    # Combined analysis score is the PRIMARY AI score (TradingView-derived)
    ca = analysis.get("combined_analysis", {})
    if ca and ca.get("score") is not None:
        parts.append(f"[CombinedAnalysis] Score: {ca['score']} | Rec: {ca.get('recommendation','N/A')} | Conf: {ca.get('confidence','N/A')}")
    else:
        parts.append(f"[SystemScore] Score: {analysis.get('ai_score','N/A')} | Signal: {analysis.get('ai_signal','N/A')}")

    labels = {
        "Technical": ["score","signal","trend","risk_level"],
        "Financial": ["score","signal","profitability_score","growth_score","valuation_score"],
        "News": ["score","signal","main_drivers"],
        "Telegram": ["score","signal","top_channels"],
        "Strategy": ["score","signal","strategy_name"],
    }
    for label, keys in labels.items():
        data = analysis.get(label.lower(), {})
        if data:
            vals = [f"{k}: {data[k]}" for k in keys if data.get(k) is not None]
            if vals:
                parts.append(f"[{label}] {' | '.join(vals)}")

    fd = analysis.get("final_decision", {})
    if fd:
        items = [f"{k}: {fd[k]}" for k in ("signal","score","risk_level","market_regime","entry_price","stop_loss") if fd.get(k) is not None]
        if items: parts.append(f"[Final Decision] {' | '.join(items)}")

    opp = analysis.get("opportunity", {})
    if opp and opp.get("score") is not None:
        parts.append(f"[Opportunity] Score: {opp['score']} | Rec: {opp.get('recommendation','N/A')}")

    parts.append("")
    parts.append("IMPORTANT: The system scores above are generic. Use the [CombinedAnalysis] score as your primary reference. Make your OWN independent judgment. Do NOT default to WATCH. Return your analysis as JSON.")
    return "\n".join(parts)


def _has_placeholders(data: dict) -> bool:
    """Check if the model copied placeholder text from the prompt."""
    placeholder_patterns = ("<", ">", "price or null", "sentence summary", "driver")
    for v in data.values():
        if isinstance(v, str) and any(p in v.lower() for p in placeholder_patterns):
            return True
    return False


def _clean_parsed(data: dict) -> dict:
    """Post-process parsed JSON: fix null strings, clean up values."""
    cleaned = {}
    for k, v in data.items():
        if isinstance(v, str) and v.strip().lower() in ("null", "none", ""):
            cleaned[k] = None
        elif isinstance(v, str) and k in ("ai_stop_loss", "ai_target_1", "ai_target_2", "ai_score"):
            try:
                cleaned[k] = float(v)
            except (ValueError, TypeError):
                cleaned[k] = None
        elif isinstance(v, list):
            cleaned[k] = [item for item in v if not (isinstance(item, str) and item.strip().lower() in ("null", "none", ""))]
        else:
            cleaned[k] = v
    return cleaned


def _parse_opinion_response(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        for fence in ("```json", "```JSON", "```"):
            if text.startswith(fence):
                text = text[len(fence):]
                if text.endswith("```"):
                    text = text[:-3]
                break
    text = text.strip()
    try:
        parsed = json.loads(text)
        return _clean_parsed(parsed)
    except json.JSONDecodeError:
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                return _clean_parsed(parsed)
            except json.JSONDecodeError:
                pass
        logger.warning("Failed to parse AI response as JSON: %.200s", raw)
        return None


def _default_opinion(symbol: str, error: str | None = None) -> dict[str, Any]:
    return {
        "ai_score": None,
        "ai_signal": "NEUTRAL",
        "ai_opinion": f"AI analysis unavailable for {symbol}.",
        "ai_reasoning": error or "Analysis could not be generated.",
        "ai_key_drivers": [],
        "ai_risks": [],
        "ai_catalyst": None,
        "ai_entry_zone": None,
        "ai_stop_loss": None,
        "ai_target_1": None,
        "ai_target_2": None,
        "ai_time_horizon": None,
        "ai_confidence": None,
    }


def generate_opinion(symbol: str, run_id: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.enable_ai_analysis or not settings.openai_api_key:
        return _default_opinion(symbol, "AI analysis is disabled or API key not configured.")

    run_id = run_id or uuid.uuid4().hex[:12]
    try:
        analysis = get_ai_analysis_for_symbol(symbol)
        prompt = _build_user_prompt(symbol, analysis)
    except Exception as exc:
        logger.error("Failed to prepare analysis data for %s: %s", symbol, exc)
        return _default_opinion(symbol, f"Data preparation error: {exc}")

    start = time.time()
    try:
        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.ai_base_url, timeout=settings.ai_timeout_seconds)
        parsed = None
        tokens = None

        for attempt in range(2):
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            if attempt == 1:
                messages.append({"role": "user", "content": "IMPORTANT: Do NOT use placeholder text like <...> or 'price or null'. Use actual numbers. Be decisive — use a real signal from STRONG BUY, BUY, WATCH, NEUTRAL, AVOID, or SELL. Return ONLY valid JSON."})

            response = client.chat.completions.create(
                model=settings.ai_model,
                messages=messages,
                temperature=0.3 if attempt == 0 else 0.5,
                max_tokens=800,
            )
            raw = response.choices[0].message.content or ""
            parsed = _parse_opinion_response(raw)
            if response.usage:
                tokens = response.usage.total_tokens
            if parsed and not _has_placeholders(parsed):
                break

        latency = int((time.time() - start) * 1000)
        if not parsed:
            return _default_opinion(symbol, "AI returned unparseable response after retry.")

        result = {
            "symbol": symbol,
            "ai_score": parsed.get("ai_score"),
            "ai_signal": parsed.get("ai_signal", "NEUTRAL"),
            "ai_opinion": parsed.get("ai_opinion", ""),
            "ai_reasoning": parsed.get("ai_reasoning", ""),
            "ai_key_drivers": parsed.get("ai_key_drivers", []),
            "ai_risks": parsed.get("ai_risks", []),
            "ai_catalyst": parsed.get("ai_catalyst"),
            "ai_entry_zone": parsed.get("ai_entry_zone"),
            "ai_stop_loss": parsed.get("ai_stop_loss"),
            "ai_target_1": parsed.get("ai_target_1"),
            "ai_target_2": parsed.get("ai_target_2"),
            "ai_time_horizon": parsed.get("ai_time_horizon"),
            "ai_confidence": parsed.get("ai_confidence"),
            "model_used": settings.ai_model,
            "tokens_used": tokens,
            "latency_ms": latency,
        }
        _persist_opinion(symbol, run_id, result)
        return result
    except Exception as exc:
        logger.error("OpenAI API call failed for %s: %s", symbol, exc)
        return _default_opinion(symbol, str(exc))


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _persist_opinion(symbol: str, run_id: str, data: dict[str, Any]) -> None:
    try:
        with SessionLocal() as db:
            existing = db.scalar(
                select(AiStockOpinion)
                .where(AiStockOpinion.symbol == symbol, AiStockOpinion.run_id == run_id)
            )
            if existing:
                return
            opinion = AiStockOpinion(
                symbol=symbol,
                run_id=run_id,
                ai_score=_to_float_or_none(data.get("ai_score")),
                ai_signal=data.get("ai_signal"),
                ai_opinion=data.get("ai_opinion"),
                ai_reasoning=data.get("ai_reasoning"),
                ai_key_drivers=data.get("ai_key_drivers"),
                ai_risks=data.get("ai_risks"),
                ai_catalyst=data.get("ai_catalyst"),
                ai_entry_zone=data.get("ai_entry_zone"),
                ai_stop_loss=_to_float_or_none(data.get("ai_stop_loss")),
                ai_target_1=_to_float_or_none(data.get("ai_target_1")),
                ai_target_2=_to_float_or_none(data.get("ai_target_2")),
                ai_time_horizon=data.get("ai_time_horizon"),
                ai_confidence=data.get("ai_confidence"),
                model_used=data.get("model_used"),
                tokens_used=data.get("tokens_used"),
                latency_ms=data.get("latency_ms"),
            )
            db.add(opinion)
            db.commit()
    except Exception as exc:
        logger.warning("Failed to persist AI opinion for %s: %s", symbol, exc)


def run_ai_analysis(limit: int | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.enable_ai_analysis:
        return {"status": "disabled", "reason": "AI analysis is disabled in settings."}
    if not settings.openai_api_key:
        return {"status": "disabled", "reason": "OpenAI API key not configured."}

    max_stocks = limit or settings.ai_max_stocks_per_run
    run_id = uuid.uuid4().hex[:12]
    results: list[dict[str, Any]] = []
    errors = 0
    total_tokens = 0
    total_latency = 0

    with SessionLocal() as db:
        symbols = db.scalars(
            select(Stock.symbol).where(Stock.is_active == True).order_by(Stock.symbol.asc())
        ).all()

    for symbol in symbols[:max_stocks]:
        try:
            opinion = generate_opinion(symbol, run_id=run_id)
            if opinion.get("tokens_used"):
                total_tokens += opinion["tokens_used"]
            if opinion.get("latency_ms"):
                total_latency += opinion["latency_ms"]
            if opinion.get("ai_score") is not None:
                results.append(opinion)
            else:
                errors += 1
        except Exception as exc:
            logger.error("AI analysis error for %s: %s", symbol, exc)
            errors += 1

    return {
        "status": "completed",
        "run_id": run_id,
        "symbols_analyzed": len(results),
        "errors": errors,
        "total_tokens_used": total_tokens,
        "total_latency_ms": total_latency,
        "symbols": [r.get("symbol", "")[:30] for r in results],
    }


def latest_opinions(limit: int = 50) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(AiStockOpinion)
            .order_by(AiStockOpinion.created_at.desc())
            .limit(limit)
        ).all()
    return [
        {
            "symbol": r.symbol,
            "ai_score": r.ai_score,
            "ai_signal": r.ai_signal,
            "ai_opinion": r.ai_opinion,
            "ai_reasoning": r.ai_reasoning,
            "ai_key_drivers": r.ai_key_drivers,
            "ai_risks": r.ai_risks,
            "ai_catalyst": r.ai_catalyst,
            "ai_entry_zone": r.ai_entry_zone,
            "ai_stop_loss": r.ai_stop_loss,
            "ai_target_1": r.ai_target_1,
            "ai_target_2": r.ai_target_2,
            "ai_time_horizon": r.ai_time_horizon,
            "ai_confidence": r.ai_confidence,
            "model_used": r.model_used,
            "tokens_used": r.tokens_used,
            "latency_ms": r.latency_ms,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
