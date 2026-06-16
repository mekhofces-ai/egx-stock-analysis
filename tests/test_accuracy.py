from __future__ import annotations

from app.intelligence.accuracy_tracker import _correct


def test_accuracy_correctness_rules() -> None:
    assert _correct("BUY", 2.0) is True
    assert _correct("BUY", -2.0) is False
    assert _correct("AVOID / SELL", -2.0) is True
    assert _correct("WATCH", 0.3) is True

