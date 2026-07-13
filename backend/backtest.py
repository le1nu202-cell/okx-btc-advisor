from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from .indicators import ema
from .models import AdviceAction, Candle
from .strategy import analyze

HOUR = 3_600_000
FOUR_HOURS = 4 * HOUR
NINETY_DAYS = 90 * 24 * HOUR


def _valid(c: Candle, timeframe: str) -> bool:
    values = (c.open, c.high, c.low, c.close, c.volume)
    return bool(
        c.confirm
        and c.timeframe == timeframe
        and all(math.isfinite(float(x)) for x in values)
        and min(c.open, c.high, c.low, c.close) > 0
        and c.volume >= 0
        and c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high
    )


def _clean(rows: list[Candle], timeframe: str) -> tuple[list[Candle], int]:
    by_ts = {c.timestamp: c for c in rows if _valid(c, timeframe)}
    clean = [by_ts[k] for k in sorted(by_ts)]
    step = HOUR if timeframe == "1H" else FOUR_HOURS
    gaps = sum(1 for a, b in zip(clean, clean[1:]) if b.timestamp - a.timestamp != step)
    return clean, gaps


def _compound(values) -> float:
    """Compound simple returns and preserve bankruptcy instead of reviving at 1%."""
    wealth = 1.0
    for value in values:
        factor = 1 + float(value)
        if not math.isfinite(factor):
            raise ValueError("non-finite return")
        if factor <= 0:
            return -1.0
        wealth *= factor
    return float(wealth - 1)


def _drawdown(values) -> float:
    wealth = peak = 1.0
    maximum = 0.0
    for value in values:
        factor = 1 + float(value)
        if not math.isfinite(factor):
            raise ValueError("non-finite return")
        wealth *= max(0.0, factor)
        peak = max(peak, wealth)
        maximum = max(maximum, 1 - wealth / peak)
        if wealth <= 0:
            return 1.0
    return float(maximum)


def _scenario(trades: list[dict], fee_bps: float, slippage_bps: float) -> dict:
    returns = []
    for trade in trades:
        net = trade["grossReturn"] - 2 * (fee_bps + slippage_bps) / 10_000 - trade["fundingCost"]
        returns.append(net)
    wins = sum(x for x in returns if x > 0)
    losses = -sum(x for x in returns if x < 0)
    return {
        "netReturn": _compound(returns),
        "maxDrawdown": _drawdown(returns),
        "profitFactor": float(wins / losses) if losses else (999.0 if wins else 0.0),
        "grossProfit": float(wins),
        "grossLoss": float(losses),
    }


def _annualized_ratios(hourly_returns: np.ndarray) -> tuple[float, float]:
    if not len(hourly_returns):
        return 0.0, 0.0
    mean = float(np.mean(hourly_returns))
    std = float(np.std(hourly_returns, ddof=1)) if len(hourly_returns) > 1 else 0.0
    sharpe = mean / std * math.sqrt(365 * 24) if std > 0 else 0.0
    # Sortino downside deviation is the RMS of all target shortfalls.  Taking
    # the standard deviation of negative observations alone makes equal losses
    # look risk-free and materially inflates/invalidates the statistic.
    downside_deviation = float(np.sqrt(np.mean(np.minimum(hourly_returns, 0.0) ** 2)))
    sortino = mean / downside_deviation * math.sqrt(365 * 24) if downside_deviation > 0 else 0.0
    return float(sharpe), float(sortino)


def _ema_benchmark(
    candles: list[Candle],
    fee_bps: float,
    slippage_bps: float,
    evaluation_start_ms: int,
    evaluation_end_ms: int,
) -> float:
    """EMA benchmark with pre-window warm-up and a closing transaction cost."""
    close = np.asarray([c.close for c in candles], dtype=float)
    fast, slow = ema(close, 20), ema(close, 50)
    one_way_cost = (fee_bps + slippage_bps) / 10_000
    returns: list[float] = []
    previous_position = 0
    for i in range(1, len(close)):
        current = candles[i]
        if not evaluation_start_ms <= current.timestamp < evaluation_end_ms:
            continue
        if current.timestamp - candles[i - 1].timestamp != HOUR:
            if previous_position:
                returns.append(-abs(previous_position) * one_way_cost)
            previous_position = 0
            continue
        position = 0 if not np.isfinite(slow[i - 1]) else (1 if fast[i - 1] > slow[i - 1] else -1)
        turnover = abs(position - previous_position)
        returns.append(position * (close[i] / close[i - 1] - 1) - turnover * one_way_cost)
        previous_position = position
    if previous_position:
        returns.append(-abs(previous_position) * one_way_cost)
    return _compound(returns)


def _invalid_result(reason: str) -> dict:
    return {
        "status": "invalid_parameters",
        "trades": 0,
        "validationPass": False,
        "validationLabel": "实验信号（参数无效）",
        "reason": reason,
    }


def run_backtest(
    c1: list[Candle],
    c4: list[Candle],
    funding: list[tuple[int, float]],
    strategy="combined",
    fee_bps=5,
    slippage_bps=5,
    max_hold_bars=24,
    evaluation_start_ms: int | None = None,
    evaluation_end_ms: int | None = None,
    strict_validation: bool = True,
    _advice_cache: dict[int, object] | None = None,
) -> dict:
    if strategy not in {"combined", "trend", "range"}:
        return _invalid_result("未知策略")
    if not isinstance(max_hold_bars, int) or max_hold_bars < 1:
        return _invalid_result("最大持有K线数必须为正整数")
    if not all(math.isfinite(float(x)) and float(x) >= 0 for x in (fee_bps, slippage_bps)):
        return _invalid_result("手续费和滑点必须为非负有限值")
    if evaluation_start_ms is not None and evaluation_end_ms is not None and evaluation_end_ms <= evaluation_start_ms:
        return _invalid_result("评估结束时间必须晚于开始时间")

    c1, gaps_1h = _clean(c1, "1H")
    c4, gaps_4h = _clean(c4, "4H")
    if len(c1) < 300 or len(c4) < 100:
        return {
            "status": "insufficient_data",
            "trades": 0,
            "validationPass": False,
            "reason": "清洗后历史K线不足",
            "dataQuality": {"gaps1H": gaps_1h, "gaps4H": gaps_4h},
        }

    funding_by_time: dict[int, float] = {}
    for timestamp, rate in funding:
        try:
            parsed_time, parsed_rate = int(timestamp), float(rate)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(parsed_rate):
            funding_by_time[parsed_time] = parsed_rate
    funding = sorted(funding_by_time.items())
    funding_ts = [x[0] for x in funding]
    funding_prefix = [0.0]
    for _, rate in funding:
        funding_prefix.append(funding_prefix[-1] + rate)

    def funding_sum(after_ms: int, through_ms: int) -> float:
        left = bisect_right(funding_ts, after_ms)
        right = bisect_right(funding_ts, through_ms)
        return funding_prefix[right] - funding_prefix[left]

    c1_ts = [x.timestamp for x in c1]
    c4_ts = [x.timestamp for x in c4]
    advice_cache = _advice_cache if _advice_cache is not None else {}
    report_start = max(c1[0].timestamp, evaluation_start_ms if evaluation_start_ms is not None else c1[0].timestamp)
    report_end = min(c1[-1].timestamp + HOUR, evaluation_end_ms if evaluation_end_ms is not None else c1[-1].timestamp + HOUR)
    if report_end <= report_start:
        return _invalid_result("评估窗口不包含可用1H K线")

    trades: list[dict] = []
    hourly = np.zeros(len(c1), dtype=float)
    held_bars = 0
    i = 220
    while i < len(c1) - 1:
        current, nxt = c1[i], c1[i + 1]
        if nxt.timestamp - current.timestamp != HOUR:
            i += 1
            continue
        if not report_start <= nxt.timestamp < report_end:
            i += 1
            continue

        decision_at = current.timestamp + HOUR
        # Candle timestamps are opens.  At the decision time only 4H candles
        # whose close is <= decision_at may be visible.  strategy.analyze also
        # enforces this, but the simulator must be point-in-time safe itself.
        k = bisect_right(c4_ts, decision_at - FOUR_HOURS)
        four = c4[max(0, k - 260) : k]
        funding_left = bisect_left(funding_ts, decision_at - NINETY_DAYS)
        funding_right = bisect_right(funding_ts, decision_at)
        known_funding = funding[funding_left:funding_right]
        if decision_at in advice_cache:
            advice = advice_cache[decision_at]
        else:
            advice = analyze(c1[max(0, i - 300) : i + 1], four, known_funding, now_ms=decision_at)
            advice_cache[decision_at] = advice
        if advice.action not in (AdviceAction.LONG_CANDIDATE, AdviceAction.SHORT_CANDIDATE) or (
            strategy != "combined" and advice.strategy != strategy
        ):
            i += 1
            continue

        side = 1 if advice.action == AdviceAction.LONG_CANDIDATE else -1
        entry = nxt.open
        stop = advice.stop_loss
        target = advice.targets[1] if len(advice.targets) > 1 else None
        if stop is None or target is None or (
            side > 0 and (stop >= entry or target <= entry)
        ) or (
            side < 0 and (stop <= entry or target >= entry)
        ):
            i += 1
            continue

        natural_exit_idx = min(len(c1) - 1, i + max_hold_bars)
        window_last_idx = bisect_left(c1_ts, report_end) - 1
        exit_idx = min(natural_exit_idx, window_last_idx)
        if exit_idx < i + 1:
            i += 1
            continue
        exit_price = c1[exit_idx].close
        reason = "windowEnd" if exit_idx < natural_exit_idx else "maxHold"
        previous = nxt
        for j in range(i + 1, exit_idx + 1):
            bar = c1[j]
            if j > i + 1 and bar.timestamp - previous.timestamp != HOUR:
                exit_idx = j - 1
                exit_price = previous.close
                reason = "dataGap"
                break
            stop_hit = bar.low <= stop if side > 0 else bar.high >= stop
            target_hit = bar.high >= target if side > 0 else bar.low <= target
            if stop_hit:
                # A stop-market order cannot fill at the stale stop if the bar
                # opens through it.  Use the worse open; targets remain capped
                # at their target price as a conservative convention.
                exit_idx = j
                exit_price = min(stop, bar.open) if side > 0 else max(stop, bar.open)
                reason = "stop"
                break
            if target_hit:
                exit_idx = j
                exit_price = target
                reason = "target"
                break
            previous = bar

        exit_close_ts = c1[exit_idx].timestamp + HOUR
        # Stops/targets happen inside the exit candle. A settlement timestamp
        # exactly at that candle's close occurs after the intrabar exit and is
        # therefore excluded; a settlement at its open is included. Scheduled
        # max-hold/window exits occur at the close and include that settlement.
        funding_through_ts = c1[exit_idx].timestamp if reason in {"stop", "target"} else exit_close_ts
        funding_cost = side * funding_sum(nxt.timestamp, funding_through_ts)
        gross = side * (exit_price - entry) / entry
        one_way_cost = (fee_bps + slippage_bps) / 10_000
        net = gross - 2 * one_way_cost - funding_cost

        held_bars += max(1, exit_idx - i)
        trades.append(
            {
                "entryTime": nxt.timestamp,
                "exitTime": exit_close_ts,
                "exitIndex": exit_idx,
                "side": "long" if side > 0 else "short",
                "grossReturn": gross,
                "fundingCost": funding_cost,
                "return": net,
                "reason": reason,
                "regime": advice.regime.value,
                "stop": stop,
                "target": target,
                "barsHeld": max(1, exit_idx - i),
                "entryIndex": i + 1,
                "entryPrice": entry,
                "exitPrice": exit_price,
                "fundingThroughTime": funding_through_ts,
            }
        )
        # The exit bar close is a valid new decision point; entering on its
        # next open remains non-overlapping and avoids an accidental 1H cooldown.
        i = exit_idx

    def mark_to_market_path(path_slippage_bps: float) -> np.ndarray:
        path = np.zeros(len(c1), dtype=float)
        path_one_way_cost = (fee_bps + path_slippage_bps) / 10_000
        for trade in trades:
            entry_idx = int(trade["entryIndex"])
            exit_idx = int(trade["exitIndex"])
            entry_price = float(trade["entryPrice"])
            side = 1 if trade["side"] == "long" else -1
            previous_factor = 1.0
            for j in range(entry_idx, exit_idx + 1):
                mark = float(trade["exitPrice"]) if j == exit_idx else c1[j].close
                mark_through_ts = (
                    int(trade["fundingThroughTime"]) if j == exit_idx else c1[j].timestamp + HOUR
                )
                accrued_funding = side * funding_sum(int(trade["entryTime"]), mark_through_ts)
                factor = 1 + side * (mark - entry_price) / entry_price - path_one_way_cost - accrued_funding
                if j == exit_idx:
                    factor -= path_one_way_cost
                factor = max(0.0, factor)
                path[j] = factor / previous_factor - 1 if previous_factor > 0 else 0.0
                previous_factor = factor
        return path

    hourly = mark_to_market_path(slippage_bps)
    report_mask = np.asarray([report_start <= x.timestamp < report_end for x in c1], dtype=bool)
    report_hourly = hourly[report_mask]
    report_candles = [x for x, keep in zip(c1, report_mask) if keep]
    returns = np.asarray([t["return"] for t in trades], dtype=float)
    base = _scenario(trades, fee_bps, slippage_bps)
    base["maxDrawdown"] = _drawdown(report_hourly)
    sharpe, sortino = _annualized_ratios(report_hourly)
    years = max((report_end - report_start) / (365 * 24 * HOUR), 1 / 365)
    annualized = (1 + base["netReturn"]) ** (1 / years) - 1 if base["netReturn"] > -1 else -1
    calmar = annualized / base["maxDrawdown"] if base["maxDrawdown"] > 0 else 0.0

    by_year = defaultdict(list)
    for trade in trades:
        by_year[str(datetime.fromtimestamp(trade["entryTime"] / 1000, timezone.utc).year)].append(trade["return"])
    by_year = {year: {"trades": len(vals), "netReturn": _compound(vals)} for year, vals in by_year.items()}
    max_losing = losing = 0
    for value in returns:
        losing = losing + 1 if value < 0 else 0
        max_losing = max(max_losing, losing)

    expected_funding = max(1, math.floor((report_end - report_start) / (8 * HOUR)))
    observed_funding = bisect_right(funding_ts, report_end) - bisect_right(funding_ts, report_start)
    funding_coverage = min(1.0, observed_funding / expected_funding)
    stresses = {}
    for bps in (5, 10, 20):
        scenario = _scenario(trades, fee_bps, bps)
        scenario["maxDrawdown"] = _drawdown(mark_to_market_path(bps)[report_mask])
        stresses[f"{bps}bps"] = scenario
    buy_hold = (
        report_candles[-1].close / report_candles[0].open - 1 - 2 * (fee_bps + slippage_bps) / 10_000
        if report_candles
        else 0.0
    )

    equity = peak = 1.0
    equity_curve = [{"timestamp": report_start, "value": 1.0}]
    drawdown_curve = [{"timestamp": report_start, "value": 0.0}]
    for candle, value in zip(report_candles, report_hourly):
        if value == 0:
            continue
        equity *= max(0.0, 1 + float(value))
        peak = max(peak, equity)
        equity_curve.append({"timestamp": candle.timestamp + HOUR, "value": equity})
        drawdown_curve.append({"timestamp": candle.timestamp + HOUR, "value": 1 - equity / peak})

    by_regime = {}
    for regime in sorted({x["regime"] for x in trades}):
        subset = [x for x in trades if x["regime"] == regime]
        summary = _scenario(subset, fee_bps, slippage_bps)
        by_regime[regime] = {
            **summary,
            "trades": len(subset),
            "winRate": sum(x["return"] > 0 for x in subset) / len(subset),
        }
    moments = {
        "count": int(len(report_hourly)),
        **{f"sumPower{p}": float(np.sum(report_hourly**p)) for p in range(1, 5)},
    }
    report_gaps_1h = sum(
        1 for a, b in zip(report_candles, report_candles[1:]) if b.timestamp - a.timestamp != HOUR
    )
    result = {
        "status": "complete",
        "strategy": strategy,
        "netReturn": base["netReturn"],
        "maxDrawdown": base["maxDrawdown"],
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "profitFactor": base["profitFactor"],
        "grossProfit": base["grossProfit"],
        "grossLoss": base["grossLoss"],
        "winRate": float(np.mean(returns > 0)) if len(returns) else 0,
        "trades": len(trades),
        "exposure": held_bars / max(1, len(report_hourly)),
        "maxConsecutiveLosses": max_losing,
        "averageBarsHeld": float(np.mean([t["barsHeld"] for t in trades])) if trades else 0,
        "byYear": by_year,
        "byRegime": by_regime,
        "equityCurve": equity_curve,
        "drawdownCurve": drawdown_curve,
        "returnMoments": moments,
        "fundingCoverage": funding_coverage,
        "fundingApproximation": funding_coverage < 0.95,
        "openInterestIncluded": False,
        "newsIncluded": False,
        "stress": stresses,
        "benchmarks": {
            "cash": 0.0,
            "buyHold1x": buy_hold,
            "ema20_50": _ema_benchmark(c1, fee_bps, slippage_bps, report_start, report_end),
        },
        "dataQuality": {
            "gaps1H": gaps_1h,
            "gaps4H": gaps_4h,
            "evaluationGaps1H": report_gaps_1h,
        },
        "pbo": None,
        "deflatedSharpe": None,
        "validationPass": False,
        "validationLabel": "实验信号",
        "holdingRule": (
            f"信号收盘后按下一根开盘进入；使用信号时绝对止损/{'2R目标' if strategy=='trend' else '对侧布林轨目标' if strategy=='range' else '趋势2R或震荡对侧布林轨目标'}，最多持有{max_hold_bars}根1H；"
            "同根止盈止损先按止损，跳空止损按更差开盘价；退出K线收盘可重新评估；评估窗口末强制平仓"
        ),
        "limitations": [
            "资金费率缺口按0近似" if funding_coverage < 0.95 else "资金费率覆盖充分",
            "历史OI与point-in-time新闻不可得，未进入回测",
            "净值与最大回撤按持仓期间逐小时盯市；K线内部路径未知时采用同根先止损",
        ],
    }
    if strict_validation:
        from .validation import run_strict_validation

        def cached_runner(*args, **kwargs):
            kwargs["_advice_cache"] = advice_cache
            return run_backtest(*args, **kwargs)

        strict = run_strict_validation(c1, c4, funding, strategy, fee_bps, slippage_bps, cached_runner)
        result.update(
            {
                "strictValidation": strict,
                "pbo": strict["pbo"],
                "deflatedSharpe": strict["deflatedSharpe"],
                "validationPass": strict["validationPass"],
                "validationLabel": strict["validationLabel"],
            }
        )
        result["limitations"].extend(strict.get("limitations", []))
    return result
