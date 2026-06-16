from __future__ import annotations

from app.intelligence.weighting_engine import normalize_weights, weighted_score


def test_weighted_score_rebalances_available_sources() -> None:
    score, weights = weighted_score({"technical": 80, "financial": None, "news": 60}, normalize_weights({"technical": 35, "financial": 25, "news": 20}))
    assert score > 60
    assert round(sum(weights.values()), 4) == 1

