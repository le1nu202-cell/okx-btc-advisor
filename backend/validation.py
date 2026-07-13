from __future__ import annotations

import itertools
import math
from datetime import datetime, timezone
from statistics import NormalDist
from typing import Callable

import numpy as np

from .models import Candle

HOUR = 3_600_000
FOUR_HOURS = 4 * HOUR
ANNUAL_HOURS = 365 * 24
MAX_EXHAUSTIVE_CSCV_SPLITS = 200_000


def _add_months(timestamp_ms: int, months: int) -> int:
    """Calendar-month arithmetic in UTC, clipping the day when necessary."""
    source = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc)
    month_index = source.year * 12 + source.month - 1 + months
    year, month = divmod(month_index, 12)
    month += 1
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    last_day = (next_month - datetime.resolution).day
    target = source.replace(year=year, month=month, day=min(source.day, last_day))
    return int(target.timestamp() * 1000)


def _moments_stats(moments: dict) -> dict:
    n = int(moments.get("count", 0))
    s1 = float(moments.get("sumPower1", 0))
    s2 = float(moments.get("sumPower2", 0))
    if n < 2:
        return {"observations": n, "sharpe": 0.0, "mean": s1 / n if n else 0.0, "std": 0.0}
    mean = s1 / n
    variance = max(0.0, (s2 - n * mean * mean) / (n - 1))
    std = math.sqrt(variance)
    return {
        "observations": n,
        "sharpe": mean / std * math.sqrt(ANNUAL_HOURS) if std else 0.0,
        "mean": mean,
        "std": std,
    }


def deflated_sharpe_ratio(moments: dict, trial_sharpes: list[float]) -> dict:
    """Bailey/Lopez de Prado DSR using per-period Sharpe trial estimates."""
    n = int(moments.get("count", 0))
    trials = np.asarray(trial_sharpes, dtype=float)
    required_powers = all(f"sumPower{power}" in moments for power in range(1, 5))
    if n < 30:
        return {"status": "unavailable", "value": None, "reason": "少于30个收益观测"}
    if not required_powers:
        return {"status": "unavailable", "value": None, "reason": "缺少一至四阶完整收益矩"}
    if len(trials) < 2 or not np.all(np.isfinite(trials)):
        return {"status": "unavailable", "value": None, "reason": "至少需要2个独立候选配置的完整试验记录"}
    stats = _moments_stats(moments)
    std = stats["std"]
    if std <= 0:
        return {"status": "unavailable", "value": None, "reason": "收益方差为零"}
    mean = stats["mean"]
    s1 = float(moments["sumPower1"])
    s2 = float(moments["sumPower2"])
    s3 = float(moments["sumPower3"])
    s4 = float(moments["sumPower4"])
    if not all(math.isfinite(x) for x in (s1, s2, s3, s4)):
        return {"status": "unavailable", "value": None, "reason": "收益矩包含非有限值"}
    central3 = (s3 - 3 * mean * s2 + 3 * mean * mean * s1 - n * mean**3) / n
    central4 = (s4 - 4 * mean * s3 + 6 * mean * mean * s2 - 4 * mean**3 * s1 + n * mean**4) / n
    skew = central3 / std**3
    kurtosis = central4 / std**4
    observed = mean / std
    trial_mean = float(np.mean(trials))
    trial_std = float(np.std(trials, ddof=1))
    gamma = 0.5772156649015329
    normal = NormalDist()
    count = len(trials)
    # E[max(SR)] includes the cross-trial mean.  Omitting it makes a family of
    # uniformly strong/inflated trials look as if its multiple-testing hurdle
    # were centred at zero and can severely overstate DSR.
    expected_max = trial_mean + trial_std * (
        (1 - gamma) * normal.inv_cdf(1 - 1 / count)
        + gamma * normal.inv_cdf(1 - 1 / (count * math.e))
    )
    denominator = 1 - skew * observed + ((kurtosis - 1) / 4) * observed**2
    if denominator <= 0 or not math.isfinite(denominator):
        return {"status": "unavailable", "value": None, "reason": "偏度/峰度修正项不可计算"}
    probability = normal.cdf((observed - expected_max) * math.sqrt(n - 1) / math.sqrt(denominator))
    return {
        "status": "available",
        "value": float(probability),
        "observedSharpe": observed * math.sqrt(ANNUAL_HOURS),
        "deflatedBenchmarkSharpe": expected_max * math.sqrt(ANNUAL_HOURS),
        "trialMeanSharpe": trial_mean * math.sqrt(ANNUAL_HOURS),
        "trials": count,
        "observations": n,
    }


def probability_of_backtest_overfitting(performance_matrix: list[list[float]]) -> dict:
    """Exhaustive CSCV PBO over subperiod x configuration observations."""
    matrix = np.asarray(performance_matrix, dtype=float)
    if matrix.ndim != 2:
        return {"status": "unavailable", "value": None, "reason": "绩效矩阵必须为二维"}
    periods, configs = matrix.shape
    if configs < 2:
        return {"status": "unavailable", "value": None, "reason": "至少需要2个独立候选配置"}
    if periods < 8:
        return {"status": "unavailable", "value": None, "reason": "CSCV至少需要8个样本外子区间"}
    dropped_periods = 0
    if periods % 2:
        matrix = matrix[:-1]
        periods -= 1
        dropped_periods = 1
    if not np.all(np.isfinite(matrix)):
        return {"status": "unavailable", "value": None, "reason": "绩效矩阵包含非有限值"}
    total_splits = math.comb(periods, periods // 2)
    if total_splits > MAX_EXHAUSTIVE_CSCV_SPLITS:
        return {
            "status": "unavailable",
            "value": None,
            "reason": f"完整CSCV需要{total_splits}次分割，超过安全上限{MAX_EXHAUSTIVE_CSCV_SPLITS}；请先聚合子区间",
            "periods": periods,
            "configurations": configs,
        }

    logits = []
    indices = range(periods)
    for train_tuple in itertools.combinations(indices, periods // 2):
        train = set(train_tuple)
        test = [i for i in indices if i not in train]
        selected = int(np.argmax(np.mean(matrix[list(train)], axis=0)))
        test_scores = np.mean(matrix[test], axis=0)
        selected_score = test_scores[selected]
        # Average ranks make tied OOS configurations neutral rather than
        # depending on their input column order. Rank 1 is worst.
        rank = 1 + float(np.sum(test_scores < selected_score)) + 0.5 * float(np.sum(test_scores == selected_score) - 1)
        relative = rank / (configs + 1)
        logits.append(math.log(relative / (1 - relative)))
    if not logits:
        return {"status": "unavailable", "value": None, "reason": "没有可用CSCV分割"}
    return {
        "status": "available",
        "value": float(np.mean(np.asarray(logits) <= 0)),
        "splits": len(logits),
        "periods": periods,
        "configurations": configs,
        "droppedPeriods": dropped_periods,
        "exhaustive": True,
    }


def _summary(result: dict) -> dict:
    keys = (
        "status",
        "netReturn",
        "maxDrawdown",
        "sharpe",
        "sortino",
        "calmar",
        "profitFactor",
        "grossProfit",
        "grossLoss",
        "winRate",
        "trades",
        "exposure",
        "fundingCoverage",
        "stress",
        "dataQuality",
        "returnMoments",
    )
    return {key: result.get(key) for key in keys if key in result}


def _metrics_complete(result: dict) -> bool:
    numeric = ("netReturn", "sharpe", "profitFactor", "grossProfit", "grossLoss", "trades", "fundingCoverage")
    if result.get("status") != "complete":
        return False
    try:
        if not all(math.isfinite(float(result[key])) for key in numeric):
            return False
        moments = result["returnMoments"]
        if int(moments["count"]) <= 0 or not all(
            math.isfinite(float(moments[f"sumPower{power}"])) for power in range(1, 5)
        ):
            return False
        stress = result["stress"]
        return all(math.isfinite(float(stress[f"{bps}bps"]["netReturn"])) for bps in (5, 10, 20))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _aggregate_windows(windows: list[dict]) -> dict:
    all_results = [x["oosMetrics"] for x in windows]
    results = [x for x in all_results if x.get("status") == "complete"]
    net = 1.0
    for result in results:
        net *= 1 + float(result.get("netReturn", 0))
    moments = {"count": 0, **{f"sumPower{p}": 0.0 for p in range(1, 5)}}
    for result in results:
        source = result.get("returnMoments", {})
        moments["count"] += int(source.get("count", 0))
        for p in range(1, 5):
            moments[f"sumPower{p}"] += float(source.get(f"sumPower{p}", 0))
    gross_profit = sum(float(result.get("grossProfit", 0)) for result in results)
    gross_loss = sum(float(result.get("grossLoss", 0)) for result in results)
    stresses = {}
    for bps in (5, 10, 20):
        value = 1.0
        for result in results:
            value *= 1 + float(result.get("stress", {}).get(f"{bps}bps", {}).get("netReturn", 0))
        stresses[f"{bps}bps"] = {"netReturn": value - 1}
    stats = _moments_stats(moments)
    funding_coverages = [float(x.get("fundingCoverage", 0)) for x in results]
    return {
        "windows": len(windows),
        "completedWindows": len(results),
        "allWindowsComplete": len(results) == len(windows),
        "metricsComplete": bool(windows) and all(_metrics_complete(x) for x in all_results),
        "netReturn": net - 1,
        "sharpe": stats["sharpe"],
        "profitFactor": gross_profit / gross_loss if gross_loss else (999.0 if gross_profit else 0.0),
        "trades": sum(int(x.get("trades", 0)) for x in results),
        # Failed windows count as not profitable; they can never be silently
        # removed from the 60% acceptance denominator.
        "profitableWindowRatio": (
            sum(float(x.get("netReturn", 0)) > 0 for x in results) / len(windows) if windows else 0.0
        ),
        "minimumFundingCoverage": min(funding_coverages) if funding_coverages else 0.0,
        "stress": stresses,
        "returnMoments": moments,
    }


def _timeline_quality(c1: list[Candle], c4: list[Candle], start: int, end: int) -> dict:
    one = sorted({x.timestamp for x in c1 if start <= x.timestamp < end})
    four = sorted({x.timestamp for x in c4 if start <= x.timestamp and x.timestamp + FOUR_HOURS <= end})
    expected_1h = max(1, math.floor((end - start) / HOUR))
    expected_4h = max(1, math.floor((end - start) / FOUR_HOURS))
    gaps_1h = sum(1 for a, b in zip(one, one[1:]) if b - a != HOUR)
    gaps_4h = sum(1 for a, b in zip(four, four[1:]) if b - a != FOUR_HOURS)
    maximum_missing_1h = max((max(0, (b - a) // HOUR - 1) for a, b in zip(one, one[1:])), default=0)
    maximum_missing_4h = max((max(0, (b - a) // FOUR_HOURS - 1) for a, b in zip(four, four[1:])), default=0)
    return {
        "start": start,
        "end": end,
        "observed1H": len(one),
        "expected1H": expected_1h,
        "coverage1H": min(1.0, len(one) / expected_1h),
        "observed4H": len(four),
        "expected4H": expected_4h,
        "coverage4H": min(1.0, len(four) / expected_4h),
        "gaps1H": gaps_1h,
        "gaps4H": gaps_4h,
        "maximumGapHours1H": maximum_missing_1h,
        "maximumGapHours4H": maximum_missing_4h * 4,
    }


def run_strict_validation(
    c1: list[Candle],
    c4: list[Candle],
    funding: list[tuple[int, float]],
    strategy: str,
    fee_bps: float,
    slippage_bps: float,
    runner: Callable,
) -> dict:
    """Run fixed 18m calibration / 3m OOS windows and a final locked 12m holdout."""
    unavailable = {"status": "unavailable", "value": None}
    c1 = sorted((x for x in c1 if x.confirm and x.timeframe == "1H"), key=lambda x: x.timestamp)
    c4 = sorted((x for x in c4 if x.confirm and x.timeframe == "4H"), key=lambda x: x.timestamp)
    if not c1 or not c4:
        return {
            "status": "unavailable",
            "validationPass": False,
            "validationLabel": "严格验证不可用",
            "windows": [],
            "holdout": None,
            "pbo": {**unavailable, "reason": "无K线数据"},
            "deflatedSharpe": {**unavailable, "reason": "无K线数据"},
            "limitations": ["严格验证需要至少33个月连续数据"],
        }

    data_start = max(c1[0].timestamp, c4[0].timestamp)
    data_end = min(c1[-1].timestamp + HOUR, c4[-1].timestamp + FOUR_HOURS)
    holdout_start = _add_months(data_end, -12)

    # Anchor backwards from the locked holdout.  Forward anchoring from the
    # later of a 1H/4H start can lose a complete 3m OOS window solely because
    # the feeds are offset by a few hours (a real OKX history starts this way).
    reverse_boundaries: list[tuple[int, int, int]] = []
    oos_end = holdout_start
    for _ in range(64):
        oos_start = _add_months(oos_end, -3)
        train_start = _add_months(oos_start, -18)
        if data_start - train_start > FOUR_HOURS:
            break
        reverse_boundaries.append((train_start, oos_start, oos_end))
        oos_end = oos_start
    boundaries = list(reversed(reverse_boundaries))
    if not boundaries:
        reason = "不足以形成18个月训练、3个月样本外和最后12个月锁定区间"
        return {
            "status": "unavailable",
            "validationPass": False,
            "validationLabel": "严格验证不可用",
            "windows": [],
            "holdout": None,
            "pbo": {**unavailable, "reason": reason},
            "deflatedSharpe": {**unavailable, "reason": reason},
            "limitations": [reason],
        }

    windows = []
    for index, (train_start, train_end, oos_end) in enumerate(boundaries, 1):
        rows1 = [x for x in c1 if train_start <= x.timestamp < oos_end]
        rows4 = [x for x in c4 if train_start <= x.timestamp < oos_end]
        rates = [x for x in funding if train_start <= x[0] <= oos_end]
        result = runner(
            rows1,
            rows4,
            rates,
            strategy,
            fee_bps,
            slippage_bps,
            evaluation_start_ms=train_end,
            evaluation_end_ms=oos_end,
            strict_validation=False,
        )
        windows.append(
            {
                "index": index,
                "trainStart": train_start,
                "trainEnd": train_end,
                "oosStart": train_end,
                "oosEnd": oos_end,
                "calibration": {
                    "mode": "frozen_parameters",
                    "selectedConfig": strategy,
                    "fittedParameters": False,
                },
                "oosMetrics": _summary(result),
            }
        )

    warmup_start = _add_months(holdout_start, -18)
    holdout_result = runner(
        [x for x in c1 if warmup_start <= x.timestamp < data_end],
        [x for x in c4 if warmup_start <= x.timestamp < data_end],
        [x for x in funding if warmup_start <= x[0] <= data_end],
        strategy,
        fee_bps,
        slippage_bps,
        evaluation_start_ms=holdout_start,
        evaluation_end_ms=data_end,
        strict_validation=False,
    )
    holdout = {
        "locked": True,
        "start": holdout_start,
        "end": data_end,
        "metrics": _summary(holdout_result),
    }
    aggregate = _aggregate_windows(windows)
    timeline_quality = _timeline_quality(c1, c4, boundaries[0][0], data_end)
    holdout_funding = float(holdout_result.get("fundingCoverage", 0)) if holdout_result.get("status") == "complete" else 0.0
    criteria = {
        "atLeastTwoOosWindows": len(windows) >= 2,
        "allOosWindowsComplete": aggregate["allWindowsComplete"],
        "oosMetricsComplete": aggregate["metricsComplete"],
        "marketDataCoverageAtLeast99Pct": (
            timeline_quality["coverage1H"] >= 0.99 and timeline_quality["coverage4H"] >= 0.99
        ),
        "noMarketDataGapLongerThan8Hours": (
            timeline_quality["maximumGapHours1H"] <= 8 and timeline_quality["maximumGapHours4H"] <= 8
        ),
        "fundingCoverageAtLeast95Pct": (
            aggregate["minimumFundingCoverage"] >= 0.95 and holdout_funding >= 0.95
        ),
        "positiveOosNetReturn": aggregate["netReturn"] > 0,
        "profitFactorAtLeast1_1": aggregate["profitFactor"] >= 1.1,
        "sharpeAtLeast0_5": aggregate["sharpe"] >= 0.5,
        "atLeast100Trades": aggregate["trades"] >= 100,
        "profitableWindowsAtLeast60Pct": aggregate["profitableWindowRatio"] >= 0.6,
        "positiveAt20bps": aggregate["stress"]["20bps"]["netReturn"] > 0,
        "positiveLockedHoldout": (
            holdout_result.get("status") == "complete" and float(holdout_result.get("netReturn", 0)) > 0
        ),
        "positiveLockedHoldoutAt20bps": (
            holdout_result.get("status") == "complete"
            and float(holdout_result.get("stress", {}).get("20bps", {}).get("netReturn", 0)) > 0
        ),
    }
    dsr = {
        **unavailable,
        "reason": "当前仅验证一个冻结配置；缺少完整、预先声明的多配置试验账本",
    }
    pbo = {
        **unavailable,
        "reason": "当前仅验证一个冻结配置；PBO至少需要2个配置和8个子区间",
    }
    threshold_pass = all(criteria.values())
    # Do not award the strongest certification while multiple-testing risk
    # cannot be measured. Threshold results remain visible separately.
    validation_pass = threshold_pass and dsr["status"] == "available" and pbo["status"] == "available"
    limitations = []
    if dsr["status"] != "available":
        limitations.append(f"DSR不可用：{dsr['reason']}")
    if pbo["status"] != "available":
        limitations.append(f"PBO不可用：{pbo['reason']}")
    if not criteria["marketDataCoverageAtLeast99Pct"]:
        limitations.append("严格验证区间的1H或4H K线覆盖率低于99%")
    if not criteria["noMarketDataGapLongerThan8Hours"]:
        limitations.append("严格验证区间存在超过8小时的连续K线缺口")
    if not criteria["fundingCoverageAtLeast95Pct"]:
        limitations.append("样本外或锁定期资金费率覆盖率低于95%")
    return {
        "status": "complete",
        "scheme": {
            "trainingMonths": 18,
            "oosMonths": 3,
            "stepMonths": 3,
            "lockedHoldoutMonths": 12,
            "calendar": "UTC",
            "anchor": "locked_holdout_backward",
            "alignmentToleranceHours": 4,
        },
        "windows": windows,
        "aggregateOos": aggregate,
        "holdout": holdout,
        "timelineQuality": timeline_quality,
        "criteria": criteria,
        "thresholdPass": threshold_pass,
        "diagnosticsComplete": False,
        "pbo": pbo,
        "deflatedSharpe": dsr,
        "validationPass": validation_pass,
        "validationLabel": "通过严格历史验证" if validation_pass else "实验信号（严格验证未通过）",
        "limitations": limitations,
    }
