from __future__ import annotations

from copy import deepcopy

import pytest

from backend.market_validation import PURGE_MS, TIMEFRAME_MS, build_validation_report
from backend.models import Candle


BASE = 1_800_000_000_000


def candles(timeframe: str, start: int, count: int, *, gap_at: int | None = None) -> list[Candle]:
    step = TIMEFRAME_MS[timeframe]
    rows = []
    for index in range(count):
        if index == gap_at:
            continue
        timestamp = start + index * step
        close = 100 + index * 0.01 + (0.08 if index % 7 < 4 else -0.04)
        open_price = close - 0.02
        rows.append(
            Candle(
                timestamp=timestamp,
                open=open_price,
                high=max(open_price, close) + 0.10,
                low=min(open_price, close) - 0.10,
                close=close,
                volume=10 + index % 5,
                timeframe=timeframe,
                confirm=True,
            )
        )
    return rows


def history(count_15m: int = 420, *, include_1m: bool = True, gap_at: int | None = None):
    start = BASE
    result = {
        "15m": candles("15m", start, count_15m, gap_at=gap_at),
        "1H": candles("1H", start - 260 * TIMEFRAME_MS["1H"], 260 + count_15m // 4 + 10),
        "4H": candles("4H", start - 220 * TIMEFRAME_MS["4H"], 220 + count_15m // 16 + 10),
    }
    if include_1m:
        result["1m"] = candles("1m", start - 220 * TIMEFRAME_MS["1m"], 220 + count_15m * 15)
    return result


def fake_analysis(candles_by_timeframe, *, decision_at, **_kwargs):
    close = candles_by_timeframe["15m"][-1].close
    bullish = int(decision_at // TIMEFRAME_MS["15m"]) % 4 < 3
    bias = "BULLISH" if bullish else "BEARISH"
    return {
        "modelVersion": "indicator-regime-v06-test",
        "decisionAt": decision_at,
        "status": "READY" if candles_by_timeframe.get("1m") else "DEGRADED",
        "overallBias": bias,
        "directionScore": close,
        "alignment": {"consistency": 85 if bullish else 55, "hardConflict": False},
        "actionContext": {"noChase": int(decision_at // TIMEFRAME_MS["15m"]) % 5 == 0, "reasons": []},
        "timeframeAnalyses": {
            "4H": {
                "regime": "TREND" if bullish else "RANGE",
                "structure": {"direction": "BULLISH" if bullish else "BEARISH"},
                "trendStrength": {"score": 65 if bullish else 35},
            }
        },
        "dataQuality": {},
    }


@pytest.fixture(autouse=True)
def patch_analysis(monkeypatch):
    monkeypatch.setattr("backend.market_validation._build_market_analysis", fake_analysis)


def test_report_uses_time_ordered_split_24h_purge_hac_and_fixed_exploratory_label():
    rows = history(420)
    generated_at = rows["15m"][-1].timestamp + TIMEFRAME_MS["15m"]
    report = build_validation_report(rows, generated_at=generated_at)

    assert report["provenance"] == "HISTORICAL_RECONSTRUCTED"
    assert report["classification"] == "EXPLORATORY_ONLY" and report["validationPass"] is False
    assert report["split"]["method"] == "TIME_ORDERED_60_40"
    assert report["split"]["purgeDurationMs"] == PURGE_MS
    assert report["trainingPeriod"]["maximumLabelEligibleAt"] <= report["split"]["splitAt"]
    assert report["validationPeriod"]["start"] >= report["split"]["splitAt"]
    assert report["independentOosWindows"] == 1
    assert report["pbo"]["status"] == report["deflatedSharpe"]["status"] == "unavailable"

    for horizon, requested_lag in {"15m": 1, "1h": 4, "4h": 16, "24h": 96}.items():
        result = report["validationResults"][horizon]
        assert result["n"] > 0
        assert result["requestedHacLag"] == requested_lag
        assert 0 <= result["effectiveSampleSize"] <= result["statisticalN"]
        assert len(result["confidenceInterval95"]) == 2
    assert "BULLISH" in report["groups"]["overallBias"]
    assert "TREND" in report["groups"]["regime4H"]
    assert "NO_CHASE" in report["groups"]["noChase"]


def test_future_candles_after_generated_at_cannot_change_report():
    rows = history(360)
    generated_at = rows["15m"][-1].timestamp + TIMEFRAME_MS["15m"]
    before = build_validation_report(rows, generated_at=generated_at)
    poisoned = deepcopy(rows)
    for timeframe in ("15m", "1H", "4H"):
        step = TIMEFRAME_MS[timeframe]
        poisoned[timeframe].append(
            Candle(
                timestamp=generated_at + 10 * step,
                open=1_000_000,
                high=2_000_000,
                low=999_999,
                close=2_000_000,
                volume=1,
                timeframe=timeframe,
                confirm=True,
            )
        )
    after = build_validation_report(poisoned, generated_at=generated_at)
    # Audit metadata may count rejected future rows, but no snapshot, split,
    # label, group, or statistic may change.
    assert after["trainingResults"] == before["trainingResults"]
    assert after["validationResults"] == before["validationResults"]
    assert after["groups"] == before["groups"]
    assert after["split"] == before["split"]
    assert after["coverage"]["analysisSamplesBuilt"] == before["coverage"]["analysisSamplesBuilt"]
    assert after["coverage"]["inputQuality"]["15m"]["futureExcluded"] == 1


def test_missing_1m_is_explicit_coverage_degradation_and_never_zero_filled():
    rows = history(320, include_1m=False)
    generated_at = rows["15m"][-1].timestamp + TIMEFRAME_MS["15m"]
    report = build_validation_report(rows, generated_at=generated_at)

    coverage = report["coverage"]["timeframeCoverage"]["1m"]
    assert coverage["availableSamples"] == 0 and coverage["ratio"] == 0
    assert any("1m特征覆盖仅" in item and "不补零" in item for item in report["limitations"])
    assert report["coverage"]["analysisSamplesBuilt"] == 320


def test_immature_labels_stay_pending_and_future_gap_is_not_bridged():
    complete = history(340)
    generated_at = complete["15m"][-1].timestamp + TIMEFRAME_MS["15m"]
    baseline = build_validation_report(complete, generated_at=generated_at)
    assert baseline["coverage"]["labelStatus"]["24h"]["PENDING"] == 96

    gapped = history(340, gap_at=250)
    report = build_validation_report(gapped, generated_at=generated_at)
    assert report["coverage"]["labelStatus"]["15m"]["GAP"] >= 1
    assert report["coverage"]["labelStatus"]["24h"]["GAP"] > baseline["coverage"]["labelStatus"]["24h"]["GAP"]
    assert report["coverage"]["inputQuality"]["15m"]["gapEdges"] >= 1


def test_locked_oos_price_poison_cannot_change_training_statistics():
    rows = history(420)
    generated_at = rows["15m"][-1].timestamp + TIMEFRAME_MS["15m"]
    baseline = build_validation_report(rows, generated_at=generated_at)
    split_at = baseline["split"]["splitAt"]

    poisoned = deepcopy(rows)
    poisoned_15m = []
    for row in poisoned["15m"]:
        if row.timestamp >= split_at:
            close = row.close * 1.5
            row = row.model_copy(
                update={
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                }
            )
        poisoned_15m.append(row)
    poisoned["15m"] = poisoned_15m
    changed = build_validation_report(poisoned, generated_at=generated_at)

    assert changed["trainingPeriod"] == baseline["trainingPeriod"]
    assert changed["trainingResults"] == baseline["trainingResults"]
    assert changed["validationResults"] != baseline["validationResults"]


def test_small_effective_sample_is_reported_insufficient():
    rows = history(180)
    generated_at = rows["15m"][-1].timestamp + TIMEFRAME_MS["15m"]
    report = build_validation_report(rows, generated_at=generated_at, max_samples=60)
    result = report["validationResults"]["15m"]
    assert result["statisticalN"] < 30
    assert result["statisticalStatus"] == "INSUFFICIENT"
    assert result["insufficiencyReasons"]
    assert any("max_samples=60" in item for item in report["limitations"])


def test_systematic_stride_keeps_original_15m_grid_for_hac():
    rows = history(360)
    generated_at = rows["15m"][-1].timestamp + TIMEFRAME_MS["15m"]
    report = build_validation_report(rows, generated_at=generated_at, max_samples=240, decision_stride=4)
    assert report["coverage"]["decisionStride"] == 4
    assert report["coverage"]["decisionSamplesRequested"] == 60
    assert report["coverage"]["underlyingDecisionCloses"] == 360
    assert any("每4根15m收盘系统抽样一次" in item for item in report["limitations"])
    assert report["validationResults"]["4h"]["requestedHacLag"] == 16


def test_invalid_arguments_and_empty_15m_are_honest():
    with pytest.raises(ValueError):
        build_validation_report({}, generated_at=BASE, max_samples=0)
    with pytest.raises(ValueError):
        build_validation_report({}, generated_at=BASE, decision_stride=0)
    report = build_validation_report({"1H": []}, generated_at=BASE)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["validationPass"] is False
    assert report["coverage"]["decisionGrid"] == "15m"
