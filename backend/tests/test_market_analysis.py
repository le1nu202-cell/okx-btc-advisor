import copy
import json
import math

from backend.market_analysis import (
    analysis_changed_significantly,
    build_market_analysis,
    compact_analysis_snapshot,
)
from backend.market_regime import MODEL_VERSION, TIMEFRAME_MS, TIMEFRAME_WEIGHTS
from backend.models import Candle


DECISION_AT = 1_767_225_600_000


def candles(timeframe, slope=2.0, amplitude=40.0, n=260):
    interval = TIMEFRAME_MS[timeframe]
    end = DECISION_AT // interval * interval - interval
    start = end - (n - 1) * interval
    result = []
    for index in range(n):
        middle = 30_000 + slope * index + amplitude * math.sin(index * 0.35)
        if slope >= 0:
            open_, close = middle - 1, middle + 1
        else:
            open_, close = middle + 1, middle - 1
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


def all_series(slopes=None):
    slopes = slopes or {}
    return {
        timeframe: candles(timeframe, slope=slopes.get(timeframe, 2.0))
        for timeframe in ("1m", "15m", "1H", "4H")
    }


def test_build_contract_is_camel_case_deterministic_and_uses_frozen_weights():
    source = all_series()
    first = build_market_analysis(source, DECISION_AT, include_series=True)
    second = build_market_analysis(source, DECISION_AT, include_series=True)
    assert first == second
    assert first["modelVersion"] == MODEL_VERSION == "indicator-regime-v06.0.0"
    assert set(first["timeframeAnalyses"]) == {"1m", "15m", "1H", "4H"}
    assert first["alignment"]["weights"] == TIMEFRAME_WEIGHTS
    expected = sum(
        TIMEFRAME_WEIGHTS[timeframe] * first["timeframeAnalyses"][timeframe]["directionScore"]
        for timeframe in TIMEFRAME_WEIGHTS
    )
    assert abs(first["directionScore"] - expected) < 1e-4
    assert all(set(first["chartSeries"][timeframe]) == {"ema20", "ema50", "ema200", "vwap"} for timeframe in TIMEFRAME_WEIGHTS)
    assert all(len(first["chartSeries"][timeframe]["ema20"]) == 241 for timeframe in TIMEFRAME_WEIGHTS)
    assert all(len(first["chartSeries"][timeframe]["ema200"]) == 61 for timeframe in TIMEFRAME_WEIGHTS)
    assert first["instrument"] == "BTC-USDT-SWAP"
    assert first["asOf"] == DECISION_AT
    assert first["compositeScore"] == first["directionScore"]
    assert first["alignmentScore"] == first["alignment"]["consistency"]
    assert first["summary"] and first["primaryReason"]
    json.dumps(first, allow_nan=False)


def test_future_rows_do_not_change_analysis_or_chart_series():
    source = all_series()
    clean = build_market_analysis(source, DECISION_AT)
    future = Candle(
        timestamp=DECISION_AT,
        open=1,
        high=1_000_000,
        low=1,
        close=999_999,
        volume=1_000_000,
        timeframe="15m",
        confirm=True,
    )
    dirty = build_market_analysis({**source, "15m": [*source["15m"], future]}, DECISION_AT)
    assert dirty["directionScore"] == clean["directionScore"]
    assert dirty["timeframeAnalyses"]["15m"]["asOf"] == clean["timeframeAnalyses"]["15m"]["asOf"]
    assert dirty["chartSeries"]["15m"] == clean["chartSeries"]["15m"]
    assert dirty["keyLevels"]["references"] == clean["keyLevels"]["references"]


def test_one_minute_reversal_cannot_change_main_direction():
    bullish_micro = build_market_analysis(all_series(), DECISION_AT, include_series=False)
    bearish_micro = build_market_analysis(all_series({"1m": -2.0}), DECISION_AT, include_series=False)
    assert bullish_micro["overallBias"] == bearish_micro["overallBias"] == "STRONG_BULLISH"
    assert bullish_micro["timeframeAnalyses"]["1m"]["bias"] != bearish_micro["timeframeAnalyses"]["1m"]["bias"]


def test_opposite_four_hour_and_one_hour_is_a_hard_conflict():
    result = build_market_analysis(all_series({"1H": -2.0}), DECISION_AT, include_series=False)
    assert result["alignment"]["hardConflict"] is True
    assert result["alignment"]["status"] == "HARD_CONFLICT"
    assert result["qualityCode"] == "HARD_4H_1H_CONFLICT"
    assert result["overallBias"] == "CONFLICT"
    assert result["directionScore"] is None
    assert result["actionContext"]["action"] == "WAIT"


def test_no_chase_has_concrete_reasons_and_never_rewrites_direction_score():
    result = build_market_analysis(all_series(), DECISION_AT, include_series=False)
    assert result["actionContext"]["noChase"] is True
    assert result["actionContext"]["action"] == "NO_CHASE"
    assert result["actionContext"]["directionalAction"] == "WATCH_LONG"
    assert result["actionContext"]["reasons"]
    assert any("15m" in reason and ("ATR" in reason or "RSI" in reason) for reason in result["actionContext"]["reasons"])
    assert result["directionScore"] == result["alignment"]["weightedScoreBefore1mGuard"]


def test_connection_and_gap_status_block_publication_with_compatible_statuses():
    source = all_series()
    disconnected = build_market_analysis(source, DECISION_AT, connection_status="reconnecting", include_series=False)
    assert disconnected["status"] == "STALE"
    assert disconnected["qualityCode"] == "CONNECTION_NOT_CONNECTED"
    assert disconnected["directionScore"] is None

    gap = build_market_analysis(source, DECISION_AT, gap_status={"1H": True}, include_series=False)
    assert gap["status"] == "INSUFFICIENT_DATA"
    assert gap["timeframeAnalyses"]["1H"]["qualityCode"] == "EXTERNAL_GAP_DETECTED"
    assert gap["directionScore"] is None

    stale_core={**source,"1H":[row.model_copy(update={"timestamp":row.timestamp-TIMEFRAME_MS["1H"]}) for row in source["1H"]]}
    stale=build_market_analysis(stale_core,DECISION_AT,include_series=False)
    assert stale["status"]=="STALE"
    assert stale["qualityCode"]=="REQUIRED_TIMEFRAME_STALE"
    assert stale["directionScore"] is None and stale["confidence"]==0

    stale_micro={**source,"1m":[row.model_copy(update={"timestamp":row.timestamp-TIMEFRAME_MS["1m"]}) for row in source["1m"]]}
    degraded=build_market_analysis(stale_micro,DECISION_AT,include_series=False)
    assert degraded["status"]=="AVAILABLE"
    assert degraded["qualityCode"]=="DEGRADED_TIMEFRAME_COVERAGE"
    assert degraded["dataQuality"]["weightedCoverage"]==0.95
    assert degraded["confidence"]<build_market_analysis(source,DECISION_AT,include_series=False)["confidence"]


def test_compact_snapshot_and_significant_change_filter():
    analysis = build_market_analysis(all_series(), DECISION_AT, include_series=False)
    compact = compact_analysis_snapshot(analysis)
    assert "chartSeries" not in compact
    assert compact["overallBias"] == analysis["overallBias"]
    assert analysis_changed_significantly(analysis, copy.deepcopy(analysis)) is False

    changed = copy.deepcopy(analysis)
    changed["actionContext"]["noChase"] = not changed["actionContext"]["noChase"]
    assert analysis_changed_significantly(analysis, changed) is True
    assert analysis_changed_significantly(None, analysis) is True
