from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

import numpy as np

from backend.indicators import atr, ema, macd, obv, wilder


MODEL_VERSION = "indicator-regime-v06.0.0"
TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "15m": 15 * 60_000,
    "1H": 60 * 60_000,
    "4H": 4 * 60 * 60_000,
}
TIMEFRAME_WEIGHTS: dict[str, float] = {"4H": 0.40, "1H": 0.35, "15m": 0.20, "1m": 0.05}
CONTRIBUTION_WEIGHTS: dict[str, float] = {
    "swingStructure": 30.0,
    "ema": 20.0,
    "macd": 15.0,
    "rsi": 10.0,
    "dmiDirection": 10.0,
    "vwap": 5.0,
    "obvRelativeVolume": 10.0,
}
CONTRIBUTION_LABELS: dict[str, str] = {
    "swingStructure": "市场结构",
    "ema": "EMA 排列与斜率",
    "macd": "MACD",
    "rsi": "RSI14",
    "dmiDirection": "DMI 方向",
    "vwap": "日内 VWAP",
    "obvRelativeVolume": "OBV 与相对成交量",
}


@dataclass(frozen=True)
class PreparedCandle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str
    confirm: bool = True


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _quality(
    status: str,
    code: str,
    *,
    timeframe: str,
    decision_at: int,
    input_count: int,
    confirmed_count: int = 0,
    excluded_unconfirmed: int = 0,
    excluded_future: int = 0,
    duplicate_count: int = 0,
    contiguous_count: int = 0,
    gap_detected: bool = False,
    last_open_at: int | None = None,
    details: list[str] | None = None,
) -> dict[str, Any]:
    interval = TIMEFRAME_MS[timeframe]
    return {
        "status": status,
        "qualityCode": code,
        "timeframe": timeframe,
        "decisionAt": decision_at,
        "inputCount": input_count,
        "confirmedCount": confirmed_count,
        "excludedUnconfirmed": excluded_unconfirmed,
        "excludedFuture": excluded_future,
        "exactDuplicatesRemoved": duplicate_count,
        "contiguousCount": contiguous_count,
        "maxLookback": 400,
        "gapDetected": gap_detected,
        "lastOpenAt": last_open_at,
        "lastConfirmedAt": last_open_at + interval if last_open_at is not None else None,
        "details": details or [],
    }


def prepare_candles(
    candles: Iterable[Any], timeframe: str, decision_at: int
) -> tuple[list[PreparedCandle], dict[str, Any]]:
    """Create the strict point-in-time, contiguous input used by v0.6.

    Future and unconfirmed rows are removed before value validation. Exact
    duplicates are harmless; two different rows for the same open timestamp
    make the timeframe unavailable rather than choosing one nondeterministically.
    """
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    if isinstance(decision_at, bool) or not isinstance(decision_at, int) or decision_at < 0:
        raise ValueError("decision_at must be a non-negative integer timestamp in milliseconds")

    source = list(candles)
    interval = TIMEFRAME_MS[timeframe]
    eligible: list[Any] = []
    excluded_unconfirmed = 0
    excluded_future = 0
    early_errors: list[str] = []

    # This ordering is intentional: bad values in a candle that was not yet
    # knowable at decision_at must not contaminate a historical snapshot.
    for row in source:
        if _field(row, "confirm", True) is not True:
            excluded_unconfirmed += 1
            continue
        raw_timestamp = _field(row, "timestamp")
        if isinstance(raw_timestamp, bool):
            early_errors.append("timestamp must be an integer")
            eligible.append(row)
            continue
        try:
            timestamp = int(raw_timestamp)
        except (TypeError, ValueError, OverflowError):
            early_errors.append("timestamp must be an integer")
            eligible.append(row)
            continue
        if timestamp + interval > decision_at:
            excluded_future += 1
            continue
        eligible.append(row)

    normalized: list[PreparedCandle] = []
    validation_errors = list(early_errors)
    for row in eligible:
        raw_timestamp = _field(row, "timestamp")
        try:
            timestamp = int(raw_timestamp)
            numeric_timestamp = float(raw_timestamp)
            values = tuple(float(_field(row, name)) for name in ("open", "high", "low", "close", "volume"))
        except (TypeError, ValueError, OverflowError):
            validation_errors.append("candle fields must be finite numbers")
            continue
        open_, high, low, close, volume = values
        row_timeframe = _field(row, "timeframe", timeframe)
        if not isfinite(numeric_timestamp) or numeric_timestamp != timestamp:
            validation_errors.append(f"misaligned timestamp at {raw_timestamp}")
        elif not all(isfinite(value) for value in values):
            validation_errors.append(f"non-finite candle at {timestamp}")
        elif timestamp < 0 or timestamp % interval != 0:
            validation_errors.append(f"misaligned timestamp at {timestamp}")
        elif row_timeframe != timeframe:
            validation_errors.append(f"timeframe mismatch at {timestamp}: {row_timeframe}")
        elif min(open_, high, low, close) <= 0:
            validation_errors.append(f"non-positive price at {timestamp}")
        elif volume < 0:
            validation_errors.append(f"negative volume at {timestamp}")
        elif high < max(open_, low, close) or low > min(open_, high, close):
            validation_errors.append(f"invalid OHLC ordering at {timestamp}")
        else:
            normalized.append(PreparedCandle(timestamp, open_, high, low, close, volume, timeframe))

    base_kwargs = {
        "timeframe": timeframe,
        "decision_at": decision_at,
        "input_count": len(source),
        "confirmed_count": len(eligible),
        "excluded_unconfirmed": excluded_unconfirmed,
        "excluded_future": excluded_future,
    }
    if validation_errors:
        code = "MISALIGNED_TIMESTAMP" if any("misaligned" in item for item in validation_errors) else "INVALID_CANDLE"
        return [], _quality("INSUFFICIENT_DATA", code, details=validation_errors[:20], **base_kwargs)

    by_timestamp: dict[int, PreparedCandle] = {}
    duplicate_count = 0
    conflicts: list[int] = []
    for candle in normalized:
        existing = by_timestamp.get(candle.timestamp)
        if existing is None:
            by_timestamp[candle.timestamp] = candle
        elif existing == candle:
            duplicate_count += 1
        else:
            conflicts.append(candle.timestamp)
    if conflicts:
        return [], _quality(
            "INSUFFICIENT_DATA",
            "CONFLICTING_DUPLICATE",
            duplicate_count=duplicate_count,
            details=[f"conflicting duplicate at {timestamp}" for timestamp in sorted(set(conflicts))[:20]],
            **base_kwargs,
        )

    ordered = [by_timestamp[key] for key in sorted(by_timestamp)]
    if not ordered:
        return [], _quality(
            "INSUFFICIENT_DATA", "NO_CONFIRMED_CANDLES", duplicate_count=duplicate_count, **base_kwargs
        )

    suffix_start = len(ordered) - 1
    while suffix_start > 0 and ordered[suffix_start].timestamp - ordered[suffix_start - 1].timestamp == interval:
        suffix_start -= 1
    gap_detected = suffix_start > 0
    suffix = ordered[suffix_start:][-400:]
    latest = suffix[-1].timestamp
    expected_latest = (decision_at // interval) * interval - interval
    common = dict(
        duplicate_count=duplicate_count,
        contiguous_count=len(suffix),
        gap_detected=gap_detected,
        last_open_at=latest,
        **base_kwargs,
    )
    if len(suffix) < 200:
        return suffix, _quality(
            "INSUFFICIENT_DATA",
            "INSUFFICIENT_CONTIGUOUS_CANDLES",
            details=[f"need 200 contiguous candles; found {len(suffix)}"],
            **common,
        )
    if latest < expected_latest:
        return suffix, _quality(
            "STALE",
            "LATEST_CANDLE_STALE",
            details=[f"latest open {latest} is older than expected {expected_latest}"],
            **common,
        )
    code = "OK_WITH_OLDER_GAPS" if gap_detected else "OK"
    details = ["older data before the usable contiguous suffix contains a gap"] if gap_detected else []
    return suffix, _quality("AVAILABLE", code, details=details, **common)


def flat_neutral_rsi(values: Iterable[float], period: int = 14) -> np.ndarray:
    """Wilder RSI where a completely flat market is neutral (50), not zero."""
    close = np.asarray(list(values), dtype=float)
    out = np.full(len(close), np.nan)
    if len(close) < 2:
        return out
    delta = np.diff(close)
    average_gain = wilder(np.where(delta > 0, delta, 0.0), period)
    average_loss = wilder(np.where(delta < 0, -delta, 0.0), period)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = average_gain / average_loss
        value = 100.0 - 100.0 / (1.0 + ratio)
    value = np.where((average_loss == 0) & (average_gain > 0), 100.0, value)
    value = np.where((average_loss == 0) & (average_gain == 0), 50.0, value)
    out[1:] = value
    return out


def dmi_adx(
    high: Iterable[float], low: Iterable[float], close: Iterable[float], period: int = 14
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return ADX, +DI and -DI using the repository's Wilder smoothing."""
    high_values = np.asarray(list(high), dtype=float)
    low_values = np.asarray(list(low), dtype=float)
    close_values = np.asarray(list(close), dtype=float)
    length = len(close_values)
    plus_di = np.full(length, np.nan)
    minus_di = np.full(length, np.nan)
    adx_values = np.full(length, np.nan)
    if length < 2:
        return adx_values, plus_di, minus_di
    up = np.diff(high_values)
    down = -np.diff(low_values)
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr_values = atr(high_values, low_values, close_values, period)
    smooth_plus = wilder(plus_dm, period)
    smooth_minus = wilder(minus_dm, period)
    denominator = atr_values[1:]
    plus = np.full_like(smooth_plus, np.nan)
    minus = np.full_like(smooth_minus, np.nan)
    finite = np.isfinite(smooth_plus) & np.isfinite(smooth_minus) & np.isfinite(denominator)
    plus[finite] = 0.0
    minus[finite] = 0.0
    positive_atr = finite & (denominator > 0)
    plus[positive_atr] = 100.0 * smooth_plus[positive_atr] / denominator[positive_atr]
    minus[positive_atr] = 100.0 * smooth_minus[positive_atr] / denominator[positive_atr]
    plus_di[1:] = plus
    minus_di[1:] = minus
    total = plus + minus
    dx = np.full_like(total, np.nan)
    valid_di = np.isfinite(plus) & np.isfinite(minus)
    dx[valid_di] = 0.0
    directional = valid_di & (total > 0)
    dx[directional] = 100.0 * np.abs(plus[directional] - minus[directional]) / total[directional]
    adx_values[1:] = wilder(dx, period)
    return adx_values, plus_di, minus_di


def confirmed_swings(
    candles: list[PreparedCandle], decision_at: int, left: int = 2, right: int = 2
) -> list[dict[str, Any]]:
    swings: list[dict[str, Any]] = []
    if not candles:
        return swings
    interval = TIMEFRAME_MS[candles[0].timeframe]
    for index in range(left, len(candles) - right):
        confirmation_at = candles[index + right].timestamp + interval
        if confirmation_at > decision_at:
            continue
        current = candles[index]
        neighbors = candles[index - left : index] + candles[index + 1 : index + right + 1]
        if all(current.high > candle.high for candle in neighbors):
            swings.append(
                {"kind": "HIGH", "price": current.high, "timestamp": current.timestamp, "confirmedAt": confirmation_at}
            )
        if all(current.low < candle.low for candle in neighbors):
            swings.append(
                {"kind": "LOW", "price": current.low, "timestamp": current.timestamp, "confirmedAt": confirmation_at}
            )
    return swings


def _arrays(candles: list[PreparedCandle]) -> tuple[np.ndarray, ...]:
    return tuple(
        np.asarray([getattr(candle, field) for candle in candles], dtype=float)
        for field in ("open", "high", "low", "close", "volume")
    )


def _finite_last(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    return float(finite[-1]) if len(finite) else None


def _sign(value: float, epsilon: float = 1e-12) -> float:
    return 1.0 if value > epsilon else -1.0 if value < -epsilon else 0.0


def _anchored_vwap(candles: list[PreparedCandle], anchor: str) -> float | None:
    if not candles:
        return None
    from datetime import datetime, timezone

    last = datetime.fromtimestamp(candles[-1].timestamp / 1000, timezone.utc)
    if anchor == "day":
        key = (last.year, last.timetuple().tm_yday)
        matches = lambda value: (value.year, value.timetuple().tm_yday) == key
    else:
        iso = last.isocalendar()
        key = (iso.year, iso.week)
        matches = lambda value: (value.isocalendar().year, value.isocalendar().week) == key
    selected = [
        candle
        for candle in candles
        if matches(datetime.fromtimestamp(candle.timestamp / 1000, timezone.utc))
    ]
    total_volume = sum(candle.volume for candle in selected)
    if total_volume <= 0:
        return None
    return sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in selected) / total_volume


def direction_label(score: float | None) -> tuple[str, str]:
    if score is None:
        return "UNKNOWN", "不可用"
    if score >= 60:
        return "STRONG_BULLISH", "强多"
    if score >= 25:
        return "BULLISH", "多"
    if score <= -60:
        return "STRONG_BEARISH", "强空"
    if score <= -25:
        return "BEARISH", "空"
    return "NEUTRAL", "中性"


def _contribution(
    name: str,
    weight: float,
    score: float | None,
    value: Any,
    explanation: str,
) -> dict[str, Any]:
    available = score is not None and isfinite(float(score))
    bounded = round(float(np.clip(score, -weight, weight)), 4) if available else 0.0
    return {
        "key": name,
        "name": name,
        "label": CONTRIBUTION_LABELS.get(name, name),
        "weight": weight,
        "available": available,
        "score": bounded,
        "value": value,
        "explanation": explanation,
    }


def _empty_analysis(timeframe: str, quality: dict[str, Any]) -> dict[str, Any]:
    bias, label = direction_label(None)
    return {
        "timeframe": timeframe,
        "status": quality["status"],
        "qualityCode": quality["qualityCode"],
        "asOf": quality.get("lastConfirmedAt"),
        "coverage": 0.0,
        "availableWeight": 0.0,
        "rawDirectionScore": None,
        "directionScore": None,
        "direction": bias,
        "bias": bias,
        "directionLabel": label,
        "regime": "UNKNOWN",
        "contributions": [],
        "structure": {"direction": "UNKNOWN", "label": "UNKNOWN", "latestSwingHigh": None, "latestSwingLow": None, "swings": []},
        "trendStrength": {"score": None, "components": {}},
        "volatilityState": "UNKNOWN",
        "extendedState": "UNKNOWN",
        "keySupport": None,
        "keyResistance": None,
        "invalidationLevel": None,
        "summary": "已收盘 K 线或指标覆盖不足，当前周期不输出方向判断。",
        "primaryReason": "数据不足",
        "conflicts": [],
        "warnings": list(quality.get("details", [])),
        "indicators": {},
        "volatility": {"atr": None, "atrPercentile": None, "baselineCount": 0, "baselineExcludesCurrent": True},
        "dataQuality": quality,
    }


def analyze_timeframe(candles: Iterable[Any], timeframe: str, decision_at: int) -> dict[str, Any]:
    prepared, quality = prepare_candles(candles, timeframe, decision_at)
    if len(prepared) < 200 or quality["status"] not in {"AVAILABLE", "STALE"}:
        return _empty_analysis(timeframe, quality)

    open_, high, low, close, volume = _arrays(prepared)
    atr_values = atr(high, low, close, 14)
    atr_value = _finite_last(atr_values)
    ema20_values, ema50_values, ema200_values = ema(close, 20), ema(close, 50), ema(close, 200)
    ema20_value, ema50_value, ema200_value = (
        _finite_last(ema20_values),
        _finite_last(ema50_values),
        _finite_last(ema200_values),
    )
    macd_line, macd_signal, macd_histogram = macd(close)
    macd_value, signal_value, histogram_value = (
        _finite_last(macd_line),
        _finite_last(macd_signal),
        _finite_last(macd_histogram),
    )
    rsi_values = flat_neutral_rsi(close)
    rsi_value = _finite_last(rsi_values)
    adx_values, plus_di_values, minus_di_values = dmi_adx(high, low, close)
    adx_value = _finite_last(adx_values)
    plus_di, minus_di = _finite_last(plus_di_values), _finite_last(minus_di_values)
    daily_vwap = _anchored_vwap(prepared, "day")
    weekly_vwap = _anchored_vwap(prepared, "week")
    obv_values = obv(close, volume)
    relative_volume = None
    if len(volume) >= 21:
        baseline_volume = float(np.mean(volume[-21:-1]))
        relative_volume = float(volume[-1] / baseline_volume) if baseline_volume > 0 else None

    swings = confirmed_swings(prepared, decision_at)
    swing_highs = [item for item in swings if item["kind"] == "HIGH"]
    swing_lows = [item for item in swings if item["kind"] == "LOW"]
    structure_direction = "UNKNOWN"
    structure_label = "UNKNOWN"
    structure_score: float | None = None
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        high_direction = _sign(swing_highs[-1]["price"] - swing_highs[-2]["price"])
        low_direction = _sign(swing_lows[-1]["price"] - swing_lows[-2]["price"])
        structure_score = 15.0 * high_direction + 15.0 * low_direction
        if high_direction > 0 and low_direction > 0:
            structure_direction, structure_label = "BULLISH", "HH_HL"
        elif high_direction < 0 and low_direction < 0:
            structure_direction, structure_label = "BEARISH", "LH_LL"
        elif high_direction == 0 and low_direction == 0:
            structure_direction, structure_label = "NEUTRAL", "RANGE"
        else:
            structure_direction, structure_label = "MIXED", "TRANSITION"

    contributions: list[dict[str, Any]] = []
    contributions.append(
        _contribution(
            "swingStructure",
            30.0,
            structure_score,
            {"latestHigh": swing_highs[-1]["price"] if swing_highs else None, "latestLow": swing_lows[-1]["price"] if swing_lows else None},
            "最近两个已确认 swing 高点和低点各贡献 15 分；右侧两根均已在 decisionAt 前收盘。",
        )
    )

    ema_score = None
    ema20_slope = None
    ema50_slope = None
    if len(close) >= 6 and atr_value is not None and atr_value > 0:
        if np.isfinite(ema20_values[-6]) and ema20_value is not None:
            ema20_slope = (ema20_value - float(ema20_values[-6])) / atr_value
        if np.isfinite(ema50_values[-6]) and ema50_value is not None:
            ema50_slope = (ema50_value - float(ema50_values[-6])) / atr_value
    if ema20_value is not None and ema50_value is not None:
        ema_score = (
            5.0 * _sign(close[-1] - ema20_value)
            + 5.0 * _sign(ema20_value - ema50_value)
            + 5.0 * _sign(ema20_slope or 0.0)
            + 5.0 * _sign(ema50_slope or 0.0)
        )
    contributions.append(
        _contribution(
            "ema",
            20.0,
            ema_score,
            {"ema20": ema20_value, "ema50": ema50_value, "ema200": ema200_value, "ema20SlopeAtr5": ema20_slope, "ema50SlopeAtr5": ema50_slope},
            "收盘/EMA20、EMA20/EMA50 排列以及 EMA20、EMA50 五根斜率各贡献 5 分。",
        )
    )

    macd_score = None
    if macd_value is not None and signal_value is not None:
        macd_score = 15.0 * _sign(macd_value - signal_value)
    contributions.append(
        _contribution(
            "macd",
            15.0,
            macd_score,
            {"line": macd_value, "signal": signal_value, "histogram": histogram_value, "histogramRising": bool(len(macd_histogram) >= 2 and np.isfinite(macd_histogram[-2]) and histogram_value is not None and histogram_value > macd_histogram[-2])},
            "MACD 线高于信号线为正，低于信号线为负。",
        )
    )

    rsi_score = 10.0 * float(np.clip((rsi_value - 50.0) / 20.0, -1.0, 1.0)) if rsi_value is not None else None
    contributions.append(
        _contribution("rsi", 10.0, rsi_score, rsi_value, "RSI 50 为中性，30/70 对应方向贡献上下限；平盘 RSI 固定为 50。")
    )

    dmi_score = None
    if plus_di is not None and minus_di is not None:
        total_di = plus_di + minus_di
        dmi_score = 10.0 * (plus_di - minus_di) / total_di if total_di > 0 else 0.0
    contributions.append(
        _contribution(
            "dmiDirection",
            10.0,
            dmi_score,
            {"plusDi": plus_di, "minusDi": minus_di},
            "+DI 与 -DI 的归一化差值决定方向；ADX 不参与方向分。",
        )
    )

    vwap_score = 5.0 * _sign(close[-1] - daily_vwap) if daily_vwap is not None else None
    contributions.append(
        _contribution(
            "vwap",
            5.0,
            vwap_score,
            {"daily": daily_vwap, "weekly": weekly_vwap},
            "最新收盘相对当前 UTC 日 VWAP 决定方向。",
        )
    )

    volume_score = None
    if relative_volume is not None and len(obv_values) >= 11:
        obv_vote = _sign(obv_values[-1] - obv_values[-11])
        price_vote = _sign(close[-1] - close[-2])
        relative_intensity = float(np.clip(relative_volume, 0.0, 2.0) / 2.0)
        volume_score = 5.0 * obv_vote + 5.0 * price_vote * relative_intensity
    contributions.append(
        _contribution(
            "obvRelativeVolume",
            10.0,
            volume_score,
            {"obv": float(obv_values[-1]), "relativeVolume": relative_volume},
            "OBV 十根变化贡献 5 分，当前价格方向按相对成交量强度贡献最多 5 分。",
        )
    )

    available_weight = sum(item["weight"] for item in contributions if item["available"])
    coverage = round(available_weight / 100.0, 4)
    raw_score = round(sum(item["score"] for item in contributions if item["available"]), 4)
    publishable = quality["status"] == "AVAILABLE" and coverage >= 0.8
    direction_score = raw_score if publishable else None
    if quality["status"] == "AVAILABLE" and coverage < 0.8:
        quality = {**quality, "status": "INSUFFICIENT_DATA", "qualityCode": "LOW_INDICATOR_COVERAGE", "details": [*quality["details"], f"indicator coverage {coverage:.2f} is below 0.80"]}
    bias, label = direction_label(direction_score)

    previous_atr = atr_values[:-1]
    previous_close = close[:-1]
    prior_ratios = np.divide(
        previous_atr,
        previous_close,
        out=np.full(len(previous_atr), np.nan),
        where=previous_close > 0,
    )
    current_ratio = atr_value / close[-1] if atr_value is not None else None
    baseline = prior_ratios[np.isfinite(prior_ratios)][-100:]
    atr_percentile = float(np.mean(baseline <= current_ratio) * 100.0) if current_ratio is not None and len(baseline) else None

    adx_strength = float(np.clip((adx_value or 0.0) / 50.0 * 100.0, 0.0, 100.0))
    adx_rising = bool(len(adx_values) >= 6 and np.isfinite(adx_values[-6]) and adx_value is not None and adx_value > adx_values[-6])
    ema_separation = (
        float(np.clip(abs(ema20_value - ema50_value) / atr_value * 25.0, 0.0, 100.0))
        if None not in (ema20_value, ema50_value, atr_value) and atr_value > 0
        else 0.0
    )
    structure_persistence = 100.0 if structure_direction in {"BULLISH", "BEARISH"} else 40.0 if structure_direction == "MIXED" else 10.0 if structure_direction == "NEUTRAL" else 0.0
    ema_slope_strength = float(np.clip(max(abs(ema20_slope or 0.0), abs(ema50_slope or 0.0)) * 100.0, 0.0, 100.0))
    if len(close) >= 25:
        direction = _sign(close[-1] - close[-6])
        prior_high = float(np.max(high[-25:-5]))
        prior_low = float(np.min(low[-25:-5]))
        if direction > 0:
            breakout_persistence = float(np.mean(close[-5:] > prior_high) * 100.0)
        elif direction < 0:
            breakout_persistence = float(np.mean(close[-5:] < prior_low) * 100.0)
        else:
            breakout_persistence = 0.0
    else:
        breakout_persistence = 0.0
    volume_confirmation = float(np.clip((relative_volume or 0.0) / 1.5 * 100.0, 0.0, 100.0))
    volatility_support = 100.0 if atr_percentile is not None and 30 <= atr_percentile <= 90 else 50.0 if atr_percentile is not None and 15 <= atr_percentile <= 97 else 10.0
    trend_strength = round(
        0.30 * adx_strength
        + 0.20 * ema_slope_strength
        + 0.20 * structure_persistence
        + 0.10 * breakout_persistence
        + 0.10 * volume_confirmation
        + 0.10 * volatility_support,
        2,
    )
    regime = "TREND" if (adx_value or 0) >= 25 and trend_strength >= 50 else "RANGE" if (adx_value or 0) <= 18 and ema_separation <= 25 else "TRANSITION"

    if atr_percentile is None:
        volatility_state = "UNKNOWN"
    elif atr_percentile < 20:
        volatility_state = "COMPRESSION"
    elif atr_percentile <= 70:
        volatility_state = "NORMAL"
    elif atr_percentile <= 90:
        volatility_state = "EXPANSION"
    else:
        volatility_state = "EXTREME"
    ema_distance_atr = (close[-1] - ema20_value) / atr_value if ema20_value is not None and atr_value is not None and atr_value > 0 else None
    extended_state = "EXTENDED_UP" if ema_distance_atr is not None and ema_distance_atr >= 1.5 else "EXTENDED_DOWN" if ema_distance_atr is not None and ema_distance_atr <= -1.5 else "NOT_EXTENDED" if ema_distance_atr is not None else "UNKNOWN"
    conflicts: list[str] = []
    if rsi_value is not None and direction_score is not None:
        if direction_score >= 25 and rsi_value < 45:
            conflicts.append("方向偏多，但 RSI14 低于 45。")
        elif direction_score <= -25 and rsi_value > 55:
            conflicts.append("方向偏空，但 RSI14 高于 55。")
    primary_reason = f"方向贡献 {direction_score:.1f}/100，结构 {structure_label}，ADX {adx_value:.1f}。" if direction_score is not None and adx_value is not None else "已收盘数据不足以形成完整解释。"

    return {
        "timeframe": timeframe,
        "status": quality["status"],
        "qualityCode": quality["qualityCode"],
        "asOf": prepared[-1].timestamp + TIMEFRAME_MS[timeframe],
        "coverage": coverage,
        "availableWeight": available_weight,
        "rawDirectionScore": raw_score,
        "directionScore": direction_score,
        "direction": bias,
        "bias": bias,
        "directionLabel": label,
        "regime": regime,
        "contributions": contributions,
        "structure": {
            "direction": structure_direction,
            "label": structure_label,
            "latestSwingHigh": swing_highs[-1] if swing_highs else None,
            "latestSwingLow": swing_lows[-1] if swing_lows else None,
            "swings": swings[-12:],
        },
        "trendStrength": {
            "score": trend_strength,
            "components": {
                "adx": {"value": adx_value, "rising": adx_rising, "normalized": round(adx_strength, 2), "weight": 0.30},
                "emaSlope": {"normalized": round(ema_slope_strength, 2), "weight": 0.20},
                "structurePersistence": {"normalized": structure_persistence, "weight": 0.20},
                "breakoutPersistence": {"normalized": round(breakout_persistence, 2), "weight": 0.10},
                "volumeConfirmation": {"normalized": round(volume_confirmation, 2), "weight": 0.10},
                "volatilitySupport": {"normalized": round(volatility_support, 2), "weight": 0.10},
            },
        },
        "volatilityState": volatility_state,
        "extendedState": extended_state,
        "keySupport": swing_lows[-1]["price"] if swing_lows else None,
        "keyResistance": swing_highs[-1]["price"] if swing_highs else None,
        "invalidationLevel": swing_lows[-1]["price"] if direction_score is not None and direction_score >= 25 and swing_lows else swing_highs[-1]["price"] if direction_score is not None and direction_score <= -25 and swing_highs else None,
        "summary": primary_reason,
        "primaryReason": primary_reason,
        "conflicts": conflicts,
        "warnings": list(quality.get("details", [])),
        "indicators": {
            "ema20": ema20_value,
            "ema50": ema50_value,
            "ema200": ema200_value,
            "ema20SlopeAtr5": ema20_slope,
            "ema50SlopeAtr5": ema50_slope,
            "macd": macd_value,
            "macdSignal": signal_value,
            "macdHistogram": histogram_value,
            "rsi": rsi_value,
            "adx": adx_value,
            "plusDi": plus_di,
            "minusDi": minus_di,
            "dailyVwap": daily_vwap,
            "weeklyVwap": weekly_vwap,
            "obv": float(obv_values[-1]),
            "relativeVolume": relative_volume,
        },
        "volatility": {
            "atr": atr_value,
            "state": volatility_state,
            "ema20DistanceAtr": ema_distance_atr,
            "atrPercentile": round(atr_percentile, 2) if atr_percentile is not None else None,
            "baselineCount": int(len(baseline)),
            "baselineExcludesCurrent": True,
        },
        "dataQuality": quality,
    }


# A descriptive alias for callers that treat the single-timeframe result as a
# market-regime analysis rather than an indicator implementation detail.
analyze_market_regime = analyze_timeframe
