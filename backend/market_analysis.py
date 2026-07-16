from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Iterable, Mapping

import numpy as np

from backend.indicators import ema
from backend.market_levels import build_key_levels
from backend.market_regime import (
    MODEL_VERSION,
    TIMEFRAME_MS,
    TIMEFRAME_WEIGHTS,
    analyze_timeframe,
    direction_label,
    prepare_candles,
)


TIMEFRAME_ORDER = ("4H", "1H", "15m", "1m")


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


def _external_gap(gap_status: Mapping[str, Any] | None, timeframe: str) -> bool:
    if not gap_status:
        return False
    value = gap_status.get(timeframe)
    if value is None:
        value = gap_status.get(timeframe.lower())
    if isinstance(value, Mapping):
        return bool(value.get("gapDetected", value.get("detected", value.get("gap", False))))
    return bool(value)


def _apply_external_gap(analysis: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(analysis)
    updated["status"] = "INSUFFICIENT_DATA"
    updated["qualityCode"] = "EXTERNAL_GAP_DETECTED"
    updated["directionScore"] = None
    updated["bias"] = "UNKNOWN"
    updated["directionLabel"] = "不可用"
    quality = updated["dataQuality"]
    quality.update(
        {
            "status": "INSUFFICIENT_DATA",
            "qualityCode": "EXTERNAL_GAP_DETECTED",
            "gapDetected": True,
            "details": [*quality.get("details", []), "runtime gap status marks this timeframe incomplete"],
        }
    )
    return updated


def _bias_direction(score: float | None) -> int:
    if score is None or abs(score) < 25:
        return 0
    return 1 if score > 0 else -1


def _alignment(timeframes: Mapping[str, dict[str, Any]], final_score: float | None, hard_conflict: bool) -> dict[str, Any]:
    available = {
        timeframe: analysis["directionScore"]
        for timeframe, analysis in timeframes.items()
        if analysis.get("directionScore") is not None
    }
    available_weight = sum(TIMEFRAME_WEIGHTS[timeframe] for timeframe in available)
    if not available or available_weight <= 0:
        consistency = 0.0
    else:
        dominant = _bias_direction(final_score)
        matched = 0.0
        for timeframe, score in available.items():
            direction = _bias_direction(score)
            weight = TIMEFRAME_WEIGHTS[timeframe]
            if direction == dominant:
                matched += weight
            elif direction == 0 or dominant == 0:
                matched += weight * 0.5
        consistency = matched / available_weight * 100.0
    if hard_conflict:
        status = "HARD_CONFLICT"
    elif not available:
        status = "INSUFFICIENT_DATA"
    elif consistency >= 75:
        status = "ALIGNED"
    else:
        status = "MIXED"
    return {
        "status": status,
        "hardConflict": hard_conflict,
        "consistency": round(consistency, 2),
        "timeframeBiases": {timeframe: timeframes[timeframe]["bias"] for timeframe in TIMEFRAME_ORDER},
        "weights": dict(TIMEFRAME_WEIGHTS),
    }


def _no_chase(
    candles_by_timeframe: Mapping[str, Iterable[Any]],
    decision_at: int,
    direction_score: float | None,
    fifteen_minute: dict[str, Any],
    key_levels: dict[str, Any],
) -> dict[str, Any]:
    prepared, quality = prepare_candles(_lookup(candles_by_timeframe, "15m"), "15m", decision_at)
    direction = _bias_direction(direction_score)
    if direction == 0 or not prepared or quality["status"] != "AVAILABLE":
        return {"noChase": False, "evaluated": False, "timeframe": "15m", "reasons": []}
    indicators = fifteen_minute.get("indicators", {})
    volatility = fifteen_minute.get("volatility", {})
    atr_value = volatility.get("atr")
    ema20 = indicators.get("ema20")
    vwap = indicators.get("dailyVwap")
    rsi_value = indicators.get("rsi")
    latest = prepared[-1]
    reasons: list[str] = []
    if atr_value is not None and atr_value > 0:
        if ema20 is not None and direction * (latest.close - ema20) >= 1.5 * atr_value:
            reasons.append(
                f"15m 收盘 {latest.close:.2f} 相对 EMA20 {ema20:.2f} 已沿主方向偏离 {abs(latest.close-ema20)/atr_value:.2f} ATR（阈值 1.50）。"
            )
        if vwap is not None and direction * (latest.close - vwap) >= 1.5 * atr_value:
            reasons.append(
                f"15m 收盘相对日 VWAP {vwap:.2f} 已沿主方向偏离 {abs(latest.close-vwap)/atr_value:.2f} ATR（阈值 1.50）。"
            )
        candle_range = latest.high - latest.low
        candle_direction = 1 if latest.close > latest.open else -1 if latest.close < latest.open else 0
        if candle_direction == direction and candle_range >= 2.0 * atr_value:
            reasons.append(f"最新已收盘 15m K 线振幅为 {candle_range/atr_value:.2f} ATR（阈值 2.00），属于同向大 K。")
        nearest = key_levels.get("nearestResistance") if direction > 0 else key_levels.get("nearestSupport")
        if nearest is not None:
            distance = direction * (nearest["price"] - latest.close)
            if 0 <= distance <= 0.35 * atr_value:
                side = "阻力" if direction > 0 else "支撑"
                reasons.append(
                    f"前方最近{side} {nearest['price']:.2f} 仅相距 {distance/atr_value:.2f} ATR（阈值 0.35）。"
                )
    if rsi_value is not None and ((direction > 0 and rsi_value >= 70) or (direction < 0 and rsi_value <= 30)):
        threshold = 70 if direction > 0 else 30
        reasons.append(f"15m RSI 为 {rsi_value:.2f}，已越过追价警戒值 {threshold}。")
    return {"noChase": bool(reasons), "evaluated": True, "timeframe": "15m", "reasons": reasons}


def _chart_series(candles_by_timeframe: Mapping[str, Iterable[Any]], decision_at: int) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Return closed-candle indicator overlays; OHLC stays in MarketSnapshot."""
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for timeframe in ("1m", "15m", "1H", "4H"):
        prepared, quality = prepare_candles(_lookup(candles_by_timeframe, timeframe), timeframe, decision_at)
        if quality["qualityCode"] in {"INVALID_CANDLE", "MISALIGNED_TIMESTAMP", "CONFLICTING_DUPLICATE"}:
            prepared = []
        close = np.asarray([candle.close for candle in prepared], dtype=float)

        def line(values: np.ndarray) -> list[dict[str, Any]]:
            return [
                {"timestamp": candle.timestamp, "value": round(float(value), 8)}
                for candle, value in zip(prepared, values)
                if np.isfinite(value)
            ]

        daily_vwap: list[dict[str, Any]] = []
        active_day: tuple[int, int] | None = None
        cumulative_value = 0.0
        cumulative_volume = 0.0
        for candle in prepared:
            instant = datetime.fromtimestamp(candle.timestamp / 1000, timezone.utc)
            day = (instant.year, instant.timetuple().tm_yday)
            if day != active_day:
                active_day = day
                cumulative_value = 0.0
                cumulative_volume = 0.0
            typical = (candle.high + candle.low + candle.close) / 3.0
            cumulative_value += typical * candle.volume
            cumulative_volume += candle.volume
            if cumulative_volume > 0:
                daily_vwap.append({"timestamp": candle.timestamp, "value": round(cumulative_value / cumulative_volume, 8)})
        result[timeframe] = {
            "ema20": line(ema(close, 20)) if len(close) else [],
            "ema50": line(ema(close, 50)) if len(close) else [],
            "ema200": line(ema(close, 200)) if len(close) else [],
            "vwap": daily_vwap,
        }
    return result


def build_market_analysis(
    candles_by_timeframe: Mapping[str, Iterable[Any]],
    decision_at: int,
    connection_status: str = "connected",
    gap_status: Mapping[str, Any] | None = None,
    include_series: bool = True,
) -> dict[str, Any]:
    """Return the complete deterministic v0.6 public market-analysis payload."""
    timeframe_analyses: dict[str, dict[str, Any]] = {}
    for timeframe in TIMEFRAME_ORDER:
        analysis = analyze_timeframe(_lookup(candles_by_timeframe, timeframe), timeframe, decision_at)
        if _external_gap(gap_status, timeframe):
            analysis = _apply_external_gap(analysis)
        timeframe_analyses[timeframe] = analysis

    scores = {
        timeframe: analysis["directionScore"]
        for timeframe, analysis in timeframe_analyses.items()
        if analysis.get("directionScore") is not None
    }
    weighted_coverage = sum(TIMEFRAME_WEIGHTS[timeframe] for timeframe in scores)
    weighted_sum = sum(TIMEFRAME_WEIGHTS[timeframe] * score for timeframe, score in scores.items())
    candidate_score = weighted_sum / weighted_coverage if weighted_coverage > 0 else None
    core_scores = {timeframe: scores[timeframe] for timeframe in ("4H", "1H", "15m") if timeframe in scores}
    core_coverage = sum(TIMEFRAME_WEIGHTS[timeframe] for timeframe in core_scores)
    core_score = (
        sum(TIMEFRAME_WEIGHTS[timeframe] * score for timeframe, score in core_scores.items()) / core_coverage
        if core_coverage > 0
        else None
    )

    four_score = scores.get("4H")
    one_score = scores.get("1H")
    hard_conflict = (
        four_score is not None
        and one_score is not None
        and _bias_direction(four_score) != 0
        and _bias_direction(one_score) != 0
        and _bias_direction(four_score) != _bias_direction(one_score)
    )
    one_minute_guard = False
    final_score = candidate_score
    if candidate_score is not None and core_score is not None:
        candidate_bias, _ = direction_label(candidate_score)
        core_bias, _ = direction_label(core_score)
        if candidate_bias != core_bias:
            final_score = core_score
            one_minute_guard = True

    connected = str(connection_status).lower() == "connected"
    required_available = four_score is not None and one_score is not None
    required_stale = any(timeframe_analyses[timeframe].get("status") == "STALE" for timeframe in ("4H", "1H"))
    if not connected:
        status, quality_code = "STALE", "CONNECTION_NOT_CONNECTED"
        final_score = None
    elif required_stale:
        status, quality_code = "STALE", "REQUIRED_TIMEFRAME_STALE"
        final_score = None
    elif not required_available or weighted_coverage < 0.75:
        status, quality_code = "INSUFFICIENT_DATA", "INSUFFICIENT_TIMEFRAME_COVERAGE"
        final_score = None
    elif hard_conflict:
        status, quality_code = "AVAILABLE", "HARD_4H_1H_CONFLICT"
        final_score = None
    else:
        status = "AVAILABLE"
        quality_code = "OK" if weighted_coverage >= 1.0 else "DEGRADED_TIMEFRAME_COVERAGE"

    if hard_conflict:
        overall_bias, direction_text = "CONFLICT", "4H/1H反向冲突"
    else:
        overall_bias, direction_text = direction_label(final_score)
    alignment = _alignment(timeframe_analyses, final_score if final_score is not None else core_score, hard_conflict)

    data_component = weighted_coverage * 100.0
    alignment_component = alignment["consistency"]
    strength_weight = sum(
        TIMEFRAME_WEIGHTS[timeframe]
        for timeframe in scores
        if timeframe_analyses[timeframe]["trendStrength"].get("score") is not None
    )
    strength_component = (
        sum(
            TIMEFRAME_WEIGHTS[timeframe] * timeframe_analyses[timeframe]["trendStrength"]["score"]
            for timeframe in scores
            if timeframe_analyses[timeframe]["trendStrength"].get("score") is not None
        )
        / strength_weight
        if strength_weight > 0
        else 0.0
    )
    confidence = 0.40 * data_component + 0.35 * alignment_component + 0.25 * strength_component
    if hard_conflict:
        confidence = min(confidence, 20.0)
    if status != "AVAILABLE":
        confidence = 0.0
    confidence = round(float(max(0.0, min(100.0, confidence))), 2)

    current_price = None
    for timeframe in ("1m", "15m", "1H", "4H"):
        series, _ = prepare_candles(_lookup(candles_by_timeframe, timeframe), timeframe, decision_at)
        if series:
            current_price = series[-1].close
            break
    key_levels = build_key_levels(candles_by_timeframe, decision_at, current_price)
    no_chase = _no_chase(
        candles_by_timeframe,
        decision_at,
        final_score,
        timeframe_analyses["15m"],
        key_levels,
    )
    if status != "AVAILABLE" or final_score is None:
        directional_action = "WAIT"
    elif overall_bias in {"STRONG_BULLISH", "BULLISH"}:
        directional_action = "WATCH_LONG"
    elif overall_bias in {"STRONG_BEARISH", "BEARISH"}:
        directional_action = "WATCH_SHORT"
    else:
        directional_action = "WAIT"
    action = "NO_CHASE" if no_chase["noChase"] and directional_action != "WAIT" else directional_action

    warnings: list[str] = []
    if not connected:
        warnings.append("公共行情连接未处于 connected，分析不得用于实时决策。")
    if hard_conflict:
        warnings.append("4H 与 1H 主要方向相反，构成硬冲突。")
    for timeframe in TIMEFRAME_ORDER:
        if timeframe_analyses[timeframe]["status"] != "AVAILABLE":
            if timeframe_analyses[timeframe]["status"] == "STALE":
                warnings.append(f"{timeframe} 最后一根确认 K 线已过期；该周期不参与综合方向，置信度已降低。")
            else:
                warnings.append(f"{timeframe}: {timeframe_analyses[timeframe]['qualityCode']}")

    strength_rows = [
        (TIMEFRAME_WEIGHTS[timeframe], timeframe_analyses[timeframe]["trendStrength"].get("score"))
        for timeframe in TIMEFRAME_ORDER
        if timeframe_analyses[timeframe]["trendStrength"].get("score") is not None
    ]
    strength_total = sum(weight for weight, _ in strength_rows)
    aggregate_strength = (
        sum(weight * float(value) for weight, value in strength_rows) / strength_total
        if strength_total > 0
        else None
    )
    nearest_support = key_levels.get("nearestSupport")
    nearest_resistance = key_levels.get("nearestResistance")
    if final_score is not None and final_score >= 25:
        invalidation_level = nearest_support.get("price") if isinstance(nearest_support, Mapping) else timeframe_analyses["1H"].get("invalidationLevel")
    elif final_score is not None and final_score <= -25:
        invalidation_level = nearest_resistance.get("price") if isinstance(nearest_resistance, Mapping) else timeframe_analyses["1H"].get("invalidationLevel")
    else:
        invalidation_level = None

    supporting_reasons: list[str] = []
    conflicting_reasons: list[str] = []
    final_direction = _bias_direction(final_score)
    for timeframe in TIMEFRAME_ORDER:
        row = timeframe_analyses[timeframe]
        score = row.get("directionScore")
        if score is None:
            continue
        reason = f"{timeframe} {row.get('directionLabel')}（方向分 {float(score):.1f}，结构 {row.get('structure', {}).get('label', 'UNKNOWN')}）。"
        if final_direction and _bias_direction(float(score)) == final_direction:
            supporting_reasons.append(reason)
        elif _bias_direction(float(score)) and _bias_direction(float(score)) != final_direction:
            conflicting_reasons.append(reason)
        conflicting_reasons.extend(str(item) for item in row.get("conflicts", []))
    if hard_conflict:
        conflicting_reasons.insert(0, "4H 与 1H 方向相反，当前不形成可执行方向结论。")
    risk_warnings = list(dict.fromkeys([*warnings, *no_chase["reasons"]]))
    primary_reason = (
        supporting_reasons[0]
        if supporting_reasons
        else conflicting_reasons[0]
        if conflicting_reasons
        else "已收盘数据不足，暂不形成方向结论。"
    )
    if status != "AVAILABLE":
        summary = "行情数据不完整或已过期，当前分析已降级，不应用于实时交易决策。"
    elif hard_conflict:
        summary = "4H 与 1H 主方向冲突，等待多周期重新一致。"
    elif action == "NO_CHASE":
        summary = f"综合{direction_text}，但当前位置已触发不追价条件；等待回撤或新的结构确认。"
    elif directional_action == "WATCH_LONG":
        summary = f"综合{direction_text}，可观察做多环境，但不代表未来一定上涨。"
    elif directional_action == "WATCH_SHORT":
        summary = f"综合{direction_text}，可观察做空环境，但不代表未来一定下跌。"
    else:
        summary = "多周期方向不足以形成明显倾向，当前更适合等待。"

    return {
        "instrument": "BTC-USDT-SWAP",
        "modelVersion": MODEL_VERSION,
        "decisionAt": decision_at,
        "asOf": decision_at,
        "status": status,
        "qualityCode": quality_code,
        "overallBias": overall_bias,
        "directionLabel": direction_text,
        "directionScore": round(final_score, 4) if final_score is not None else None,
        "compositeScore": round(final_score, 4) if final_score is not None else None,
        "alignment": {
            **alignment,
            "weightedCoverage": round(weighted_coverage, 4),
            "coreScoreWithout1m": round(core_score, 4) if core_score is not None else None,
            "weightedScoreBefore1mGuard": round(candidate_score, 4) if candidate_score is not None else None,
            "oneMinuteGuardApplied": one_minute_guard,
        },
        "confidence": confidence,
        "trendStrength": round(float(aggregate_strength), 2) if aggregate_strength is not None else None,
        "alignmentScore": alignment["consistency"],
        "confidenceDetails": {
            "dataCoverage": round(data_component, 2),
            "alignment": round(alignment_component, 2),
            "trendStrength": round(strength_component, 2),
            "formula": "0.40*dataCoverage + 0.35*alignment + 0.25*trendStrength",
        },
        "actionContext": {
            "action": action,
            "directionalAction": directional_action,
            "noChase": no_chase["noChase"],
            "noChaseEvaluated": no_chase["evaluated"],
            "reasons": no_chase["reasons"],
        },
        "actionContextCode": action,
        "primaryReason": primary_reason,
        "summary": summary,
        "invalidationLevel": round(float(invalidation_level), 8) if invalidation_level is not None else None,
        "nearestSupport": nearest_support.get("price") if isinstance(nearest_support, Mapping) else None,
        "nearestResistance": nearest_resistance.get("price") if isinstance(nearest_resistance, Mapping) else None,
        "supportingReasons": supporting_reasons,
        "conflictingReasons": list(dict.fromkeys(conflicting_reasons)),
        "riskWarnings": risk_warnings,
        "currentPrice": current_price,
        "timeframeAnalyses": timeframe_analyses,
        "keyLevels": key_levels,
        "chartSeries": _chart_series(candles_by_timeframe, decision_at) if include_series else {},
        "dataQuality": {
            "status": status,
            "qualityCode": quality_code,
            "connectionStatus": connection_status,
            "decisionAt": decision_at,
            "weightedCoverage": round(weighted_coverage, 4),
            "timeframes": {timeframe: timeframe_analyses[timeframe]["dataQuality"] for timeframe in TIMEFRAME_ORDER},
            "warnings": warnings,
        },
    }


def compact_analysis_snapshot(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a full payload to stable fields suitable for WS change checks."""
    timeframes = analysis.get("timeframeAnalyses", {})
    levels = analysis.get("keyLevels", {})
    return {
        "instrument": analysis.get("instrument", "BTC-USDT-SWAP"),
        "modelVersion": analysis.get("modelVersion"),
        "decisionAt": analysis.get("decisionAt"),
        "asOf": analysis.get("asOf", analysis.get("decisionAt")),
        "status": analysis.get("status"),
        "qualityCode": analysis.get("qualityCode"),
        "overallBias": analysis.get("overallBias"),
        "directionScore": analysis.get("directionScore"),
        "compositeScore": analysis.get("compositeScore", analysis.get("directionScore")),
        "trendStrength": analysis.get("trendStrength"),
        "alignmentScore": analysis.get("alignmentScore"),
        "confidence": analysis.get("confidence"),
        "summary": analysis.get("summary"),
        "primaryReason": analysis.get("primaryReason"),
        "invalidationLevel": analysis.get("invalidationLevel"),
        "nearestSupport": analysis.get("nearestSupport"),
        "nearestResistance": analysis.get("nearestResistance"),
        "alignment": {
            "status": analysis.get("alignment", {}).get("status"),
            "hardConflict": analysis.get("alignment", {}).get("hardConflict"),
            "consistency": analysis.get("alignment", {}).get("consistency"),
        },
        "actionContext": {
            "action": analysis.get("actionContext", {}).get("action"),
            "noChase": analysis.get("actionContext", {}).get("noChase"),
            "reasons": list(analysis.get("actionContext", {}).get("reasons", [])),
        },
        "timeframes": {
            timeframe: {
                "status": timeframes.get(timeframe, {}).get("status"),
                "qualityCode": timeframes.get(timeframe, {}).get("qualityCode"),
                "directionScore": timeframes.get(timeframe, {}).get("directionScore"),
                "bias": timeframes.get(timeframe, {}).get("bias"),
                "regime": timeframes.get(timeframe, {}).get("regime"),
                "trendStrength": timeframes.get(timeframe, {}).get("trendStrength", {}).get("score"),
            }
            for timeframe in TIMEFRAME_ORDER
        },
        "levels": {
            "supports": [item.get("price") for item in levels.get("supports", [])],
            "resistances": [item.get("price") for item in levels.get("resistances", [])],
        },
        "dataQuality": {
            "status": analysis.get("dataQuality", {}).get("status", analysis.get("status")),
            "qualityCode": analysis.get("dataQuality", {}).get("qualityCode", analysis.get("qualityCode")),
            "weightedCoverage": analysis.get("dataQuality", {}).get("weightedCoverage"),
            "warnings": list(analysis.get("dataQuality", {}).get("warnings", [])),
        },
    }


def analysis_changed_significantly(previous: Mapping[str, Any] | None, current: Mapping[str, Any] | None) -> bool:
    """Detect user-visible changes while ignoring routine candle refresh noise."""
    if previous is None or current is None:
        return previous is not current
    old = dict(previous) if "timeframes" in previous and "timeframeAnalyses" not in previous else compact_analysis_snapshot(previous)
    new = dict(current) if "timeframes" in current and "timeframeAnalyses" not in current else compact_analysis_snapshot(current)
    for key in ("modelVersion", "status", "qualityCode", "overallBias"):
        if old.get(key) != new.get(key):
            return True
    for path in (("alignment", "status"), ("alignment", "hardConflict"), ("actionContext", "action"), ("actionContext", "noChase")):
        if old[path[0]].get(path[1]) != new[path[0]].get(path[1]):
            return True
    for key, threshold in (("directionScore", 10.0), ("confidence", 10.0)):
        before, after = old.get(key), new.get(key)
        if (before is None) != (after is None):
            return True
        if before is not None and after is not None and isfinite(float(before)) and isfinite(float(after)):
            if abs(float(after) - float(before)) >= threshold:
                return True
    for timeframe in TIMEFRAME_ORDER:
        for key in ("status", "qualityCode", "bias", "regime"):
            if old["timeframes"][timeframe].get(key) != new["timeframes"][timeframe].get(key):
                return True
    return False
