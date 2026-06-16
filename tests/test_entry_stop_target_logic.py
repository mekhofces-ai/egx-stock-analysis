from __future__ import annotations

import pandas as pd

from app.services.daily_loss_audit import (
    BAD_CALL,
    EVAL_DATA_MISSING,
    EVAL_ENTRY_NOT_REACHED,
    EVAL_NOT_EVALUATED,
    EVAL_STOP_HIT,
    EVAL_TARGET_HIT,
    GOOD_CALL,
    LOW_MISSING_DATA,
    NO_ENTRY,
    classify_recommendation_path,
)


def _candles(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    return frame


def test_stop_loss_hit_before_target_is_bad_call() -> None:
    audit = classify_recommendation_path(
        _candles([{"datetime": "2026-06-07 10:00", "open": 10.2, "high": 10.4, "low": 9.4, "close": 9.6, "volume": 1}]),
        entry_zone_low=10,
        entry_zone_high=10.5,
        stop_loss=9.5,
        target_1=11,
        target_2=12,
        target_3=13,
    )
    assert audit.result == BAD_CALL
    assert audit.stop_loss_hit is True
    assert audit.evaluation_status == EVAL_STOP_HIT


def test_target_hit_before_stop_is_good_call() -> None:
    audit = classify_recommendation_path(
        _candles([{"datetime": "2026-06-07 10:00", "open": 10.2, "high": 11.2, "low": 10.1, "close": 11, "volume": 1}]),
        entry_zone_low=10,
        entry_zone_high=10.5,
        stop_loss=9.5,
        target_1=11,
        target_2=12,
        target_3=13,
    )
    assert audit.result == GOOD_CALL
    assert audit.target_1_hit is True
    assert audit.evaluation_status == EVAL_TARGET_HIT


def test_entry_never_touched_is_no_entry() -> None:
    audit = classify_recommendation_path(
        _candles([{"datetime": "2026-06-07 10:00", "open": 9.6, "high": 9.8, "low": 9.2, "close": 9.4, "volume": 1}]),
        entry_zone_low=10,
        entry_zone_high=10.5,
        stop_loss=9.5,
        target_1=11,
        target_2=12,
        target_3=13,
    )
    assert audit.result == NO_ENTRY
    assert audit.entry_touched is False


def test_no_future_candle_is_not_evaluated() -> None:
    audit = classify_recommendation_path(
        pd.DataFrame(),
        entry_zone_low=10,
        entry_zone_high=10.5,
        stop_loss=9.5,
        target_1=11,
        target_2=12,
        target_3=13,
    )
    assert audit.evaluation_status == EVAL_NOT_EVALUATED
    assert audit.final_quality == "Not Evaluated"


def test_missing_price_data_is_data_missing() -> None:
    audit = classify_recommendation_path(
        pd.DataFrame(),
        entry_zone_low=10,
        entry_zone_high=10.5,
        stop_loss=9.5,
        target_1=11,
        target_2=12,
        target_3=13,
        evaluation_quality=LOW_MISSING_DATA,
    )
    assert audit.evaluation_status == EVAL_DATA_MISSING


def test_entry_not_reached_is_not_win_or_loss() -> None:
    audit = classify_recommendation_path(
        _candles([{"datetime": "2026-06-08 10:00", "open": 12.0, "high": 12.5, "low": 11.8, "close": 12.2, "volume": 1}]),
        entry_zone_low=10,
        entry_zone_high=11,
        stop_loss=9,
        target_1=12,
        target_2=13,
        target_3=14,
        signal="BUY",
    )
    assert audit.evaluation_status == EVAL_ENTRY_NOT_REACHED
    assert audit.entry_touched is False
    assert audit.stop_loss_hit is False
    assert audit.target_1_hit is False
