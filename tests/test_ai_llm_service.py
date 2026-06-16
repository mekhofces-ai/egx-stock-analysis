from __future__ import annotations

from app.services.ai_llm_service import _build_user_prompt, _default_opinion, _parse_opinion_response


def test_default_opinion() -> None:
    result = _default_opinion("COMI")
    assert result["ai_signal"] == "NEUTRAL"
    assert result["ai_score"] is None
    assert "COMI" in result["ai_opinion"]


def test_default_opinion_with_error() -> None:
    result = _default_opinion("COMI", "API timeout")
    assert "API timeout" in result["ai_reasoning"]


def test_parse_opinion_response_json() -> None:
    raw = '{"ai_score": 75.5, "ai_signal": "BUY", "ai_opinion": "Good stock"}'
    result = _parse_opinion_response(raw)
    assert result is not None
    assert result["ai_score"] == 75.5
    assert result["ai_signal"] == "BUY"


def test_parse_opinion_response_code_block() -> None:
    raw = '```json\n{"ai_score": 80, "ai_signal": "STRONG BUY"}\n```'
    result = _parse_opinion_response(raw)
    assert result is not None
    assert result["ai_score"] == 80
    assert result["ai_signal"] == "STRONG BUY"


def test_parse_opinion_response_invalid() -> None:
    result = _parse_opinion_response("not json at all")
    assert result is None


def test_parse_opinion_response_embedded_braces() -> None:
    raw = "Here is the result: {\"ai_score\": 65, \"ai_signal\": \"WATCH\"}"
    result = _parse_opinion_response(raw)
    assert result is not None
    assert result["ai_score"] == 65


def test_build_user_prompt_includes_symbol() -> None:
    analysis = {
        "symbol": "COMI",
        "name": "COMI (Banks)",
        "sector": "Banks",
        "last_price": 50.0,
        "ai_score": 72.5,
        "ai_signal": "BUY",
        "technical": {"score": 80, "signal": "BUY", "reason": "Bullish trend"},
        "financial": {"score": 70, "signal": "BUY"},
        "news": {"score": 65, "signal": "NEUTRAL"},
        "telegram": {"score": 60, "signal": "WATCH"},
        "strategy": {"score": 75, "signal": "BUY"},
        "final_decision": {"signal": "BUY", "score": 72},
        "combined_analysis": {"score": 70, "recommendation": "BUY"},
        "opportunity": {"score": 68, "recommendation": "BUY"},
    }
    prompt = _build_user_prompt("COMI", analysis)
    assert "COMI" in prompt
    assert "Technical" in prompt
    assert "Financial" in prompt
    assert "News" in prompt
    assert "Telegram" in prompt
    assert "Strategy" in prompt
    assert "Final Decision" in prompt
