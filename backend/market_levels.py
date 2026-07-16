from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

import numpy as np

from backend.indicators import atr
from backend.market_regime import (
    TIMEFRAME_MS,
    PreparedCandle,
    _anchored_vwap,
    confirmed_swings,
    prepare_candles,
)
from backend.technical import _volume_profile


_SOURCE_STRENGTH = {
    "SWING_4H": 2.5,
    "SWING_1H": 1.75,
    "PREVIOUS_DAY_HIGH": 2.0,
    "PREVIOUS_DAY_LOW": 2.0,
    "DAY_OPEN": 1.0,
    "WEEK_OPEN": 1.5,
    "DAILY_VWAP": 1.25,
    "WEEKLY_VWAP": 1.5,
    "VOLUME_PROFILE_POC": 2.0,
    "VOLUME_PROFILE_VAH": 1.5,
    "VOLUME_PROFILE_VAL": 1.5,
}

_SOURCE_LABELS = {
    "SWING_4H": "4H 确认摆动位",
    "SWING_1H": "1H 确认摆动位",
    "PREVIOUS_DAY_HIGH": "前一日高点",
    "PREVIOUS_DAY_LOW": "前一日低点",
    "DAY_OPEN": "当日开盘",
    "WEEK_OPEN": "周开盘",
    "DAILY_VWAP": "日内 VWAP",
    "WEEKLY_VWAP": "周 VWAP",
    "VOLUME_PROFILE_POC": "成交量分布 POC",
    "VOLUME_PROFILE_VAH": "成交量分布 VAH",
    "VOLUME_PROFILE_VAL": "成交量分布 VAL",
}


def _lookup(candles_by_timeframe: Mapping[str, Iterable[Any]], timeframe: str) -> Iterable[Any]:
    aliases = {
        "1m": ("1m", "candles1m", "candles_1m"),
        "15m": ("15m", "candles15m", "candles_15m"),
        "1H": ("1H", "1h", "candles1h", "candles_1h"),
        "4H": ("4H", "4h", "candles4h", "candles_4h"),
    }
    for key in aliases[timeframe]:
        if key in candles_by_timeframe:
            return candles_by_timeframe[key]
    return []


def _complete_period_rows(
    candles: list[PreparedCandle], start_at: int, cutoff_at: int
) -> list[PreparedCandle]:
    """Return a closed, gap-free period prefix or nothing.

    The caller supplies UTC-natural day/week boundaries.  A partial prefix is
    usable only when it starts at the period open and reaches the latest candle
    that could have closed by ``cutoff_at``.  This prevents a late suffix from
    being mislabeled as the period open or a full-period VWAP.
    """
    if not candles or cutoff_at <= start_at:
        return []
    interval = TIMEFRAME_MS[candles[0].timeframe]
    expected_latest = (cutoff_at // interval) * interval - interval
    if expected_latest < start_at:
        return []
    rows = [
        candle
        for candle in candles
        if start_at <= candle.timestamp <= expected_latest
        and candle.timestamp + interval <= cutoff_at
    ]
    expected_count = (expected_latest - start_at) // interval + 1
    if (
        len(rows) != expected_count
        or rows[0].timestamp != start_at
        or rows[-1].timestamp != expected_latest
        or any(right.timestamp - left.timestamp != interval for left, right in zip(rows, rows[1:]))
    ):
        return []
    return rows


def _preferred_period_rows(
    prepared: Mapping[str, list[PreparedCandle]], start_at: int, cutoff_at: int
) -> list[PreparedCandle]:
    """Preserve the existing 1H reference where complete, then use 15m."""
    for timeframe in ("1H", "15m"):
        rows = _complete_period_rows(prepared[timeframe], start_at, cutoff_at)
        if rows:
            return rows
    return []


def _last_close_at(candles: list[PreparedCandle]) -> int | None:
    if not candles:
        return None
    return candles[-1].timestamp + TIMEFRAME_MS[candles[-1].timeframe]


def _candidate(source: str, price: float | None, confirmed_at: int | None) -> dict[str, Any] | None:
    if price is None or not np.isfinite(price) or price <= 0:
        return None
    return {
        "source": source,
        "price": float(price),
        "strength": _SOURCE_STRENGTH[source],
        "confirmedAt": confirmed_at,
    }


def _cluster(candidates: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    if not candidates:
        return []
    groups: list[list[dict[str, Any]]] = []
    for candidate in sorted(candidates, key=lambda item: item["price"]):
        if not groups:
            groups.append([candidate])
            continue
        current = groups[-1]
        total_weight = sum(item["strength"] for item in current)
        center = sum(item["price"] * item["strength"] for item in current) / total_weight
        if abs(candidate["price"] - center) <= threshold:
            current.append(candidate)
        else:
            groups.append([candidate])
    clustered: list[dict[str, Any]] = []
    for group in groups:
        total_weight = sum(item["strength"] for item in group)
        price = sum(item["price"] * item["strength"] for item in group) / total_weight
        confirmations = [item["confirmedAt"] for item in group if item["confirmedAt"] is not None]
        clustered.append(
            {
                "price": round(float(price), 8),
                "strength": round(min(100.0, total_weight / 5.0 * 100.0), 2),
                "sources": sorted({item["source"] for item in group}),
                "confirmedAt": max(confirmations) if confirmations else None,
                "sourcePrices": [
                    {"source": item["source"], "price": round(item["price"], 8)}
                    for item in sorted(group, key=lambda value: (value["source"], value["price"]))
                ],
            }
        )
    return clustered


def build_key_levels(
    candles_by_timeframe: Mapping[str, Iterable[Any]],
    decision_at: int,
    current_price: float | None = None,
) -> dict[str, Any]:
    """Build deterministic point-in-time levels and ATR-cluster them.

    All source series pass through the same strict future/confirmation filter
    as regime analysis. Volume profile intentionally reuses the existing
    candle-range-uniform implementation on an already-truncated input.
    """
    prepared: dict[str, list[PreparedCandle]] = {}
    quality: dict[str, dict[str, Any]] = {}
    for timeframe in ("1m", "15m", "1H", "4H"):
        series, status = prepare_candles(_lookup(candles_by_timeframe, timeframe), timeframe, decision_at)
        prepared[timeframe] = series
        quality[timeframe] = status

    if current_price is None:
        for timeframe in ("1m", "15m", "1H", "4H"):
            if prepared[timeframe]:
                current_price = prepared[timeframe][-1].close
                break
    if current_price is None or not np.isfinite(current_price) or current_price <= 0:
        return {
            "asOf": decision_at,
            "currentPrice": None,
            "supports": [],
            "resistances": [],
            "nearestSupport": None,
            "nearestResistance": None,
            "references": {},
            "clusterAtr": None,
            "dataQuality": quality,
        }

    candidates: list[dict[str, Any]] = []
    for timeframe in ("4H", "1H"):
        for swing in confirmed_swings(prepared[timeframe], decision_at)[-16:]:
            item = _candidate(f"SWING_{timeframe}", swing["price"], swing["confirmedAt"])
            if item:
                candidates.append(item)

    base = prepared["1H"] or prepared["15m"]
    references: dict[str, Any] = {
        "previousDayHigh": None,
        "previousDayLow": None,
        "dayOpen": None,
        "weekOpen": None,
        "dailyVwap": None,
        "weeklyVwap": None,
        "volumeProfile": {"poc": None, "vah": None, "val": None, "bins": 32, "method": "candle_range_uniform"},
    }
    reference_confirmations: dict[str, int | None] = {}
    if base:
        decision = datetime.fromtimestamp(decision_at / 1000, timezone.utc)
        day_start = datetime(decision.year, decision.month, decision.day, tzinfo=timezone.utc)
        week_start = day_start - timedelta(days=day_start.weekday())
        day_start_at = int(day_start.timestamp() * 1000)
        previous_day_start_at = day_start_at - 86_400_000
        week_start_at = int(week_start.timestamp() * 1000)

        previous_day_candles = _preferred_period_rows(prepared, previous_day_start_at, day_start_at)
        current_day_candles = _preferred_period_rows(prepared, day_start_at, decision_at)
        current_week_candles = _preferred_period_rows(prepared, week_start_at, decision_at)
        previous_day_confirmed_at = _last_close_at(previous_day_candles)
        current_day_confirmed_at = _last_close_at(current_day_candles)
        current_week_confirmed_at = _last_close_at(current_week_candles)
        volume_profile = _volume_profile(base[-120:])
        volume_profile_confirmed_at = _last_close_at(base)
        references.update(
            {
                "previousDayHigh": max((candle.high for candle in previous_day_candles), default=None),
                "previousDayLow": min((candle.low for candle in previous_day_candles), default=None),
                "dayOpen": current_day_candles[0].open if current_day_candles else None,
                "weekOpen": current_week_candles[0].open if current_week_candles else None,
                "dailyVwap": _anchored_vwap(current_day_candles, "day"),
                "weeklyVwap": _anchored_vwap(current_week_candles, "week"),
                "volumeProfile": volume_profile,
            }
        )
        reference_confirmations.update(
            {
                "PREVIOUS_DAY_HIGH": previous_day_confirmed_at,
                "PREVIOUS_DAY_LOW": previous_day_confirmed_at,
                "DAY_OPEN": current_day_confirmed_at,
                "WEEK_OPEN": current_week_confirmed_at,
                "DAILY_VWAP": current_day_confirmed_at,
                "WEEKLY_VWAP": current_week_confirmed_at,
                "VOLUME_PROFILE_POC": volume_profile_confirmed_at,
                "VOLUME_PROFILE_VAH": volume_profile_confirmed_at,
                "VOLUME_PROFILE_VAL": volume_profile_confirmed_at,
            }
        )
        mapping = {
            "PREVIOUS_DAY_HIGH": references["previousDayHigh"],
            "PREVIOUS_DAY_LOW": references["previousDayLow"],
            "DAY_OPEN": references["dayOpen"],
            "WEEK_OPEN": references["weekOpen"],
            "DAILY_VWAP": references["dailyVwap"],
            "WEEKLY_VWAP": references["weeklyVwap"],
            "VOLUME_PROFILE_POC": references["volumeProfile"].get("poc"),
            "VOLUME_PROFILE_VAH": references["volumeProfile"].get("vah"),
            "VOLUME_PROFILE_VAL": references["volumeProfile"].get("val"),
        }
        for source, price in mapping.items():
            item = _candidate(source, price, reference_confirmations.get(source))
            if item:
                candidates.append(item)

    cluster_atr = None
    if base:
        high = np.asarray([candle.high for candle in base], dtype=float)
        low = np.asarray([candle.low for candle in base], dtype=float)
        close = np.asarray([candle.close for candle in base], dtype=float)
        values = atr(high, low, close, 14)
        finite = values[np.isfinite(values)]
        cluster_atr = float(finite[-1]) if len(finite) else None
    threshold = max(float(current_price) * 0.0005, (cluster_atr or 0.0) * 0.35)
    levels = _cluster(candidates, threshold)

    supports: list[dict[str, Any]] = []
    resistances: list[dict[str, Any]] = []
    for index, level in enumerate(levels):
        distance_pct = (level["price"] - current_price) / current_price * 100.0
        sources = list(level.get("sources", []))
        timeframe = "4H" if "SWING_4H" in sources else "1H" if "SWING_1H" in sources else None
        enriched = {
            **level,
            "label": " / ".join(_SOURCE_LABELS.get(source, source) for source in sources),
            "source": sources[0] if sources else "",
            "timeframe": timeframe,
            "strengthLabel": "强" if level["strength"] >= 70 else "中" if level["strength"] >= 40 else "弱",
            "touches": len(level.get("sourcePrices", [])),
            "distancePct": round(distance_pct, 4),
            "distanceAtr": round(abs(level["price"] - current_price) / cluster_atr, 4) if cluster_atr else None,
        }
        if level["price"] <= current_price:
            supports.append({**enriched, "id": f"support-{index}", "kind": "SUPPORT"})
        else:
            resistances.append({**enriched, "id": f"resistance-{index}", "kind": "RESISTANCE"})
    supports = sorted(supports, key=lambda item: item["price"], reverse=True)[:3]
    resistances = sorted(resistances, key=lambda item: item["price"])[:3]

    return {
        "asOf": decision_at,
        "currentPrice": round(float(current_price), 8),
        "supports": supports,
        "resistances": resistances,
        "nearestSupport": supports[0] if supports else None,
        "nearestResistance": resistances[0] if resistances else None,
        "references": references,
        "clusterAtr": round(cluster_atr, 8) if cluster_atr is not None else None,
        "clusterThreshold": round(threshold, 8),
        "dataQuality": quality,
    }
