import json
import math

import numpy as np

from backend.market_regime import (
    CONTRIBUTION_WEIGHTS,
    TIMEFRAME_MS,
    analyze_timeframe,
    confirmed_swings,
    direction_label,
    dmi_adx,
    flat_neutral_rsi,
    prepare_candles,
)
from backend.models import Candle


DECISION_AT = 1_767_225_600_000


def candles(timeframe="1H", n=260, slope=2.0, amplitude=40.0, decision_at=DECISION_AT):
    interval = TIMEFRAME_MS[timeframe]
    end = decision_at // interval * interval - interval
    start = end - (n - 1) * interval
    result = []
    for index in range(n):
        middle = 30_000 + slope * index + amplitude * math.sin(index * 0.35)
        open_, close = middle - 1, middle + 1
        result.append(
            Candle(
                timestamp=start + index * interval,
                open=open_,
                high=max(open_, close) + 10,
                low=min(open_, close) - 10,
                close=close,
                volume=100 + index % 17,
                timeframe=timeframe,
                confirm=True,
            )
        )
    return result


def test_flat_rsi_is_neutral_and_dmi_returns_three_aligned_series():
    values = np.full(80, 100.0)
    rsi = flat_neutral_rsi(values)
    assert rsi[-1] == 50.0
    adx, plus_di, minus_di = dmi_adx(values + 1, values - 1, values)
    assert len(adx) == len(plus_di) == len(minus_di) == len(values)
    assert np.isfinite(adx[-1])
    assert plus_di[-1] == minus_di[-1] == 0.0


def test_future_and_unconfirmed_rows_are_removed_before_strict_validation():
    base = candles()
    clean = analyze_timeframe(base, "1H", DECISION_AT)
    future = {
        "timestamp": DECISION_AT,
        "open": float("nan"),
        "high": -1,
        "low": -2,
        "close": float("inf"),
        "volume": -1,
        "timeframe": "1H",
        "confirm": True,
    }
    unconfirmed = base[-1].model_copy(update={"close": 999_999, "high": 1_000_000, "confirm": False})
    dirty = analyze_timeframe([future, unconfirmed, *reversed(base)], "1H", DECISION_AT)
    assert dirty["directionScore"] == clean["directionScore"]
    assert dirty["asOf"] == clean["asOf"]
    assert dirty["dataQuality"]["excludedFuture"] == 1
    assert dirty["dataQuality"]["excludedUnconfirmed"] == 1


def test_exact_duplicates_dedupe_but_conflicting_duplicate_blocks_timeframe():
    base = candles()
    exact_rows, exact_quality = prepare_candles([*base, base[-1]], "1H", DECISION_AT)
    assert len(exact_rows) == len(base)
    assert exact_quality["status"] == "AVAILABLE"
    assert exact_quality["exactDuplicatesRemoved"] == 1

    conflicting = base[-1].model_copy(update={"close": base[-1].close + 1})
    _, conflict_quality = prepare_candles([*base, conflicting], "1H", DECISION_AT)
    assert conflict_quality["status"] == "INSUFFICIENT_DATA"
    assert conflict_quality["qualityCode"] == "CONFLICTING_DUPLICATE"


def test_strict_validation_alignment_and_longest_contiguous_suffix():
    base = candles(n=410)
    prepared, quality = prepare_candles(base, "1H", DECISION_AT)
    assert len(prepared) == 400
    assert quality["status"] == "AVAILABLE"

    misaligned = base[-2].model_copy(update={"timestamp": base[-2].timestamp + 1})
    _, invalid = prepare_candles([*base[:-2], misaligned, base[-1]], "1H", DECISION_AT)
    assert invalid["qualityCode"] == "MISALIGNED_TIMESTAMP"

    with_gap = [*base[:100], *base[101:]]
    suffix, gap_quality = prepare_candles(with_gap, "1H", DECISION_AT)
    assert len(suffix) == 309
    assert gap_quality["status"] == "AVAILABLE"
    assert gap_quality["qualityCode"] == "OK_WITH_OLDER_GAPS"


def test_complete_timeframe_uses_frozen_weights_and_json_safe_result():
    result = analyze_timeframe(candles(), "1H", DECISION_AT)
    assert result["status"] == "AVAILABLE"
    assert result["coverage"] == 1.0
    assert {item["name"]: item["weight"] for item in result["contributions"]} == CONTRIBUTION_WEIGHTS
    assert sum(item["score"] for item in result["contributions"]) == result["rawDirectionScore"]
    assert "adx" not in {item["name"] for item in result["contributions"]}
    assert 0 <= result["trendStrength"]["score"] <= 100
    assert set(result["trendStrength"]["components"]) == {
        "adx", "emaSlope", "structurePersistence", "breakoutPersistence", "volumeConfirmation", "volatilitySupport",
    }
    assert result["indicators"]["ema200"] is not None
    assert result["structure"]["label"] in {"HH_HL", "LH_LL", "RANGE", "TRANSITION", "UNKNOWN"}
    assert result["volatilityState"] in {"COMPRESSION", "NORMAL", "EXPANSION", "EXTREME"}
    assert result["extendedState"] in {"NOT_EXTENDED", "EXTENDED_UP", "EXTENDED_DOWN"}
    assert result["volatility"]["baselineExcludesCurrent"] is True
    assert result["volatility"]["baselineCount"] <= 100
    json.dumps(result, allow_nan=False)


def test_swing_confirmation_and_score_labels_obey_boundaries():
    prepared, _ = prepare_candles(candles(), "1H", DECISION_AT)
    swings = confirmed_swings(prepared, DECISION_AT)
    assert swings
    assert all(item["confirmedAt"] <= DECISION_AT for item in swings)
    assert direction_label(60) == ("STRONG_BULLISH", "强多")
    assert direction_label(59.999)[0] == "BULLISH"
    assert direction_label(25)[0] == "BULLISH"
    assert direction_label(24.999)[0] == "NEUTRAL"
    assert direction_label(-24.999)[0] == "NEUTRAL"
    assert direction_label(-25)[0] == "BEARISH"
    assert direction_label(-60)[0] == "STRONG_BEARISH"


def test_insufficient_and_stale_have_compatible_status_and_detailed_code():
    insufficient = analyze_timeframe(candles(n=199), "1H", DECISION_AT)
    assert insufficient["status"] == "INSUFFICIENT_DATA"
    assert insufficient["qualityCode"] == "INSUFFICIENT_CONTIGUOUS_CANDLES"
    assert insufficient["directionScore"] is None

    stale = analyze_timeframe(candles(decision_at=DECISION_AT - TIMEFRAME_MS["1H"]), "1H", DECISION_AT)
    assert stale["status"] == "STALE"
    assert stale["qualityCode"] == "LATEST_CANDLE_STALE"
    assert stale["directionScore"] is None
