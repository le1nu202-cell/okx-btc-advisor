from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class APIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda s: s.split("_")[0] + "".join(p.title() for p in s.split("_")[1:]),
        populate_by_name=True,
        allow_inf_nan=False,
    )


class MarketRegime(str, Enum):
    TREND = "TREND"
    RANGE = "RANGE"
    TRANSITION = "TRANSITION"
    STALE = "STALE"


class AdviceAction(str, Enum):
    LONG_CANDIDATE = "LONG_CANDIDATE"
    SHORT_CANDIDATE = "SHORT_CANDIDATE"
    WATCH_LONG = "WATCH_LONG"
    WATCH_SHORT = "WATCH_SHORT"
    WAIT = "WAIT"


class Candle(APIModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    volume_ccy: float | None = None
    confirm: bool = True
    timeframe: str = "1H"


class IndicatorContribution(APIModel):
    name: str
    score: float
    value: float | None = None
    explanation: str


class DataQuality(APIModel):
    fresh: bool
    last_candle_at: int | None = None
    funding_available: bool = False
    open_interest_available: bool = False
    warnings: list[str] = Field(default_factory=list)


class SignalAdvice(APIModel):
    instrument: str = "BTC-USDT-SWAP"
    strategy: str
    candle_close_at: int
    regime: MarketRegime
    action: AdviceAction
    direction_score: float = Field(ge=-100, le=100)
    technical_score: float = Field(default=0, ge=-85, le=85)
    news_score: float = Field(default=0, ge=-15, le=15)
    news_analysis: dict[str, Any] | None = None
    confidence: float = Field(ge=0, le=100)
    contributions: list[IndicatorContribution] = Field(default_factory=list)
    trigger_price: float | None = None
    invalidation: str
    stop_loss: float | None = None
    targets: list[float] = Field(default_factory=list)
    risk_reward: list[float] = Field(default_factory=list)
    explanation: str
    data_quality: DataQuality
    config_version: str = "research-v2-unvalidated"
    created_at: str = Field(default_factory=utc_now_iso)


class RiskEstimate(APIModel):
    equity: float | None = None
    risk_percent: float | None = None
    leverage: float | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    stop_distance: float | None = None
    reference_notional: float | None = None
    quantity_btc: float | None = None
    warnings: list[str] = Field(default_factory=list)


class Settings(APIModel):
    equity: float | None = Field(default=None, gt=0)
    risk_percent: float | None = Field(default=None, ge=0.1, le=2.0)
    leverage: float | None = Field(default=None, ge=1.0, le=2.0)
    notifications_enabled: bool = False
    fee_bps: float = Field(default=5.0, ge=0, le=100)
    slippage_bps: float = Field(default=5.0, ge=0, le=100)
    custom_parameters: dict[str, float] = Field(default_factory=dict)

    @field_validator("custom_parameters")
    @classmethod
    def finite_values(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > 30 or any(not (-1e6 < float(v) < 1e6) for v in value.values()):
            raise ValueError("自定义参数无效")
        if value:
            raise ValueError("当前版本尚未将自定义策略参数接入计算，因此不接受非空自定义参数")
        return value


class CandleSeriesStatus(APIModel):
    available: bool = False
    stale: bool = True
    last_at: int | None = None
    gap_detected: bool = False


class MarketSnapshot(APIModel):
    instrument: str
    price: float | None
    updated_at: str
    stale: bool
    candles_1m: list[Candle] = Field(default_factory=list)
    candles_15m: list[Candle] = Field(default_factory=list)
    candles_1h: list[Candle]
    candles_4h: list[Candle]
    mark_price: float | None = None
    mark_price_time: int | None = None
    candle_status: dict[str, CandleSeriesStatus] = Field(default_factory=dict)
    funding_rate: float | None
    funding_time: int | None
    open_interest: float | None
    open_interest_time: int | None
    connection_status: str


class BacktestRequest(APIModel):
    strategy: str = Field(default="combined", pattern="^(trend|range|combined)$")
    years: int = Field(default=3, ge=1, le=5)
    fee_bps: float = Field(default=5, ge=0, le=100)
    slippage_bps: float = Field(default=5, ge=0, le=100)


class BacktestStatus(APIModel):
    id: str
    status: str
    progress: float = 0
    message: str = ""
    result: dict[str, Any] | None = None
