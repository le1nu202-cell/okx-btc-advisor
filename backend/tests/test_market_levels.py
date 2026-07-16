import json
import math
from datetime import datetime, timezone

from backend.market_levels import build_key_levels
from backend.market_regime import TIMEFRAME_MS
from backend.models import Candle


DECISION_AT = int(datetime(2026, 1, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)


def candles(timeframe, slope=1.5, amplitude=60.0, n=300):
    interval = TIMEFRAME_MS[timeframe]
    end = DECISION_AT // interval * interval - interval
    start = end - (n - 1) * interval
    result = []
    for index in range(n):
        middle = 30_000 + slope * index + amplitude * math.sin(index * 0.31)
        open_, close = middle - 2, middle + 2
        result.append(
            Candle(
                timestamp=start + index * interval,
                open=open_,
                high=max(open_, close) + 12,
                low=min(open_, close) - 12,
                close=close,
                volume=100 + index % 23,
                timeframe=timeframe,
                confirm=True,
            )
        )
    return result


def all_series():
    return {timeframe: candles(timeframe) for timeframe in ("1m", "15m", "1H", "4H")}


def calendar_series(decision_at: int, timeframe: str, count: int) -> list[Candle]:
    interval = TIMEFRAME_MS[timeframe]
    end = decision_at // interval * interval - interval
    start = end - (count - 1) * interval
    result = []
    for index in range(count):
        timestamp = start + index * interval
        day = datetime.fromtimestamp(timestamp / 1000, timezone.utc).day
        price = 10_000 + day * 100
        result.append(
            Candle(
                timestamp=timestamp,
                open=price,
                high=price + 50,
                low=price - 50,
                close=price,
                volume=1,
                timeframe=timeframe,
                confirm=True,
            )
        )
    return result


def test_levels_include_required_point_in_time_sources_and_are_bounded():
    result = build_key_levels(all_series(), DECISION_AT)
    references = result["references"]
    assert references["previousDayHigh"] is not None
    assert references["previousDayLow"] is not None
    assert references["dayOpen"] is not None
    assert references["weekOpen"] is not None
    assert references["dailyVwap"] is not None
    assert references["weeklyVwap"] is not None
    assert references["volumeProfile"]["poc"] is not None
    assert references["volumeProfile"]["method"] == "candle_range_uniform"
    assert len(result["supports"]) <= 3
    assert len(result["resistances"]) <= 3
    assert all(level["confirmedAt"] <= DECISION_AT for level in [*result["supports"], *result["resistances"]])
    assert any(
        source.startswith("SWING_")
        for level in [*result["supports"], *result["resistances"]]
        for source in level["sources"]
    )
    json.dumps(result, allow_nan=False)


def test_future_outlier_cannot_change_levels_or_volume_profile():
    source = all_series()
    clean = build_key_levels(source, DECISION_AT)
    future = Candle(
        timestamp=DECISION_AT,
        open=1,
        high=1_000_000,
        low=1,
        close=999_999,
        volume=1_000_000,
        timeframe="1H",
        confirm=True,
    )
    dirty = build_key_levels({**source, "1H": [*source["1H"], future]}, DECISION_AT)
    assert dirty["references"] == clean["references"]
    assert dirty["supports"] == clean["supports"]
    assert dirty["resistances"] == clean["resistances"]


def test_levels_are_atr_clustered_and_sorted_nearest_first():
    result = build_key_levels(all_series(), DECISION_AT)
    supports = result["supports"]
    resistances = result["resistances"]
    assert result["clusterAtr"] > 0
    assert result["clusterThreshold"] >= result["clusterAtr"] * 0.35 - 1e-8
    assert supports == sorted(supports, key=lambda item: item["price"], reverse=True)
    assert resistances == sorted(resistances, key=lambda item: item["price"])
    assert all(level["distancePct"] <= 0 for level in supports)
    assert all(level["distancePct"] > 0 for level in resistances)


def test_conflicting_duplicate_is_not_silently_used_as_level_source():
    source = all_series()
    original = source["1H"][-1]
    conflict = original.model_copy(update={"close": original.close + 1})
    result = build_key_levels({**source, "1H": [*source["1H"], conflict]}, DECISION_AT)
    assert result["dataQuality"]["1H"]["qualityCode"] == "CONFLICTING_DUPLICATE"
    # Lower timeframes may still provide references, but the invalid 1H series
    # itself cannot supply a swing level.
    assert all(
        "SWING_1H" not in level["sources"]
        for level in [*result["supports"], *result["resistances"]]
    )


def test_first_hour_after_utc_midnight_uses_previous_natural_day():
    decision_at = int(datetime(2026, 1, 7, 0, 15, tzinfo=timezone.utc).timestamp() * 1000)
    source = {
        "1H": calendar_series(decision_at, "1H", 300),
        "15m": calendar_series(decision_at, "15m", 400),
    }

    result = build_key_levels(source, decision_at, current_price=10_700)
    references = result["references"]

    assert references["previousDayHigh"] == 10_650
    assert references["previousDayLow"] == 10_550
    assert references["dayOpen"] == 10_700
    assert references["dailyVwap"] == 10_700
    # Wednesday's current ISO week began on Monday, January 5.
    assert references["weekOpen"] == 10_500
    assert all(
        level["confirmedAt"] <= decision_at
        for level in [*result["supports"], *result["resistances"]]
        if level["confirmedAt"] is not None
    )


def test_first_closed_bar_on_monday_starts_new_utc_week_without_future_data():
    decision_at = int(datetime(2026, 1, 12, 0, 15, tzinfo=timezone.utc).timestamp() * 1000)
    source = {
        "1H": calendar_series(decision_at, "1H", 300),
        "15m": calendar_series(decision_at, "15m", 400),
    }

    result = build_key_levels(source, decision_at, current_price=11_200)
    references = result["references"]

    assert references["previousDayHigh"] == 11_150
    assert references["dayOpen"] == 11_200
    assert references["weekOpen"] == 11_200
    assert references["dailyVwap"] == 11_200
    assert references["weeklyVwap"] == 11_200


def test_exact_utc_week_open_keeps_current_day_and_week_references_missing():
    decision_at = int(datetime(2026, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    source = {
        "1H": calendar_series(decision_at, "1H", 300),
        "15m": calendar_series(decision_at, "15m", 400),
    }

    result = build_key_levels(source, decision_at, current_price=11_150)
    references = result["references"]

    assert references["previousDayHigh"] == 11_150
    assert references["previousDayLow"] == 11_050
    assert references["dayOpen"] is None
    assert references["dailyVwap"] is None
    assert references["weekOpen"] is None
    assert references["weeklyVwap"] is None
