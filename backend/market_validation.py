from __future__ import annotations

import math
import time
from bisect import bisect_right
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .models import Candle


TIMEFRAME_MS = {
    "1m": 60_000,
    "15m": 15 * 60_000,
    "1H": 60 * 60_000,
    "4H": 4 * 60 * 60_000,
}
HORIZONS_MS = {
    "15m": TIMEFRAME_MS["15m"],
    "1h": TIMEFRAME_MS["1H"],
    "4h": TIMEFRAME_MS["4H"],
    "24h": 24 * TIMEFRAME_MS["1H"],
}
HAC_LAGS_15M = {"15m": 1, "1h": 4, "4h": 16, "24h": 96}
PURGE_MS = HORIZONS_MS["24h"]
MIN_STATISTICAL_SAMPLES = 30
MIN_INDEPENDENT_OOS_WINDOWS = 4
FEATURE_LOOKBACK = {"1m": 2_000, "15m": 800, "1H": 400, "4H": 400}
REQUIRED_CONTIGUOUS_BARS = {"1m": 200, "15m": 200, "1H": 200, "4H": 200}


def _build_market_analysis(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Import lazily so the validation module remains independently testable."""
    from .market_analysis import build_market_analysis

    return build_market_analysis(*args, **kwargs)


def _raw_value(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _candle_signature(candle: Candle) -> tuple[Any, ...]:
    return (
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.volume,
        candle.volume_ccy,
        candle.confirm,
    )


def _clean_series(rows: Sequence[Any], timeframe: str, generated_at: int) -> tuple[list[Candle], dict[str, int]]:
    step = TIMEFRAME_MS[timeframe]
    by_timestamp: dict[int, Candle] = {}
    conflicts: set[int] = set()
    invalid = unconfirmed = future = 0
    for raw in rows:
        try:
            timestamp = int(_raw_value(raw, "timestamp", "ts"))
            confirmed = bool(_raw_value(raw, "confirm", default=True))
            values = tuple(_finite(_raw_value(raw, name)) for name in ("open", "high", "low", "close", "volume"))
            volume_ccy = _finite(_raw_value(raw, "volume_ccy", "volumeCcy"))
            if not confirmed:
                unconfirmed += 1
                continue
            if timestamp + step > generated_at:
                future += 1
                continue
            if any(value is None for value in values):
                invalid += 1
                continue
            open_price, high, low, close, volume = (float(value) for value in values)
            if (
                timestamp < 0
                or timestamp % step != 0
                or min(open_price, high, low, close) <= 0
                or volume < 0
                or low > min(open_price, close)
                or max(open_price, close) > high
            ):
                invalid += 1
                continue
            candle = Candle(
                timestamp=timestamp,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                volume_ccy=volume_ccy,
                confirm=True,
                timeframe=timeframe,
            )
        except (TypeError, ValueError, OverflowError):
            invalid += 1
            continue
        previous = by_timestamp.get(timestamp)
        if previous is None:
            by_timestamp[timestamp] = candle
        elif _candle_signature(previous) != _candle_signature(candle):
            conflicts.add(timestamp)
    for timestamp in conflicts:
        by_timestamp.pop(timestamp, None)
    clean = [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]
    gaps = sum(1 for left, right in zip(clean, clean[1:]) if right.timestamp - left.timestamp != step)
    return clean, {
        "invalid": invalid,
        "unconfirmed": unconfirmed,
        "futureExcluded": future,
        "duplicateConflicts": len(conflicts),
        "gapEdges": gaps,
    }


def _timeframe_rows(candles_by_timeframe: Mapping[str, Sequence[Any]], timeframe: str) -> Sequence[Any]:
    aliases = {
        "1m": ("1m", "candles1m", "candles_1m"),
        "15m": ("15m", "candles15m", "candles_15m"),
        "1H": ("1H", "1h", "candles1h", "candles_1h"),
        "4H": ("4H", "4h", "candles4h", "candles_4h"),
    }
    for name in aliases[timeframe]:
        value = candles_by_timeframe.get(name)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return value
    return ()


def _continuity_metadata(series: list[Candle], timeframe: str) -> tuple[list[int], list[int]]:
    step = TIMEFRAME_MS[timeframe]
    closed_at = [row.timestamp + step for row in series]
    run_lengths: list[int] = []
    run = 0
    previous: int | None = None
    for row in series:
        run = run + 1 if previous is not None and row.timestamp - previous == step else 1
        run_lengths.append(run)
        previous = row.timestamp
    return closed_at, run_lengths


def _feature_available(
    decision_at: int,
    timeframe: str,
    closed_at: list[int],
    run_lengths: list[int],
) -> bool:
    index = bisect_right(closed_at, decision_at) - 1
    if index < 0:
        return False
    step = TIMEFRAME_MS[timeframe]
    return (
        decision_at - closed_at[index] < step
        and run_lengths[index] >= REQUIRED_CONTIGUOUS_BARS[timeframe]
    )


def _text(value: Any, default: str = "UNKNOWN") -> str:
    if isinstance(value, Mapping):
        value = value.get("label", value.get("value", value.get("direction", value.get("status"))))
    if value is None:
        return default
    result = str(getattr(value, "value", value)).strip().upper()
    return result or default


def _bin_trend_strength(value: Any) -> str:
    if isinstance(value, Mapping):
        score = _finite(value.get("score"))
        if score is None and value.get("label") is not None:
            return _text(value.get("label"))
    else:
        score = _finite(value)
    if score is None:
        return "UNKNOWN"
    if score < 25:
        return "WEAK_0_24"
    if score < 50:
        return "MODERATE_25_49"
    if score < 75:
        return "STRONG_50_74"
    return "VERY_STRONG_75_100"


def _bin_consistency(value: Any) -> str:
    score = _finite(value)
    if score is None:
        return "UNKNOWN"
    if score < 40:
        return "CONFLICT_LT40"
    if score < 60:
        return "MIXED_40_59"
    if score < 80:
        return "ALIGNED_60_79"
    return "STRONGLY_ALIGNED_80_100"


def _snapshot_features(analysis: Mapping[str, Any]) -> dict[str, Any]:
    bias = _text(analysis.get("overallBias"))
    timeframe_analyses = analysis.get("timeframeAnalyses")
    four = timeframe_analyses.get("4H", {}) if isinstance(timeframe_analyses, Mapping) else {}
    structure = four.get("structure") if isinstance(four, Mapping) else None
    structure_direction = structure.get("direction") if isinstance(structure, Mapping) else structure
    alignment = analysis.get("alignment") if isinstance(analysis.get("alignment"), Mapping) else {}
    action_context = analysis.get("actionContext") if isinstance(analysis.get("actionContext"), Mapping) else {}
    no_chase = action_context.get("noChase")
    direction = 1 if bias in {"BULLISH", "STRONG_BULLISH"} else -1 if bias in {"BEARISH", "STRONG_BEARISH"} else 0
    return {
        "direction": direction,
        "groups": {
            "overallBias": bias,
            "regime4H": _text(four.get("regime") if isinstance(four, Mapping) else None),
            "structure4H": _text(structure_direction),
            "trendStrength": _bin_trend_strength(four.get("trendStrength") if isinstance(four, Mapping) else None),
            "consistency": _bin_consistency(alignment.get("consistency")),
            "noChase": "NO_CHASE" if no_chase is True else "CHASE_ALLOWED" if no_chase is False else "UNKNOWN",
        },
        "analysisStatus": _text(analysis.get("status")),
        "modelVersion": str(analysis.get("modelVersion") or "indicator-regime-v06"),
    }


def _label_for_horizon(
    decision_at: int,
    reference_price: float,
    direction: int,
    horizon: str,
    fifteen_by_open: Mapping[int, Candle],
    generated_at: int,
) -> dict[str, Any]:
    horizon_ms = HORIZONS_MS[horizon]
    if decision_at + horizon_ms > generated_at:
        return {"status": "PENDING", "eligibleAt": decision_at + horizon_ms}
    steps = horizon_ms // TIMEFRAME_MS["15m"]
    path: list[Candle] = []
    for offset in range(steps):
        row = fifteen_by_open.get(decision_at + offset * TIMEFRAME_MS["15m"])
        if row is None:
            return {"status": "GAP", "eligibleAt": decision_at + horizon_ms}
        path.append(row)
    terminal = path[-1].close
    forward_return = terminal / reference_price - 1
    result: dict[str, Any] = {
        "status": "AVAILABLE",
        "eligibleAt": decision_at + horizon_ms,
        "referencePrice": reference_price,
        "outcomePrice": terminal,
        "forwardReturn": forward_return,
        "pathBars": len(path),
    }
    if direction:
        directional_return = direction * forward_return
        if direction > 0:
            mfe = max(row.high for row in path) / reference_price - 1
            mae = min(row.low for row in path) / reference_price - 1
        else:
            mfe = 1 - min(row.low for row in path) / reference_price
            mae = 1 - max(row.high for row in path) / reference_price
        result.update(
            {
                "directionalReturn": directional_return,
                "directionHit": directional_return > 0,
                # MFE is non-negative favorable movement; MAE is non-positive
                # adverse movement, both expressed as decimal returns.
                "mfe": max(0.0, mfe),
                "mae": min(0.0, mae),
            }
        )
    else:
        result.update({"directionalReturn": None, "directionHit": None, "mfe": None, "mae": None})
    return result


def _hac_mean(values: list[tuple[int, float]], requested_lag: int) -> dict[str, Any]:
    if not values:
        return {
            "mean": None,
            "standardError": None,
            "confidenceInterval95": [None, None],
            "effectiveSampleSize": 0.0,
            "hacLag": 0,
            "requestedHacLag": requested_lag,
        }
    ordered = sorted(values)
    observations = {int(index): float(value) for index, value in ordered}
    sample = list(observations.values())
    count = len(sample)
    mean = math.fsum(sample) / count
    if count == 1:
        return {
            "mean": mean,
            "standardError": None,
            "confidenceInterval95": [None, None],
            "effectiveSampleSize": 1.0,
            "hacLag": 0,
            "requestedHacLag": requested_lag,
        }
    deviations = {index: value - mean for index, value in observations.items()}
    gamma_zero = math.fsum(value * value for value in deviations.values()) / count
    span = max(observations) - min(observations)
    lag = min(requested_lag, max(0, span))
    long_run_variance = gamma_zero
    for offset in range(1, lag + 1):
        products = [value * deviations[index - offset] for index, value in deviations.items() if index - offset in deviations]
        covariance = math.fsum(products) / count if products else 0.0
        long_run_variance += 2 * (1 - offset / (lag + 1)) * covariance
    long_run_variance = max(0.0, long_run_variance)
    standard_error = math.sqrt(long_run_variance / count)
    if gamma_zero == 0:
        effective = float(count)
    elif long_run_variance <= 0:
        effective = float(count)
    else:
        effective = max(1.0, min(float(count), count * gamma_zero / long_run_variance))
    return {
        "mean": mean,
        "standardError": standard_error,
        "confidenceInterval95": [mean - 1.96 * standard_error, mean + 1.96 * standard_error],
        "effectiveSampleSize": effective,
        "hacLag": lag,
        "requestedHacLag": requested_lag,
    }


def _period_statistics(records: list[dict[str, Any]], horizon: str) -> dict[str, Any]:
    available = [record for record in records if record["labels"][horizon]["status"] == "AVAILABLE"]
    raw = [(record["gridIndex"], float(record["labels"][horizon]["forwardReturn"])) for record in available]
    directional = [
        (record["gridIndex"], float(record["labels"][horizon]["directionalReturn"]))
        for record in available
        if record["labels"][horizon].get("directionalReturn") is not None
    ]
    metric = directional if directional else raw
    inference = _hac_mean(metric, HAC_LAGS_15M[horizon])
    effective = float(inference["effectiveSampleSize"] or 0)
    statistical_n = len(metric)
    sufficient = statistical_n >= MIN_STATISTICAL_SAMPLES and effective >= MIN_STATISTICAL_SAMPLES
    return {
        "n": len(available),
        "directionalN": len(directional),
        "statisticalN": statistical_n,
        "meanForwardReturn": math.fsum(value for _, value in raw) / len(raw) if raw else None,
        "meanDirectionalReturn": math.fsum(value for _, value in directional) / len(directional) if directional else None,
        "hitRate": (
            sum(record["labels"][horizon].get("directionHit") is True for record in available) / len(directional)
            if directional
            else None
        ),
        "meanMfe": (
            math.fsum(float(record["labels"][horizon]["mfe"]) for record in available if record["labels"][horizon].get("mfe") is not None)
            / len(directional)
            if directional
            else None
        ),
        "meanMae": (
            math.fsum(float(record["labels"][horizon]["mae"]) for record in available if record["labels"][horizon].get("mae") is not None)
            / len(directional)
            if directional
            else None
        ),
        "inferenceMetric": "DIRECTIONAL_RETURN" if directional else "MARKET_RETURN",
        **inference,
        "statisticalStatus": "SUFFICIENT" if sufficient else "INSUFFICIENT",
        "insufficiencyReasons": [
            reason
            for condition, reason in (
                (statistical_n < MIN_STATISTICAL_SAMPLES, f"少于{MIN_STATISTICAL_SAMPLES}个可推断样本"),
                (effective < MIN_STATISTICAL_SAMPLES, f"HAC有效样本少于{MIN_STATISTICAL_SAMPLES}"),
            )
            if condition
        ],
    }


def _result_set(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {horizon: _period_statistics(records, horizon) for horizon in HORIZONS_MS}


def _period(start_records: list[dict[str, Any]], *, locked: bool, description: str) -> dict[str, Any]:
    return {
        "start": start_records[0]["decisionAt"] if start_records else None,
        "end": start_records[-1]["decisionAt"] if start_records else None,
        "samples": len(start_records),
        "locked": locked,
        "description": description,
    }


def _presentation_fields(
    generated_at: int,
    validation_results: Mapping[str, Mapping[str, Any]],
    sample_size: int,
    limitations: Sequence[str],
) -> dict[str, Any]:
    sufficient = bool(validation_results) and all(
        value.get("statisticalStatus") == "SUFFICIENT" for value in validation_results.values()
    )
    metrics: list[dict[str, Any]] = []
    labels = {"15m": "15 分钟", "1h": "1 小时", "4h": "4 小时", "24h": "24 小时"}
    for horizon, label in labels.items():
        value = validation_results.get(horizon, {})
        for key, name in (
            ("hitRate", "方向命中率"),
            ("meanDirectionalReturn", "方向化平均收益"),
            ("meanMfe", "平均 MFE"),
            ("meanMae", "平均 MAE"),
        ):
            numeric = value.get(key)
            metrics.append(
                {
                    "key": f"{horizon}-{key}",
                    "label": f"{label}{name}",
                    "value": numeric,
                    "valueText": "—" if numeric is None else f"{float(numeric) * 100:.3f}%",
                    "passed": None,
                    "samples": value.get("directionalN", value.get("n", 0)),
                    "statisticalStatus": value.get("statisticalStatus", "INSUFFICIENT"),
                }
            )
    summary = (
        "仅完成一个锁定 OOS 窗口，仍属探索性结果；没有通过正式历史有效性验证。"
        if sufficient
        else "历史验证的原始或 HAC 有效样本不足；当前判断只作为实验性市场结构描述。"
    )
    return {
        "asOf": generated_at,
        "passed": False,
        "sufficient": sufficient,
        "sampleSize": sample_size,
        "minimumSampleSize": MIN_STATISTICAL_SAMPLES,
        "summary": summary,
        "warnings": list(limitations),
        "metrics": metrics,
    }


def _empty_report(generated_at: int, limitations: list[str], quality: dict[str, Any]) -> dict[str, Any]:
    validation_results = _result_set([])
    return {
        "modelVersion": "market-validation-v06",
        "provenance": "HISTORICAL_RECONSTRUCTED",
        "generatedAt": generated_at,
        "status": "INSUFFICIENT_DATA",
        "classification": "EXPLORATORY_ONLY",
        "validationPass": False,
        "trainingPeriod": _period([], locked=False, description="60%时间顺序训练描述区"),
        "validationPeriod": _period([], locked=True, description="40%锁定OOS；不用于阈值选择"),
        "trainingResults": _result_set([]),
        "validationResults": validation_results,
        "groups": {},
        "coverage": quality,
        "independentOosWindows": 0,
        "pbo": {"status": "unavailable", "value": None, "reason": "没有预注册多配置绩效矩阵"},
        "deflatedSharpe": {"status": "unavailable", "value": None, "reason": "没有预注册多配置试验账本"},
        "limitations": limitations,
        **_presentation_fields(generated_at, validation_results, 0, limitations),
    }


def build_validation_report(
    candles_by_timeframe: Mapping[str, Sequence[Any]],
    generated_at: int | None = None,
    max_samples: int | None = None,
    decision_stride: int = 1,
) -> dict[str, Any]:
    """Reconstruct point-in-time v0.6 analyses and evaluate future labels.

    Feature snapshots and outcome labels are deliberately built in separate
    phases.  This function never reads account, order, trade-log, or fill data.
    Every returned snapshot is explicitly historical reconstruction rather
    than an assertion that it was persisted live at the decision time.
    """
    generated = int(generated_at if generated_at is not None else time.time() * 1000)
    if generated <= 0:
        raise ValueError("generated_at must be a positive epoch millisecond value")
    if max_samples is not None and (isinstance(max_samples, bool) or int(max_samples) < 1):
        raise ValueError("max_samples must be a positive integer")
    if isinstance(decision_stride, bool) or int(decision_stride) < 1:
        raise ValueError("decision_stride must be a positive integer")
    stride = int(decision_stride)

    cleaned: dict[str, list[Candle]] = {}
    input_quality: dict[str, dict[str, int]] = {}
    for timeframe in TIMEFRAME_MS:
        cleaned[timeframe], input_quality[timeframe] = _clean_series(
            _timeframe_rows(candles_by_timeframe, timeframe), timeframe, generated
        )
    fifteen = cleaned["15m"]
    quality: dict[str, Any] = {
        "decisionGrid": "15m",
        "inputQuality": input_quality,
        "timeframeCoverage": {},
        "labelStatus": {horizon: {"AVAILABLE": 0, "PENDING": 0, "GAP": 0} for horizon in HORIZONS_MS},
    }
    if not fifteen:
        return _empty_report(generated, ["没有已收盘且合法的15m K线，无法建立决策网格"], quality)

    indexed_decisions = list(enumerate(row.timestamp + TIMEFRAME_MS["15m"] for row in fifteen))
    underlying_decisions = len(indexed_decisions)
    if max_samples is not None and len(indexed_decisions) > int(max_samples):
        indexed_decisions = indexed_decisions[-int(max_samples) :]
    if stride > 1:
        # Anchor systematic sampling at the newest decision.  The retained
        # grid index still counts 15m intervals, so HAC offsets keep their
        # original time meaning even when the interactive report is bounded.
        indexed_decisions = list(reversed(indexed_decisions[::-stride]))
    fifteen_by_open = {row.timestamp: row for row in fifteen}
    close_times: dict[str, list[int]] = {}
    run_lengths: dict[str, list[int]] = {}
    for timeframe, rows in cleaned.items():
        close_times[timeframe], run_lengths[timeframe] = _continuity_metadata(rows, timeframe)

    feature_available_counts = Counter({timeframe: 0 for timeframe in TIMEFRAME_MS})
    records: list[dict[str, Any]] = []
    analysis_errors = 0
    for grid_index, decision_at in indexed_decisions:
        for timeframe in TIMEFRAME_MS:
            if _feature_available(decision_at, timeframe, close_times[timeframe], run_lengths[timeframe]):
                feature_available_counts[timeframe] += 1
        visible: dict[str, list[Candle]] = {}
        for timeframe, rows in cleaned.items():
            end = bisect_right(close_times[timeframe], decision_at)
            start = max(0, end - FEATURE_LOOKBACK[timeframe])
            visible[timeframe] = rows[start:end]
        try:
            analysis = _build_market_analysis(
                visible,
                decision_at=decision_at,
                connection_status="connected",
                gap_status=None,
                include_series=False,
            )
            if not isinstance(analysis, Mapping):
                raise TypeError("market analysis must return a mapping")
        except Exception:
            # A single historically damaged/unsupported snapshot must be
            # counted as unavailable, not abort the complete validation job.
            # SystemExit/KeyboardInterrupt remain outside Exception.
            analysis_errors += 1
            continue
        features = _snapshot_features(analysis)
        reference = fifteen_by_open.get(decision_at - TIMEFRAME_MS["15m"])
        if reference is None:
            continue
        labels = {
            horizon: _label_for_horizon(
                decision_at,
                reference.close,
                int(features["direction"]),
                horizon,
                fifteen_by_open,
                generated,
            )
            for horizon in HORIZONS_MS
        }
        for horizon, label in labels.items():
            quality["labelStatus"][horizon][label["status"]] += 1
        records.append(
            {
                "gridIndex": grid_index,
                "decisionAt": decision_at,
                "provenance": "HISTORICAL_RECONSTRUCTED",
                **features,
                "labels": labels,
            }
        )

    quality.update(
        {
            "decisionSamplesRequested": len(indexed_decisions),
            "underlyingDecisionCloses": underlying_decisions,
            "decisionStride": stride,
            "analysisSamplesBuilt": len(records),
            "analysisErrors": analysis_errors,
            "maxSamplesApplied": int(max_samples) if max_samples is not None else None,
        }
    )
    for timeframe in TIMEFRAME_MS:
        available = int(feature_available_counts[timeframe])
        quality["timeframeCoverage"][timeframe] = {
            "availableSamples": available,
            "totalSamples": len(indexed_decisions),
            "ratio": available / len(indexed_decisions) if indexed_decisions else 0.0,
            "requiredContiguousBars": REQUIRED_CONTIGUOUS_BARS[timeframe],
        }
    limitations = [
        "所有分析均为HISTORICAL_RECONSTRUCTED，不冒充当时实时保存的快照",
        "15m决策标签来自未来已收盘15m K线；未来结果与特征快照严格分离",
        "逐小时与逐15分钟结果存在重叠，标准误使用Newey-West/HAC修正",
        "单次60/40时间切分只有一个锁定OOS区间，少于4个独立OOS窗口",
        "PBO/DSR缺少预注册多配置试验账本，保持不可用",
    ]
    one_minute_ratio = quality["timeframeCoverage"]["1m"]["ratio"]
    if one_minute_ratio < 1:
        limitations.append(
            f"1m特征覆盖仅{one_minute_ratio:.2%}；缺失时保持降级/不可用，不补零也不回填未来1m数据"
        )
    if max_samples is not None:
        limitations.append(f"本报告按max_samples={int(max_samples)}仅使用最近连续15m决策样本")
    if stride > 1:
        limitations.append(f"交互报告每{stride}根15m收盘系统抽样一次；gridIndex仍保留原15m间隔供HAC修正")
    if not records:
        return _empty_report(generated, limitations + ["没有成功重建的分析快照"], quality)

    split_index = min(len(records) - 1, max(1, int(math.floor(len(records) * 0.60)))) if len(records) > 1 else 0
    split_at = records[split_index]["decisionAt"] if len(records) > 1 else records[0]["decisionAt"]
    training = [record for record in records if record["decisionAt"] + PURGE_MS <= split_at]
    validation = [record for record in records if record["decisionAt"] >= split_at]
    purged = [
        record
        for record in records
        if record["decisionAt"] < split_at < record["decisionAt"] + PURGE_MS
    ]

    dimensions = ("overallBias", "regime4H", "structure4H", "trendStrength", "consistency", "noChase")
    groups: dict[str, dict[str, Any]] = {}
    for dimension in dimensions:
        labels = sorted({record["groups"].get(dimension, "UNKNOWN") for record in records})
        groups[dimension] = {}
        for label in labels:
            train_subset = [record for record in training if record["groups"].get(dimension, "UNKNOWN") == label]
            validation_subset = [record for record in validation if record["groups"].get(dimension, "UNKNOWN") == label]
            groups[dimension][label] = {
                "trainingSamples": len(train_subset),
                "validationSamples": len(validation_subset),
                "horizons": {
                    horizon: {
                        "training": _period_statistics(train_subset, horizon),
                        "lockedOos": _period_statistics(validation_subset, horizon),
                    }
                    for horizon in HORIZONS_MS
                },
            }

    validation_results = _result_set(validation)
    return {
        "modelVersion": "market-validation-v06",
        "analysisModelVersion": records[-1]["modelVersion"],
        "provenance": "HISTORICAL_RECONSTRUCTED",
        "generatedAt": generated,
        "status": "COMPLETE" if records else "INSUFFICIENT_DATA",
        "classification": "EXPLORATORY_ONLY",
        "validationPass": False,
        "split": {
            "method": "TIME_ORDERED_60_40",
            "thresholdsOptimized": False,
            "splitAt": split_at,
            "purgeDurationMs": PURGE_MS,
            "purgedSamples": len(purged),
        },
        "trainingPeriod": {
            **_period(training, locked=False, description="60%时间顺序训练描述区；阈值固定、不优化"),
            "maximumLabelEligibleAt": max(
                (record["decisionAt"] + PURGE_MS for record in training), default=None
            ),
        },
        "validationPeriod": _period(validation, locked=True, description="40%锁定OOS；不参与阈值或分箱选择"),
        "trainingResults": _result_set(training),
        "validationResults": validation_results,
        "groups": groups,
        "coverage": quality,
        "independentOosWindows": 1 if validation else 0,
        "minimumIndependentOosWindows": MIN_INDEPENDENT_OOS_WINDOWS,
        "pbo": {"status": "unavailable", "value": None, "reason": "没有预注册多配置绩效矩阵"},
        "deflatedSharpe": {"status": "unavailable", "value": None, "reason": "没有预注册多配置试验账本"},
        "limitations": limitations,
        **_presentation_fields(generated, validation_results, len(validation), limitations),
    }
