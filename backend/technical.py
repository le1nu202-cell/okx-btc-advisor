from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Iterable

import numpy as np

from backend.indicators import atr, bollinger, ema, macd, obv, rsi, sma
from backend.models import Candle


GROUP_CAPS = {"trend": 30.0, "structure": 25.0, "volume_price": 20.0, "momentum": 15.0, "volatility": 10.0}


def _clean(candles: Iterable[Candle]) -> list[Candle]:
    """Use confirmed candles only, sorted and de-duplicated by open timestamp."""
    by_time: dict[int, Candle] = {}
    for candle in candles:
        if candle.confirm and all(isfinite(float(v)) for v in (candle.open, candle.high, candle.low, candle.close, candle.volume)):
            by_time[candle.timestamp] = candle
    return [by_time[key] for key in sorted(by_time)]


def _arrays(candles: list[Candle]) -> tuple[np.ndarray, ...]:
    return tuple(np.asarray([getattr(c, name) for c in candles], dtype=float) for name in ("open", "high", "low", "close", "volume"))


def _last_finite(values: np.ndarray) -> float | None:
    valid = values[np.isfinite(values)]
    return round(float(valid[-1]), 8) if len(valid) else None


def _clip(value: float, cap: float) -> float:
    return round(float(np.clip(value, -cap, cap)), 2)


def _percentile_rank(values: np.ndarray, lookback: int = 100) -> float | None:
    valid = values[np.isfinite(values)]
    if not len(valid):
        return None
    window = valid[-lookback:]
    return round(float(np.mean(window <= window[-1]) * 100), 2)


def _mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= period:
        return out
    typical = (high + low + close) / 3
    flow = typical * volume
    direction = np.diff(typical, prepend=np.nan)
    positive = np.where(direction > 0, flow, 0.0)
    negative = np.where(direction < 0, flow, 0.0)
    for i in range(period, len(close)):
        pos = positive[i - period + 1 : i + 1].sum()
        neg = negative[i - period + 1 : i + 1].sum()
        out[i] = 100.0 if neg == 0 and pos > 0 else 0.0 if pos == neg == 0 else 100 - 100 / (1 + pos / neg)
    return out


def _stoch_rsi(close: np.ndarray, rsi_period: int = 14, stoch_period: int = 14) -> np.ndarray:
    base = rsi(close, rsi_period)
    out = np.full(len(close), np.nan)
    for i in range(rsi_period + stoch_period - 1, len(close)):
        window = base[i - stoch_period + 1 : i + 1]
        if np.all(np.isfinite(window)):
            spread = window.max() - window.min()
            out[i] = 0.0 if spread == 0 else 100 * (base[i] - window.min()) / spread
    return out


def _williams_r(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(close), np.nan)
    for i in range(period - 1, len(close)):
        hh = high[i - period + 1 : i + 1].max()
        ll = low[i - period + 1 : i + 1].min()
        out[i] = -50.0 if hh == ll else -100 * (hh - close[i]) / (hh - ll)
    return out


def _anchored_vwap(candles: list[Candle], anchor: str) -> float | None:
    if not candles:
        return None
    last_dt = datetime.fromtimestamp(candles[-1].timestamp / 1000, timezone.utc)
    if anchor == "day":
        key = (last_dt.year, last_dt.timetuple().tm_yday)
        selected = [c for c in candles if (lambda d: (d.year, d.timetuple().tm_yday))(datetime.fromtimestamp(c.timestamp / 1000, timezone.utc)) == key]
    else:
        iso = last_dt.isocalendar()
        key = (iso.year, iso.week)
        selected = [c for c in candles if (lambda d: (d.isocalendar().year, d.isocalendar().week))(datetime.fromtimestamp(c.timestamp / 1000, timezone.utc)) == key]
    total_volume = sum(max(c.volume, 0.0) for c in selected)
    if total_volume <= 0:
        return None
    return round(sum(((c.high + c.low + c.close) / 3) * max(c.volume, 0.0) for c in selected) / total_volume, 8)


def _volume_profile(candles: list[Candle], bins: int = 32, lookback: int = 120) -> dict[str, float | int | str | None]:
    sample = candles[-lookback:]
    if not sample:
        return {"poc": None, "vah": None, "val": None, "bins": bins, "method": "candle_range_uniform"}
    floor, ceiling = min(c.low for c in sample), max(c.high for c in sample)
    if ceiling <= floor:
        return {"poc": round(floor, 8), "vah": round(ceiling, 8), "val": round(floor, 8), "bins": bins, "method": "candle_range_uniform"}
    edges = np.linspace(floor, ceiling, bins + 1)
    bucket_volume = np.zeros(bins)
    for candle in sample:
        touched = np.flatnonzero((edges[:-1] <= candle.high) & (edges[1:] >= candle.low))
        if len(touched):
            bucket_volume[touched] += max(candle.volume, 0.0) / len(touched)
    centers = (edges[:-1] + edges[1:]) / 2
    poc_index = int(np.argmax(bucket_volume))
    target = bucket_volume.sum() * 0.70
    # A value area is a contiguous range around the POC.  Selecting the
    # globally largest buckets can bridge low-volume gaps and substantially
    # overstate the 70% area.
    left=right=poc_index;cumulative=float(bucket_volume[poc_index])
    while cumulative<target and (left>0 or right<bins-1):
        left_volume=float(bucket_volume[left-1]) if left>0 else -1.0
        right_volume=float(bucket_volume[right+1]) if right<bins-1 else -1.0
        if right_volume>left_volume:
            right+=1;cumulative+=float(bucket_volume[right])
        else:
            left-=1;cumulative+=float(bucket_volume[left])
    return {
        "poc": round(float(centers[poc_index]), 8),
        "vah": round(float(edges[right + 1]), 8),
        "val": round(float(edges[left]), 8),
        "bins": bins,
        "method": "candle_range_uniform",
    }


def _rating(candles: list[Candle]) -> dict[str, float | str | bool]:
    if len(candles) < 200:
        return {"score": 0.0, "label": "数据不足", "ready": False}
    _, high, low, close, volume = _arrays(candles)
    votes: list[float] = []
    for period in (10, 20, 30, 50, 100, 200):
        value = ema(close, period)[-1]
        votes.append(1.0 if close[-1] > value else -1.0 if close[-1] < value else 0.0)
    current_rsi = rsi(close)[-1]
    votes.append(1.0 if current_rsi > 55 else -1.0 if current_rsi < 45 else 0.0)
    line, signal, _ = macd(close)
    votes.append(1.0 if line[-1] > signal[-1] else -1.0 if line[-1] < signal[-1] else 0.0)
    wr = _williams_r(high, low, close)[-1]
    votes.append(1.0 if wr > -50 else -1.0 if wr < -50 else 0.0)
    score = round(float(np.mean(votes)), 3)
    label = "强多" if score > 0.5 else "偏多" if score > 0.1 else "强空" if score < -0.5 else "偏空" if score < -0.1 else "中性"
    return {"score": score, "label": label, "ready": True}


def analyze_technical(candles_1h: Iterable[Candle], candles_4h: Iterable[Candle]) -> dict:
    """Return a deterministic, JSON-friendly technical confluence snapshot."""
    one_hour, four_hour = _clean(candles_1h), _clean(candles_4h)
    # All timeframes must represent the same point-in-time snapshot.  A 4H
    # candle opening at T is only knowable at T+4H.
    if one_hour:
        decision_at=one_hour[-1].timestamp+3_600_000
        four_hour=[c for c in four_hour if c.timestamp+14_400_000<=decision_at]
    warnings: list[str] = []
    if len(one_hour) < 200 or len(four_hour) < 200:
        warnings.append("至少需要200根已收盘的1H和4H K线完成指标预热")
        return {
            "ready": False, "group_scores": {name: 0.0 for name in GROUP_CAPS}, "group_caps": GROUP_CAPS,
            "technical_score": 0.0, "rating_1h": _rating(one_hour), "rating_4h": _rating(four_hour),
            "levels": {"donchian_support": None, "donchian_resistance": None, "volume_profile": _volume_profile(one_hour)},
            "volume_price": {"daily_vwap": _anchored_vwap(one_hour, "day"), "weekly_vwap": _anchored_vwap(one_hour, "week"), "mfi": None},
            "momentum": {"stoch_rsi": None, "williams_r": None},
            "volatility": {"atr": None, "atr_percentile": None, "bollinger_width_percentile": None, "phase": "UNKNOWN"},
            "conflicts": [], "risk_overlay": {"block_new_entries": True, "position_scale": 0.0, "reasons": warnings}, "warnings": warnings,
        }

    _, h1, l1, c1, v1 = _arrays(one_hour)
    _, h4, l4, c4, _ = _arrays(four_hour)
    atr_values = atr(h1, l1, c1)
    atr_ratio = np.divide(atr_values, c1, out=np.full(len(c1), np.nan), where=c1 != 0)
    upper, middle, lower = bollinger(c1)
    bb_width = np.divide(upper - lower, middle, out=np.full(len(c1), np.nan), where=middle != 0)
    atr_pct, bb_pct = _percentile_rank(atr_ratio), _percentile_rank(bb_width)
    phase = "COMPRESSION" if (atr_pct or 0) <= 35 and (bb_pct or 0) <= 20 else "EXTREME" if max(atr_pct or 0, bb_pct or 0) >= 85 else "EXPANSION" if max(atr_pct or 0, bb_pct or 0) >= 65 else "NORMAL"

    ema20_1, ema50_1, ema200_1 = ema(c1, 20)[-1], ema(c1, 50)[-1], ema(c1, 200)[-1]
    ema50_4, ema200_4 = ema(c4, 50)[-1], ema(c4, 200)[-1]
    trend_votes = [np.sign(ema20_1 - ema50_1), np.sign(c1[-1] - ema200_1), 1.5 * np.sign(ema50_4 - ema200_4), np.sign(c4[-1] - ema50_4)]
    trend_score = _clip(sum(trend_votes) / 4.5 * GROUP_CAPS["trend"], GROUP_CAPS["trend"])

    resistance, support = float(h1[-21:-1].max()), float(l1[-21:-1].min())
    structure_raw = 1.0 if c1[-1] > resistance else -1.0 if c1[-1] < support else float(np.clip(2 * (c1[-1] - support) / max(resistance - support, 1e-12) - 1, -0.5, 0.5))
    recent_high, prior_high = h1[-5:].max(), h1[-10:-5].max()
    recent_low, prior_low = l1[-5:].min(), l1[-10:-5].min()
    structure_raw += 0.35 * (1 if recent_high > prior_high and recent_low > prior_low else -1 if recent_high < prior_high and recent_low < prior_low else 0)
    structure_score = _clip(structure_raw / 1.35 * GROUP_CAPS["structure"], GROUP_CAPS["structure"])

    daily_vwap, weekly_vwap = _anchored_vwap(one_hour, "day"), _anchored_vwap(one_hour, "week")
    mfi_values = _mfi(h1, l1, c1, v1)
    mfi_value = _last_finite(mfi_values)
    vwma20 = float(np.sum(c1[-20:] * v1[-20:]) / np.sum(v1[-20:])) if np.sum(v1[-20:]) > 0 else float("nan")
    sma20 = sma(c1, 20)[-1]
    obv_values = obv(c1, v1)
    volume_votes = [np.sign(c1[-1] - daily_vwap) if daily_vwap else 0, np.sign(c1[-1] - weekly_vwap) if weekly_vwap else 0, np.sign(vwma20 - sma20), np.sign(obv_values[-1] - obv_values[-10])]
    volume_score = _clip(sum(volume_votes) / 4 * GROUP_CAPS["volume_price"], GROUP_CAPS["volume_price"])

    rsi_value = float(rsi(c1)[-1]); stoch_values = _stoch_rsi(c1); wr_values = _williams_r(h1, l1, c1)
    stoch_value, wr_value = _last_finite(stoch_values), _last_finite(wr_values)
    momentum_votes = [float(np.clip((rsi_value - 50) / 20, -1, 1)), float(np.clip(((stoch_value or 50) - 50) / 50, -1, 1)), float(np.clip(((wr_value or -50) + 50) / 50, -1, 1))]
    momentum_score = _clip(sum(momentum_votes) / 3 * GROUP_CAPS["momentum"], GROUP_CAPS["momentum"])

    # Volatility changes risk and entry quality, not direction.  Keep it in
    # the risk overlay instead of casting a second momentum vote.
    volatility_score = 0.0
    scores = {"trend": trend_score, "structure": structure_score, "volume_price": volume_score, "momentum": momentum_score, "volatility": volatility_score}
    total = _clip(sum(scores.values()), 100)

    conflicts: list[str] = []
    directional = [score for name, score in scores.items() if abs(score) >= 0.35 * GROUP_CAPS[name]]
    if any(x > 0 for x in directional) and any(x < 0 for x in directional):
        conflicts.append("技术分组方向不一致，降低信号可信度")
    rating_1h, rating_4h = _rating(one_hour), _rating(four_hour)
    if rating_1h["score"] * rating_4h["score"] < 0:
        conflicts.append("1H与4H评级方向相反")
    risk_reasons: list[str] = []
    position_scale = 1.0
    if phase == "EXTREME":
        position_scale = 0.5; risk_reasons.append("波动处于极端分位，建议仓位减半并避免追价")
    elif phase == "COMPRESSION":
        position_scale = 0.75; risk_reasons.append("波动压缩，等待放量突破确认")
    if conflicts:
        position_scale = min(position_scale, 0.75); risk_reasons.append("存在技术面冲突")

    return {
        "ready": True, "as_of": one_hour[-1].timestamp, "group_scores": scores, "group_caps": GROUP_CAPS,
        "technical_score": total, "rating_1h": rating_1h, "rating_4h": rating_4h,
        "levels": {"donchian_support": round(support, 8), "donchian_resistance": round(resistance, 8), "volume_profile": _volume_profile(one_hour)},
        "volume_price": {"daily_vwap": daily_vwap, "weekly_vwap": weekly_vwap, "vwma20": round(vwma20, 8), "mfi": mfi_value},
        "momentum": {"rsi": round(rsi_value, 8), "stoch_rsi": stoch_value, "williams_r": wr_value},
        "volatility": {"atr": _last_finite(atr_values), "atr_percentile": atr_pct, "bollinger_width_percentile": bb_pct, "phase": phase},
        "conflicts": conflicts, "risk_overlay": {"block_new_entries": False, "position_scale": position_scale, "reasons": risk_reasons}, "warnings": warnings,
    }
