from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DISCLAIMER, get_settings
from app.models import ExtractedSignal, FinalAnalysis, Stock, TechnicalAnalysis, TelegramMessage
from app.services.market_data.base import ProviderChain, build_provider_chain
from app.services.parser import parse_message
from app.services.signal_validator import validate_signal
from app.services.stock_aliases import COMMON_ARABIC_STOCK_ALIASES
from app.services.technical_analysis import analyze_symbol

logger = logging.getLogger(__name__)


def known_symbols(db: Session) -> list[str]:
    return db.scalars(select(Stock.symbol).where(Stock.is_active.is_(True))).all()


def known_aliases(db: Session) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    stocks = db.scalars(select(Stock).where(Stock.is_active.is_(True))).all()
    for stock in stocks:
        values = [stock.symbol, stock.name_ar, stock.name_en, stock.tradingview_symbol]
        aliases[stock.symbol] = [str(value) for value in values if value]
    for symbol, values in COMMON_ARABIC_STOCK_ALIASES.items():
        aliases.setdefault(symbol, [])
        aliases[symbol].extend(values)
    return aliases


def parse_pending_messages(db: Session, limit: int = 100) -> list[ExtractedSignal]:
    symbols = known_symbols(db)
    aliases = known_aliases(db)
    messages = db.scalars(
        select(TelegramMessage)
        .where(TelegramMessage.parsed.is_(False))
        .order_by(TelegramMessage.created_at.asc())
        .limit(limit)
    ).all()
    extracted: list[ExtractedSignal] = []
    for message in messages:
        parsed = parse_message(message.text or "", known_symbols=symbols, known_aliases=aliases)
        payload = parsed.to_dict()
        if parsed.stock_symbol or parsed.direction:
            signal = ExtractedSignal(
                source_id=message.source_id,
                telegram_message_id=message.id,
                stock_symbol=payload["stock_symbol"],
                stock_name=payload["stock_name"],
                direction=payload["direction"],
                entry_price=payload["entry_price"],
                targets=payload["targets"],
                stop_loss=payload["stop_loss"],
                support=payload["support"],
                resistance=payload["resistance"],
                timeframe=payload["timeframe"],
                hype_words=payload["hype_words"],
                risk_flags=payload["risk_flags"],
                sentiment_score=payload["sentiment_score"],
                raw=payload["raw"],
            )
            db.add(signal)
            extracted.append(signal)
        message.parsed = True
    db.commit()
    return extracted


def _store_technical(db: Session, snapshot: dict[str, Any]) -> TechnicalAnalysis:
    technical = TechnicalAnalysis(
        symbol=snapshot["symbol"],
        as_of=snapshot["as_of"],
        timeframe=snapshot["timeframe"],
        indicators=snapshot["indicators"],
        trend_direction=snapshot["trend_direction"],
        volatility_score=snapshot["volatility_score"],
        liquidity_score=snapshot["liquidity_score"],
        technical_score=snapshot["technical_score"],
        risk_score=snapshot["risk_score"],
        support=snapshot["support"],
        resistance=snapshot["resistance"],
        breakout=snapshot["breakout"],
        provider=snapshot["provider"],
        is_mock=snapshot["is_mock"],
    )
    db.add(technical)
    db.flush()
    return technical


def analyze_signal(db: Session, signal: ExtractedSignal, provider_chain: ProviderChain | None = None) -> FinalAnalysis | None:
    if not signal.stock_symbol:
        signal.status = "ignored_missing_symbol"
        db.commit()
        return None
    provider_chain = provider_chain or build_provider_chain(get_settings())
    try:
        snapshot = analyze_symbol(signal.stock_symbol, provider_chain).to_dict()
    except Exception as exc:
        logger.warning("Technical analysis failed for %s: %s", signal.stock_symbol, exc)
        snapshot = {
            "symbol": signal.stock_symbol,
            "as_of": signal.created_at,
            "timeframe": "1D",
            "indicators": {"last_price": None},
            "trend_direction": "UNKNOWN",
            "volatility_score": 80.0,
            "liquidity_score": 0.0,
            "technical_score": 25.0,
            "risk_score": 85.0,
            "support": None,
            "resistance": None,
            "breakout": False,
            "provider": "missing",
            "is_mock": False,
        }
    technical = _store_technical(db, snapshot)
    result = validate_signal(signal, snapshot, source=signal.source)
    final = FinalAnalysis(
        extracted_signal_id=signal.id,
        source_id=signal.source_id,
        technical_analysis_id=technical.id,
        symbol=result["symbol"],
        final_decision=result["final_decision"],
        confidence_score=result["confidence_score"],
        entry_zone=result["entry_zone"],
        stop_loss=result["stop_loss"],
        targets=result["targets"],
        reasons=result["reasons"],
        warnings=result["warnings"],
        invalidation_point=result["invalidation_point"],
        position_size_suggestion=result["position_size_suggestion"],
        last_price=result["last_price"],
        trend=result["trend"],
        disclaimer=DISCLAIMER,
    )
    db.add(final)
    signal.status = "analyzed"
    db.commit()
    db.refresh(final)
    return final


def analyze_pending_signals(db: Session, provider_chain: ProviderChain | None = None, limit: int = 50) -> list[FinalAnalysis]:
    parse_pending_messages(db, limit=limit)
    pending = db.scalars(
        select(ExtractedSignal)
        .where(ExtractedSignal.status == "pending_analysis")
        .order_by(ExtractedSignal.created_at.asc())
        .limit(limit)
    ).all()
    analyses: list[FinalAnalysis] = []
    provider_chain = provider_chain or build_provider_chain(get_settings())
    for signal in pending:
        final = analyze_signal(db, signal, provider_chain=provider_chain)
        if final:
            analyses.append(final)
    return analyses


def analyze_symbol_manually(
    db: Session,
    symbol: str,
    direction: str = "WATCH",
    entry_price: float | None = None,
    stop_loss: float | None = None,
    targets: list[float] | None = None,
    provider_chain: ProviderChain | None = None,
) -> FinalAnalysis:
    provider_chain = provider_chain or build_provider_chain(get_settings())
    snapshot = analyze_symbol(symbol, provider_chain).to_dict()
    technical = _store_technical(db, snapshot)
    pseudo_signal = {
        "stock_symbol": symbol.upper(),
        "direction": direction.upper(),
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "targets": targets or [],
        "hype_words": [],
        "risk_flags": [],
    }
    result = validate_signal(pseudo_signal, snapshot)
    final = FinalAnalysis(
        technical_analysis_id=technical.id,
        symbol=result["symbol"],
        final_decision=result["final_decision"],
        confidence_score=result["confidence_score"],
        entry_zone=result["entry_zone"],
        stop_loss=result["stop_loss"],
        targets=result["targets"],
        reasons=result["reasons"],
        warnings=result["warnings"],
        invalidation_point=result["invalidation_point"],
        position_size_suggestion=result["position_size_suggestion"],
        last_price=result["last_price"],
        trend=result["trend"],
        disclaimer=DISCLAIMER,
    )
    db.add(final)
    db.commit()
    db.refresh(final)
    return final


def format_alert(final: FinalAnalysis, signal: ExtractedSignal | None = None, source_username: str | None = None) -> str:
    source = source_username or "Manual"
    direction = signal.direction if signal and signal.direction else "WATCH"
    targets = ", ".join(f"{target:.2f}" for target in (final.targets or [])) or "-"
    reasons = "\n".join(f"{idx}. {reason}" for idx, reason in enumerate(final.reasons or [], start=1)) or "1. No reasons recorded."
    warnings = "\n".join(f"* {warning}" for warning in (final.warnings or [])) or "* None"
    risk = "-"
    if final.technical_analysis and final.technical_analysis.risk_score is not None:
        risk = f"{final.technical_analysis.risk_score:.0f}%"
    return (
        "📊 EGX Signal Analysis\n"
        f"Stock: {final.symbol}\n"
        f"Source: {source}\n"
        f"Telegram Direction: {direction}\n"
        f"Last Price: {final.last_price if final.last_price is not None else '-'}\n"
        f"Trend: {final.trend or '-'}\n\n"
        f"Decision: {final.final_decision}\n"
        f"Confidence: {final.confidence_score:.0f}%\n"
        f"Risk: {risk}\n\n"
        f"Entry: {final.entry_zone or '-'}\n"
        f"Stop Loss: {final.stop_loss if final.stop_loss is not None else '-'}\n"
        f"Targets: {targets}\n\n"
        f"Reasons:\n{reasons}\n\n"
        f"Warnings:\n{warnings}\n\n"
        f"Disclaimer: {DISCLAIMER}"
    )
