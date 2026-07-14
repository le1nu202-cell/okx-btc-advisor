from __future__ import annotations

import math
import random
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from .models import APIModel, Candle, MarketRegime


INSTRUMENT = "BTC-USDT-SWAP"


class TradeDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class MarginMode(str, Enum):
    ISOLATED = "ISOLATED"
    CROSS = "CROSS"


class MaintenanceMarginSource(str, Enum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class SizingMode(str, Enum):
    MARGIN = "MARGIN"
    MAX_LOSS = "MAX_LOSS"


class TradeState(str, Enum):
    IDLE = "IDLE"
    PLANNED = "PLANNED"
    INITIAL_OPEN = "INITIAL_OPEN"
    APPROACHING_ADD = "APPROACHING_ADD"
    ADDED = "ADDED"
    REDUCE_ZONE = "REDUCE_ZONE"
    PARTIALLY_REDUCED = "PARTIALLY_REDUCED"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = {TradeState.TAKE_PROFIT, TradeState.STOPPED, TradeState.CANCELLED}

# v0.4 initially persisted two market-reminder phases in the same field as
# manually confirmed execution states. Keep accepting those values so existing
# local SQLite payloads remain readable, but normalize them at live-plan
# boundaries. Replay has its own simulated state machine and intentionally does
# not use this helper.
LEGACY_LIVE_STATE_MAP = {
    TradeState.APPROACHING_ADD: TradeState.INITIAL_OPEN,
    TradeState.REDUCE_ZONE: TradeState.ADDED,
}


def canonical_live_execution_state(value: TradeState | str) -> TradeState:
    state = value if isinstance(value, TradeState) else TradeState(value)
    return LEGACY_LIVE_STATE_MAP.get(state, state)


def normalize_live_execution_state(record: dict[str, Any]) -> dict[str, Any]:
    """Lazily repair legacy live reminder states without a schema migration."""
    current = TradeState(record["state"])
    canonical = canonical_live_execution_state(current)
    if canonical == current:
        return record
    return {**record, "state": canonical.value}


class TradeAction(str, Enum):
    CONFIRM_INITIAL = "CONFIRM_INITIAL"
    CONFIRM_ADD = "CONFIRM_ADD"
    CONFIRM_REDUCE = "CONFIRM_REDUCE"
    CONFIRM_TAKE_PROFIT = "CONFIRM_TAKE_PROFIT"
    CONFIRM_STOP = "CONFIRM_STOP"
    CANCEL = "CANCEL"


class TradePlanDraft(APIModel):
    instrument: str = Field(default=INSTRUMENT, pattern="^BTC-USDT-SWAP$")
    direction: TradeDirection = TradeDirection.LONG
    initial_entry_price: float | None = Field(default=None, ge=1e-8, le=1_000_000_000)
    add_price: float | None = Field(default=None, ge=1e-8, le=1_000_000_000)
    stop_price: float | None = Field(default=None, ge=1e-8, le=1_000_000_000)
    take_profit_price: float | None = Field(default=None, ge=1e-8, le=1_000_000_000)
    equity: float = Field(default=80.0, gt=0, le=1_000_000_000)
    leverage: float = Field(default=66.0, ge=1, le=125)
    initial_margin: float | None = Field(default=None, gt=0, le=1_000_000_000)
    initial_margin_percent: float = Field(default=4.0, gt=0, le=100)
    add_multiplier: float = Field(default=2.0, gt=0, le=10)
    maker_fee_bps: float = Field(default=2.0, ge=0, le=100)
    taker_fee_bps: float = Field(default=5.0, ge=0, le=100)
    slippage_bps: float = Field(default=5.0, ge=0, le=500)
    margin_mode: MarginMode = MarginMode.CROSS
    cross_available_equity: float | None = Field(default=None, ge=0, le=1_000_000_000)
    extra_margin_usdt: float = Field(default=0.0, ge=0, le=1_000_000_000)
    maintenance_margin_source: MaintenanceMarginSource = MaintenanceMarginSource.AUTO
    maintenance_margin_rate: float | None = Field(default=None, ge=0, lt=1)
    maintenance_margin_fixed_usdt: float = Field(default=0.0, ge=0, le=1_000_000_000)
    liquidation_fee_bps: float = Field(default=5.0, ge=0, le=1_000)
    include_unsettled_funding: bool = False
    unsettled_funding_usdt: float = Field(default=0.0, ge=-1_000_000_000, le=1_000_000_000)
    assume_no_other_positions: bool = True
    sizing_mode: SizingMode = SizingMode.MARGIN
    max_loss_usdt: float | None = Field(default=None, gt=0, le=1_000_000_000)
    loss_limit_usdt: float | None = Field(default=None, gt=0, le=1_000_000_000)
    risk_low_max_percent: float = Field(default=3.0, ge=0, le=100)
    risk_medium_max_percent: float = Field(default=5.0, ge=0, le=100)
    risk_high_max_percent: float = Field(default=8.0, ge=0, le=100)
    approach_threshold_percent: float = Field(default=0.25, gt=0, le=5)
    notes: str = Field(default="", max_length=4000)
    screenshot_path: str | None = Field(default=None, max_length=1000)

    @field_validator("screenshot_path")
    @classmethod
    def local_screenshot_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if "://" in value or value.startswith("\\\\"):
            raise ValueError("截图路径只能是本机路径，不能是网址或网络共享")
        return value

    @model_validator(mode="after")
    def ordered_risk_thresholds(self):
        if not (self.risk_low_max_percent <= self.risk_medium_max_percent <= self.risk_high_max_percent):
            raise ValueError("风险等级阈值必须依次递增")
        if self.sizing_mode == SizingMode.MAX_LOSS and self.max_loss_usdt is None:
            raise ValueError("按最大亏损反推仓位时必须填写最大亏损")
        return self


class AdverseMoveLoss(APIModel):
    move_percent: float
    loss_usdt: float
    equity_percent: float


class RiskCalculation(APIModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requested_initial_margin: float | None = None
    initial_margin: float | None = None
    initial_margin_percent: float | None = None
    affordable_initial_margin: float | None = None
    max_initial_margin_by_loss: float | None = None
    initial_notional: float | None = None
    initial_quantity_btc: float | None = None
    add_margin: float | None = None
    add_notional: float | None = None
    add_quantity_btc: float | None = None
    total_margin: float | None = None
    total_notional: float | None = None
    total_quantity_btc: float | None = None
    remaining_quantity_after_planned_reduce: float | None = None
    initial_fill_price: float | None = None
    add_fill_price: float | None = None
    average_entry_price: float | None = None
    gross_breakeven_price: float | None = None
    fee_breakeven_price: float | None = None
    full_cost_breakeven_price: float | None = None
    all_in_breakeven_price: float | None = None
    fee_adjusted_breakeven_price: float | None = None
    reduce_zone_price: float | None = None
    opening_fee: float | None = None
    add_fee: float | None = None
    estimated_reduce_fee: float | None = None
    estimated_close_fee: float | None = None
    total_fees_at_take_profit: float | None = None
    total_fees_at_stop: float | None = None
    estimated_slippage_at_take_profit: float | None = None
    estimated_slippage_at_stop: float | None = None
    gross_profit_at_take_profit: float | None = None
    net_profit_at_take_profit: float | None = None
    initial_only_net_profit_at_take_profit: float | None = None
    gross_loss_at_stop: float | None = None
    net_loss_at_stop: float | None = None
    max_loss_equity_percent: float | None = None
    risk_reward_ratio: float | None = None
    stop_distance_percent: float | None = None
    add_to_stop_space_percent: float | None = None
    adverse_move_losses: list[AdverseMoveLoss] = Field(default_factory=list)
    risk_level: str = "未知"
    loss_limit_exceeded: bool = False
    liquidation_warning: bool = False
    liquidation_scenarios: dict[str, Any] | None = None
    assumptions: list[str] = Field(default_factory=list)


class TradeActionRequest(APIModel):
    action: TradeAction
    price: float | None = Field(default=None, ge=1e-8, le=1_000_000_000)
    quantity_btc: float | None = Field(default=None, ge=1e-12, le=1_000_000_000_000_000_000)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def confirmed_fill_is_complete(self):
        if self.action != TradeAction.CANCEL and (self.price is None or self.quantity_btc is None):
            raise ValueError("人工确认成交必须同时提供实际价格和实际 BTC 数量")
        return self


class ReplayCreateRequest(APIModel):
    mode: str = Field(default="random", pattern="^(random|manual)$")
    start_at: int | None = Field(default=None, gt=0)


class ReplayPlanRequest(APIModel):
    plan: TradePlanDraft


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return str(uuid.uuid4())


def validate_price_order(plan: TradePlanDraft) -> list[str]:
    prices = (
        plan.initial_entry_price,
        plan.add_price,
        plan.stop_price,
        plan.take_profit_price,
    )
    if any(value is None for value in prices):
        return ["请完整填写初始开仓价、第一压力位、第二压力位和止盈价"]
    entry, add, stop, target = (float(value) for value in prices)
    if plan.direction == TradeDirection.LONG:
        if not target > entry > add > stop > 0:
            return ["做多价格顺序必须为：止盈价 > 初始开仓价 > 加仓价 > 硬止损价"]
    elif not target < entry < add < stop:
        return ["做空价格顺序必须为：止盈价 < 初始开仓价 < 加仓价 < 硬止损价"]
    return []


def _entry_fill(price: float, direction: TradeDirection, slippage: float) -> float:
    return price * (1 + slippage if direction == TradeDirection.LONG else 1 - slippage)


def _exit_fill(price: float, direction: TradeDirection, slippage: float) -> float:
    return price * (1 - slippage if direction == TradeDirection.LONG else 1 + slippage)


def _signed_pnl(direction: TradeDirection, entry_cost: float, exit_value: float) -> float:
    return exit_value - entry_cost if direction == TradeDirection.LONG else entry_cost - exit_value


def _risk_level(percent: float, plan: TradePlanDraft) -> str:
    if percent <= plan.risk_low_max_percent:
        return "低"
    if percent <= plan.risk_medium_max_percent:
        return "中"
    if percent <= plan.risk_high_max_percent:
        return "高"
    return "极高"


def _core_calculation(plan: TradePlanDraft, initial_margin: float) -> dict[str, Any]:
    assert plan.initial_entry_price and plan.add_price and plan.stop_price and plan.take_profit_price
    maker = plan.maker_fee_bps / 10_000
    taker = plan.taker_fee_bps / 10_000
    slip = plan.slippage_bps / 10_000
    initial_fill = _entry_fill(plan.initial_entry_price, plan.direction, slip)
    add_fill = _entry_fill(plan.add_price, plan.direction, slip)
    initial_notional = initial_margin * plan.leverage
    add_margin = initial_margin * plan.add_multiplier
    add_notional = add_margin * plan.leverage
    q0 = initial_notional / initial_fill
    qa = add_notional / add_fill
    total_q = q0 + qa
    entry_cost = q0 * initial_fill + qa * add_fill
    reference_entry_cost = q0 * plan.initial_entry_price + qa * plan.add_price
    average = entry_cost / total_q
    entry_fees = initial_notional * maker + add_notional * taker
    if plan.direction == TradeDirection.LONG:
        fee_breakeven = (entry_cost + entry_fees) / (total_q * (1 - taker))
    else:
        fee_breakeven = (entry_cost - entry_fees) / (total_q * (1 + taker))
    exit_factor = 1 - slip if plan.direction == TradeDirection.LONG else 1 + slip
    full_cost_breakeven = fee_breakeven / exit_factor
    reduce_fill = _exit_fill(full_cost_breakeven, plan.direction, slip)
    target_fill = _exit_fill(plan.take_profit_price, plan.direction, slip)
    stop_fill = _exit_fill(plan.stop_price, plan.direction, slip)
    reduce_value = qa * reduce_fill
    final_value = q0 * target_fill
    reference_tp_exit_value = qa * full_cost_breakeven + q0 * plan.take_profit_price
    gross_tp = _signed_pnl(plan.direction, reference_entry_cost, reference_tp_exit_value)
    opening_fee = initial_notional * maker
    add_fee = add_notional * taker
    reduce_fee = reduce_value * taker
    close_fee = final_value * taker
    total_tp_fees = opening_fee + add_fee + reduce_fee + close_fee
    entry_slippage = abs(entry_cost - reference_entry_cost)
    tp_exit_slippage = abs(reference_tp_exit_value - (reduce_value + final_value))
    tp_slippage = entry_slippage + tp_exit_slippage
    net_tp = gross_tp - total_tp_fees - tp_slippage
    initial_only_reference_gross = _signed_pnl(
        plan.direction,
        q0 * plan.initial_entry_price,
        q0 * plan.take_profit_price,
    )
    initial_only_slippage = abs(q0 * initial_fill - q0 * plan.initial_entry_price) + abs(q0 * target_fill - q0 * plan.take_profit_price)
    initial_only_net = initial_only_reference_gross - opening_fee - final_value * taker - initial_only_slippage
    stop_value = total_q * stop_fill
    reference_stop_value = total_q * plan.stop_price
    gross_stop_pnl = _signed_pnl(plan.direction, reference_entry_cost, reference_stop_value)
    stop_fee = stop_value * taker
    total_stop_fees = opening_fee + add_fee + stop_fee
    stop_slippage = entry_slippage + abs(reference_stop_value - stop_value)
    net_stop_pnl = gross_stop_pnl - total_stop_fees - stop_slippage
    net_loss = max(0.0, -net_stop_pnl)
    adverse = []
    for move in (0.1, 0.5, 1.0):
        market_price = average * (1 - move / 100 if plan.direction == TradeDirection.LONG else 1 + move / 100)
        exit_price = _exit_fill(market_price, plan.direction, slip)
        exit_value = total_q * exit_price
        reference_value = total_q * market_price
        gross_pnl = _signed_pnl(plan.direction, reference_entry_cost, reference_value)
        move_slippage = entry_slippage + abs(reference_value - exit_value)
        pnl = gross_pnl - opening_fee - add_fee - exit_value * taker - move_slippage
        loss = max(0.0, -pnl)
        adverse.append((move, loss))
    return {
        "initial_fill": initial_fill,
        "add_fill": add_fill,
        "initial_notional": initial_notional,
        "add_margin": add_margin,
        "add_notional": add_notional,
        "q0": q0,
        "qa": qa,
        "total_q": total_q,
        "entry_cost": entry_cost,
        "average": average,
        "fee_breakeven": fee_breakeven,
        "full_cost_breakeven": full_cost_breakeven,
        "opening_fee": opening_fee,
        "add_fee": add_fee,
        "reduce_fee": reduce_fee,
        "close_fee": close_fee,
        "total_tp_fees": total_tp_fees,
        "total_stop_fees": total_stop_fees,
        "tp_slippage": tp_slippage,
        "stop_slippage": stop_slippage,
        "gross_tp": gross_tp,
        "net_tp": net_tp,
        "initial_only_net": initial_only_net,
        "gross_stop_loss": max(0.0, -gross_stop_pnl),
        "net_stop_loss": net_loss,
        "adverse": adverse,
    }


def calculate_risk(plan: TradePlanDraft) -> RiskCalculation:
    errors = validate_price_order(plan)
    requested_margin = plan.initial_margin if plan.initial_margin is not None else plan.equity * plan.initial_margin_percent / 100
    affordable = plan.equity / (1 + plan.add_multiplier)
    if errors:
        return RiskCalculation(
            valid=False,
            errors=errors,
            requested_initial_margin=requested_margin,
            affordable_initial_margin=affordable,
        )
    unit = _core_calculation(plan, 1.0)
    max_by_loss = None
    if plan.max_loss_usdt is not None and unit["net_stop_loss"] > 0:
        max_by_loss = plan.max_loss_usdt / unit["net_stop_loss"]
    margin = requested_margin
    if plan.sizing_mode == SizingMode.MAX_LOSS:
        if max_by_loss is None:
            return RiskCalculation(valid=False, errors=["当前价格与成本无法反推出有效仓位"])
        margin = min(max_by_loss, affordable)
    if not math.isfinite(margin) or margin <= 0:
        return RiskCalculation(valid=False, errors=["初始保证金必须大于零"])
    result = _core_calculation(plan, margin)
    max_loss_percent = result["net_stop_loss"] / plan.equity * 100
    stop_distance = abs(float(plan.stop_price) - result["average"]) / result["average"] * 100
    add_stop_space = abs(float(plan.stop_price) - float(plan.add_price)) / float(plan.add_price) * 100
    warnings: list[str] = []
    total_margin = margin * (1 + plan.add_multiplier)
    if total_margin > plan.equity + 1e-9:
        warnings.append("初始保证金与计划加仓保证金之和超过账户权益，实际可能无法执行")
    liquidation_warning = stop_distance >= 100 / plan.leverage * 0.75
    if liquidation_warning:
        warnings.append("硬止损距离接近或超过高杠杆理论保证金缓冲；实际强平可能先于硬止损，且受维持保证金档位影响")
    if plan.margin_mode == MarginMode.CROSS:
        warnings.append("全仓模式下其他持仓、浮亏与维持保证金会共享权益，本工具无法给出账户级精确强平价")
    else:
        warnings.append("逐仓强平价依赖 OKX 维持保证金档位与实际费用，本工具不把硬止损等同为保证可成交的强平保护")
    limit = plan.loss_limit_usdt if plan.loss_limit_usdt is not None else plan.max_loss_usdt
    exceeded = limit is not None and result["net_stop_loss"] > limit + 1e-9
    if exceeded:
        warnings.append(f"预计硬止损净亏损超过用户设置的 {limit:.4f} USDT 单笔上限")
    assumptions = [
        "初始开仓按 Maker 费率估算；加仓、减仓、止盈和止损按 Taker 费率估算",
        "滑点对开仓按不利方向增加成交价，对平仓按不利方向减少收益",
        "止盈净利润假设先在手续费调整后的减仓区减掉与加仓相同的 BTC 数量，再由初始数量到达止盈价",
        "未计入资金费率、维持保证金档位、强平罚金和订单部分成交",
    ]
    return RiskCalculation(
        valid=True,
        warnings=warnings,
        requested_initial_margin=requested_margin,
        initial_margin=margin,
        initial_margin_percent=margin / plan.equity * 100,
        affordable_initial_margin=affordable,
        max_initial_margin_by_loss=max_by_loss,
        initial_notional=result["initial_notional"],
        initial_quantity_btc=result["q0"],
        add_margin=result["add_margin"],
        add_notional=result["add_notional"],
        add_quantity_btc=result["qa"],
        total_margin=total_margin,
        total_notional=result["initial_notional"] + result["add_notional"],
        total_quantity_btc=result["total_q"],
        remaining_quantity_after_planned_reduce=result["q0"],
        initial_fill_price=result["initial_fill"],
        add_fill_price=result["add_fill"],
        average_entry_price=result["average"],
        gross_breakeven_price=result["average"],
        fee_breakeven_price=result["fee_breakeven"],
        full_cost_breakeven_price=result["full_cost_breakeven"],
        all_in_breakeven_price=result["full_cost_breakeven"],
        fee_adjusted_breakeven_price=result["full_cost_breakeven"],
        reduce_zone_price=result["full_cost_breakeven"],
        opening_fee=result["opening_fee"],
        add_fee=result["add_fee"],
        estimated_reduce_fee=result["reduce_fee"],
        estimated_close_fee=result["close_fee"],
        total_fees_at_take_profit=result["total_tp_fees"],
        total_fees_at_stop=result["total_stop_fees"],
        estimated_slippage_at_take_profit=result["tp_slippage"],
        estimated_slippage_at_stop=result["stop_slippage"],
        gross_profit_at_take_profit=result["gross_tp"],
        net_profit_at_take_profit=result["net_tp"],
        initial_only_net_profit_at_take_profit=result["initial_only_net"],
        gross_loss_at_stop=result["gross_stop_loss"],
        net_loss_at_stop=result["net_stop_loss"],
        max_loss_equity_percent=max_loss_percent,
        risk_reward_ratio=result["net_tp"] / result["net_stop_loss"] if result["net_stop_loss"] > 0 else None,
        stop_distance_percent=stop_distance,
        add_to_stop_space_percent=add_stop_space,
        adverse_move_losses=[
            AdverseMoveLoss(move_percent=move, loss_usdt=loss, equity_percent=loss / plan.equity * 100)
            for move, loss in result["adverse"]
        ],
        risk_level=_risk_level(max_loss_percent, plan),
        loss_limit_exceeded=exceeded,
        liquidation_warning=liquidation_warning,
        assumptions=assumptions,
    )


def _fill_object(value: Any, default_quantity: float = 0.0) -> dict[str, float] | None:
    """Read both v0.4 fill objects and legacy numeric prices."""
    confirmed_at: float | None = None
    if isinstance(value, dict):
        try:
            price = float(value.get("price"))
            quantity = float(value.get("quantityBtc", value.get("quantity_btc")))
            raw_confirmed_at = value.get("confirmedAt", value.get("confirmed_at"))
            if raw_confirmed_at is not None:
                candidate = float(raw_confirmed_at)
                if math.isfinite(candidate) and candidate > 0:
                    confirmed_at = int(candidate)
        except (TypeError, ValueError, OverflowError):
            return None
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        price, quantity = float(value), float(default_quantity)
    else:
        return None
    if not all(math.isfinite(item) and item > 0 for item in (price, quantity)):
        return None
    fill = {"price": price, "quantityBtc": quantity}
    if confirmed_at is not None:
        fill["confirmedAt"] = confirmed_at
    return fill


def normalized_actual_fills(record: dict[str, Any]) -> dict[str, dict[str, float]]:
    risk = RiskCalculation.model_validate(record["risk"])
    source = dict(record.get("actualFills") or {})
    result: dict[str, dict[str, float]] = {}
    for key, default in (
        ("initial", float(risk.initial_quantity_btc or 0)),
        ("add", float(risk.add_quantity_btc or 0)),
    ):
        fill = _fill_object(source.get(key), default)
        if fill:
            result[key] = fill
    opened = sum(fill["quantityBtc"] for fill in result.values())
    reduce_fill = _fill_object(source.get("reduce"), min(float(risk.add_quantity_btc or 0), opened))
    if reduce_fill:
        result["reduce"] = reduce_fill
    exit_default = max(0.0, opened - (reduce_fill["quantityBtc"] if reduce_fill else 0.0))
    exit_fill = _fill_object(source.get("exit"), exit_default)
    if exit_fill:
        result["exit"] = exit_fill
    return result


def _adverse_slippage(
    direction: TradeDirection,
    opening: bool,
    planned_price: float,
    actual_price: float,
    quantity: float,
) -> float:
    if direction == TradeDirection.LONG:
        per_btc = actual_price - planned_price if opening else planned_price - actual_price
    else:
        per_btc = planned_price - actual_price if opening else actual_price - planned_price
    return max(0.0, per_btc * quantity)


def recompute_execution(record: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the actual cost pool and realized segments from persisted fills."""
    plan = TradePlanDraft.model_validate(record["plan"])
    risk = RiskCalculation.model_validate(record["risk"])
    fills = normalized_actual_fills(record)
    initial = fills.get("initial")
    add = fills.get("add")
    opened_quantity = sum(fill["quantityBtc"] for fill in (initial, add) if fill)
    entry_cost = sum(fill["price"] * fill["quantityBtc"] for fill in (initial, add) if fill)
    average = entry_cost / opened_quantity if opened_quantity > 0 else None
    maker = plan.maker_fee_bps / 10_000
    taker = plan.taker_fee_bps / 10_000
    total_entry_fees = (
        (initial["price"] * initial["quantityBtc"] * maker if initial else 0.0)
        + (add["price"] * add["quantityBtc"] * taker if add else 0.0)
    )
    entry_fee_per_btc = total_entry_fees / opened_quantity if opened_quantity > 0 else 0.0
    remaining = opened_quantity
    realized_gross = 0.0
    allocated_entry_fees = 0.0
    exit_fees = 0.0
    segments: list[dict[str, Any]] = []
    for key in ("reduce", "exit"):
        fill = fills.get(key)
        if not fill or average is None:
            continue
        quantity = min(fill["quantityBtc"], remaining)
        if quantity <= 0:
            continue
        gross = _signed_pnl(plan.direction, average * quantity, fill["price"] * quantity)
        allocated = entry_fee_per_btc * quantity
        closing_fee = fill["price"] * quantity * taker
        if key == "reduce":
            planned_exit = float(risk.reduce_zone_price or fill["price"])
            action_name = TradeAction.CONFIRM_REDUCE.value
        else:
            state = TradeState(record["state"])
            planned_exit = float(plan.take_profit_price if state == TradeState.TAKE_PROFIT else plan.stop_price)
            action_name = TradeAction.CONFIRM_TAKE_PROFIT.value if state == TradeState.TAKE_PROFIT else TradeAction.CONFIRM_STOP.value
        segment_slippage = _adverse_slippage(plan.direction, False, planned_exit, fill["price"], quantity)
        next_remaining = remaining - quantity
        segment = {
            "action": action_name,
            "type": key,
            "price": fill["price"],
            "quantityBtc": quantity,
            "entryCost": average * quantity,
            "grossPnl": gross,
            "allocatedEntryFees": allocated,
            "exitFees": closing_fee,
            "fees": allocated + closing_fee,
            "slippageUsdt": segment_slippage,
            "netPnl": gross - allocated - closing_fee,
            "remainingQuantityBtc": max(0.0, next_remaining),
        }
        segments.append(segment)
        realized_gross += gross
        allocated_entry_fees += allocated
        exit_fees += closing_fee
        remaining = next_remaining
    slippage = 0.0
    if initial and plan.initial_entry_price:
        slippage += _adverse_slippage(plan.direction, True, plan.initial_entry_price, initial["price"], initial["quantityBtc"])
    if add and plan.add_price:
        slippage += _adverse_slippage(plan.direction, True, plan.add_price, add["price"], add["quantityBtc"])
    reduce = fills.get("reduce")
    if reduce and risk.reduce_zone_price:
        slippage += _adverse_slippage(plan.direction, False, risk.reduce_zone_price, reduce["price"], reduce["quantityBtc"])
    final_fill = fills.get("exit")
    if final_fill:
        state = TradeState(record["state"])
        reference = plan.take_profit_price if state == TradeState.TAKE_PROFIT else plan.stop_price
        if reference:
            slippage += _adverse_slippage(plan.direction, False, reference, final_fill["price"], final_fill["quantityBtc"])
    execution = {
        "openedQuantityBtc": opened_quantity,
        "remainingQuantityBtc": max(0.0, remaining),
        "averageEntryPrice": average,
        "remainingEntryCost": (average or 0.0) * max(0.0, remaining),
        "realizedGrossPnl": realized_gross,
        "allocatedEntryFees": allocated_entry_fees,
        "exitFees": exit_fees,
        "fees": allocated_entry_fees + exit_fees,
        "incurredFees": total_entry_fees + exit_fees,
        "slippageUsdt": slippage,
        "realizedNetPnl": realized_gross - allocated_entry_fees - exit_fees,
        "mfeMaeSupported": "reduce" not in fills,
    }
    remaining_entry_fee = max(0.0, total_entry_fees - allocated_entry_fees)
    execution_risk: dict[str, Any] = {
        "quantityBtc": max(0.0, remaining),
        "averageEntryPrice": average if remaining > 0 else None,
        "grossBreakevenPrice": average if remaining > 0 else None,
        "feeBreakevenPrice": None,
        "fullCostBreakevenPrice": None,
        "allInBreakevenPrice": None,
        "feeAdjustedBreakevenPrice": None,
        "remainingEntryFee": remaining_entry_fee,
        "remainingEntryCost": (average or 0.0) * max(0.0, remaining),
        "estimatedExitFeeAtStop": 0.0,
        "estimatedExitSlippageAtStop": 0.0,
        "remainingNetPnlAtStop": 0.0,
        "remainingNetLossAtStop": 0.0,
        "netLossAtStop": 0.0,
        "maxLossEquityPercent": max(0.0, -execution["realizedNetPnl"]) / plan.equity * 100,
        "totalNetPnlIfStopped": execution["realizedNetPnl"],
    }
    if remaining > 0 and average is not None:
        remaining_cost = average * remaining
        # These breakeven prices describe the whole trade after any realized
        # reduce segment. A realized loss must be recovered by the remaining
        # position; a realized profit lowers the price required to finish the
        # complete trade at breakeven.
        direction_sign = 1.0 if plan.direction == TradeDirection.LONG else -1.0
        gross_breakeven = (remaining_cost - direction_sign * execution["realizedGrossPnl"]) / remaining
        if plan.direction == TradeDirection.LONG:
            fee_breakeven = (remaining_cost + remaining_entry_fee - execution["realizedNetPnl"]) / (remaining * (1 - taker))
        else:
            fee_breakeven = (remaining_cost - remaining_entry_fee + execution["realizedNetPnl"]) / (remaining * (1 + taker))
        exit_factor = 1 - plan.slippage_bps / 10_000 if plan.direction == TradeDirection.LONG else 1 + plan.slippage_bps / 10_000
        full_cost_breakeven = fee_breakeven / exit_factor
        stop_market = float(plan.stop_price)
        stop_fill = _exit_fill(stop_market, plan.direction, plan.slippage_bps / 10_000)
        stop_value = remaining * stop_fill
        stop_exit_fee = stop_value * taker
        remaining_stop_net = _signed_pnl(plan.direction, remaining_cost, stop_value) - remaining_entry_fee - stop_exit_fee
        total_if_stopped = execution["realizedNetPnl"] + remaining_stop_net
        execution_risk.update({
            "grossBreakevenPrice": gross_breakeven,
            "feeBreakevenPrice": fee_breakeven,
            "fullCostBreakevenPrice": full_cost_breakeven,
            "allInBreakevenPrice": full_cost_breakeven,
            "feeAdjustedBreakevenPrice": full_cost_breakeven,
            "estimatedExitFeeAtStop": stop_exit_fee,
            "estimatedExitSlippageAtStop": abs(stop_market - stop_fill) * remaining,
            "remainingNetPnlAtStop": remaining_stop_net,
            "remainingNetLossAtStop": max(0.0, -remaining_stop_net),
            "netLossAtStop": max(0.0, -total_if_stopped),
            "maxLossEquityPercent": max(0.0, -total_if_stopped) / plan.equity * 100,
            "totalNetPnlIfStopped": total_if_stopped,
        })
    execution_risk["riskLevel"] = _risk_level(float(execution_risk["maxLossEquityPercent"]), plan)
    updated = dict(record)
    updated["actualFills"] = fills
    updated["realizedSegments"] = segments
    updated["execution"] = execution
    updated["executionRisk"] = execution_risk
    if not execution["mfeMaeSupported"]:
        updated["mfeUsdt"] = None
        updated["maeUsdt"] = None
    return updated


def make_plan_record(plan: TradePlanDraft) -> dict[str, Any]:
    risk = calculate_risk(plan)
    if not risk.valid:
        raise ValueError("；".join(risk.errors))
    now = now_ms()
    record = {
        "id": new_id(),
        "instrument": INSTRUMENT,
        "state": TradeState.PLANNED.value,
        "plan": plan.model_dump(by_alias=True, mode="json"),
        "risk": risk.model_dump(by_alias=True, mode="json"),
        "addCount": 0,
        "actualFills": {},
        "mfeUsdt": 0.0,
        "maeUsdt": 0.0,
        "createdAt": now,
        "updatedAt": now,
    }
    return recompute_execution(record)


def apply_action(record: dict[str, Any], request: TradeActionRequest) -> tuple[dict[str, Any], str]:
    current = TradeState(record["state"])
    if current in TERMINAL_STATES:
        raise ValueError("交易计划已经结束，不能继续变更状态")
    action = request.action
    allowed = {
        TradeAction.CONFIRM_INITIAL: ({TradeState.PLANNED}, TradeState.INITIAL_OPEN, "initial"),
        TradeAction.CONFIRM_ADD: ({TradeState.INITIAL_OPEN, TradeState.APPROACHING_ADD}, TradeState.ADDED, "add"),
        TradeAction.CONFIRM_REDUCE: ({TradeState.ADDED, TradeState.REDUCE_ZONE}, TradeState.PARTIALLY_REDUCED, "reduce"),
        TradeAction.CONFIRM_TAKE_PROFIT: ({TradeState.INITIAL_OPEN, TradeState.APPROACHING_ADD, TradeState.ADDED, TradeState.REDUCE_ZONE, TradeState.PARTIALLY_REDUCED}, TradeState.TAKE_PROFIT, "exit"),
        TradeAction.CONFIRM_STOP: ({TradeState.INITIAL_OPEN, TradeState.APPROACHING_ADD, TradeState.ADDED, TradeState.REDUCE_ZONE, TradeState.PARTIALLY_REDUCED}, TradeState.STOPPED, "exit"),
        TradeAction.CANCEL: ({TradeState.PLANNED}, TradeState.CANCELLED, "cancel"),
    }
    states, target, fill_key = allowed[action]
    if current not in states:
        raise ValueError(f"状态 {current.value} 不能执行 {action.value}")
    if action == TradeAction.CONFIRM_ADD and int(record.get("addCount", 0)) >= 1:
        raise ValueError("每笔计划最多只允许一次加仓")
    if action != TradeAction.CANCEL and (request.price is None or request.quantity_btc is None):
        raise ValueError("人工确认成交必须同时提供实际价格和实际 BTC 数量")

    fills = normalized_actual_fills(record)
    execution = recompute_execution(record)["execution"]
    remaining = float(execution.get("remainingQuantityBtc") or 0)
    quantity = float(request.quantity_btc or 0)
    if action == TradeAction.CONFIRM_INITIAL and "initial" in fills:
        raise ValueError("初始开仓已经确认，不能重复记录")
    if action in {TradeAction.CONFIRM_REDUCE, TradeAction.CONFIRM_TAKE_PROFIT, TradeAction.CONFIRM_STOP}:
        if remaining <= 0:
            raise ValueError("当前没有可减仓或退出的剩余 BTC 数量")
        tolerance = max(1e-12, remaining * 1e-9)
        if quantity > remaining + tolerance:
            raise ValueError(f"实际成交数量不能超过剩余仓位 {remaining:.12f} BTC")
        if action == TradeAction.CONFIRM_REDUCE and quantity >= remaining - tolerance:
            raise ValueError("部分减仓必须保留一部分仓位；全部退出请使用止盈或止损确认")
        if action in {TradeAction.CONFIRM_TAKE_PROFIT, TradeAction.CONFIRM_STOP} and abs(quantity - remaining) > tolerance:
            raise ValueError(f"最终退出数量必须等于剩余仓位 {remaining:.12f} BTC")

    updated = dict(record)
    updated["state"] = target.value
    confirmed_at = now_ms()
    updated["updatedAt"] = confirmed_at
    if fill_key != "cancel":
        fills[fill_key] = {"price": float(request.price), "quantityBtc": quantity, "confirmedAt": confirmed_at}
    updated["actualFills"] = fills
    if action == TradeAction.CONFIRM_ADD:
        updated["addCount"] = 1
    if request.note:
        updated["lastNote"] = request.note
    updated.pop("activeReminder", None)
    return recompute_execution(updated), target.value


def price_trigger(record: dict[str, Any], price: float, data_fresh: bool) -> tuple[dict[str, Any], str | None, str | None]:
    """Create reminders only; market prices never assert that a real fill occurred."""
    if not data_fresh or not math.isfinite(price) or price <= 0:
        return record, None, None
    state = canonical_live_execution_state(record["state"])
    if state in TERMINAL_STATES or state == TradeState.IDLE:
        return record, None, None
    plan = TradePlanDraft.model_validate(record["plan"])
    risk = RiskCalculation.model_validate(record["risk"])
    assert plan.initial_entry_price and plan.stop_price and plan.take_profit_price and plan.add_price
    is_long = plan.direction == TradeDirection.LONG

    def reminded(event: str, message: str):
        previous = dict(record.get("activeReminder") or {})
        if previous.get("type") == event:
            return record, None, None
        updated = dict(record)
        created_at = now_ms()
        updated["activeReminder"] = {"type": event, "message": message, "price": price, "createdAt": created_at, "at": created_at}
        updated["updatedAt"] = now_ms()
        return updated, event, message

    if state == TradeState.PLANNED:
        distance = abs(price - plan.initial_entry_price) / plan.initial_entry_price * 100
        touched = price <= plan.initial_entry_price if is_long else price >= plan.initial_entry_price
        if touched or distance <= plan.approach_threshold_percent:
            return reminded("ENTRY_APPROACH", "价格已接近或触及计划开仓价，请人工确认实际开仓价格和 BTC 数量")
        return record, None, None

    stop_hit = price <= plan.stop_price if is_long else price >= plan.stop_price
    target_hit = price >= plan.take_profit_price if is_long else price <= plan.take_profit_price
    pending_type = dict(record.get("activeReminder") or {}).get("type")
    if pending_type == "STOP_HIT_PENDING_CONFIRMATION":
        return record, None, None
    if stop_hit:
        return reminded("STOP_HIT_PENDING_CONFIRMATION", "价格已触及硬止损位，请立即核对并人工确认实际止损成交")
    if pending_type == "TAKE_PROFIT_HIT_PENDING_CONFIRMATION":
        return record, None, None
    if target_hit:
        return reminded("TAKE_PROFIT_HIT_PENDING_CONFIRMATION", "价格已触及止盈位，请核对并人工确认实际止盈成交")
    if state in {TradeState.INITIAL_OPEN, TradeState.APPROACHING_ADD} and int(record.get("addCount", 0)) == 0:
        distance = abs(price - plan.add_price) / plan.add_price * 100
        touched = price <= plan.add_price if is_long else price >= plan.add_price
        if touched or distance <= plan.approach_threshold_percent:
            return reminded("APPROACHING_ADD", "价格已接近或触及加仓位；这里只提醒，不会自动加仓")
    if state in {TradeState.ADDED, TradeState.REDUCE_ZONE} and risk.reduce_zone_price:
        reached = price >= risk.reduce_zone_price if is_long else price <= risk.reduce_zone_price
        if reached:
            return reminded("REDUCE_ZONE", "价格已回到全成本减仓区；请人工确认实际减仓价格和 BTC 数量")
    return record, None, None


def candle_mark_to_market(record: dict[str, Any], candle: Candle) -> tuple[float, float]:
    plan = TradePlanDraft.model_validate(record["plan"])
    execution = recompute_execution(record)["execution"]
    quantity = float(execution.get("remainingQuantityBtc") or 0)
    cost = float(execution.get("remainingEntryCost") or 0)
    if not quantity:
        return 0.0, 0.0
    if plan.direction == TradeDirection.LONG:
        favorable = candle.high * quantity - cost
        adverse = cost - candle.low * quantity
    else:
        favorable = cost - candle.low * quantity
        adverse = candle.high * quantity - cost
    return max(0.0, favorable), max(0.0, adverse)


def replay_start_index(candles: list[Candle], mode: str, start_at: int | None, rng: random.Random | None = None) -> int:
    if len(candles) < 260:
        raise ValueError("历史 1H K 线不足，至少需要 260 根才能启动盲测")
    if mode == "manual":
        if start_at is None:
            raise ValueError("手动模式必须提供历史开始时间")
        candidates = [index for index, candle in enumerate(candles) if candle.timestamp <= start_at]
        if not candidates:
            raise ValueError("所选时间早于本地历史数据")
        index = candidates[-1]
    else:
        index = (rng or random.SystemRandom()).randint(200, len(candles) - 50)
    if index < 200 or index >= len(candles) - 1:
        raise ValueError("所选时间缺少足够的预热或后续 K 线")
    return index


def visible_replay_candles(candles_1h: list[Candle], candles_4h: list[Candle], cursor_index: int) -> tuple[list[Candle], list[Candle]]:
    one = candles_1h[max(0, cursor_index - 199): cursor_index + 1]
    decision_close = candles_1h[cursor_index].timestamp + 3_600_000
    four = [candle for candle in candles_4h if candle.timestamp + 4 * 3_600_000 <= decision_close]
    return one, four[-200:]


def build_add_check(
    plan: TradePlanDraft,
    risk: RiskCalculation,
    candles_1h: list[Candle],
    candles_4h: list[Candle],
    current_price: float | None,
    funding_rate: float | None,
    oi_context: dict[str, Any] | None,
    technical: dict[str, Any] | None,
    regime: MarketRegime,
    data_fresh: bool,
) -> dict[str, Any]:
    if not data_fresh or current_price is None or len(candles_1h) < 21 or not candles_4h:
        return {"status": "数据不足", "asOf": now_ms(), "warnings": ["行情过期、断线或 K 线数量不足，已停止生成价格触发检查"]}
    latest = candles_1h[-1]
    previous = candles_1h[-21:-1]
    volume_average = sum(c.volume for c in previous) / len(previous)
    volume_ratio = latest.volume / volume_average if volume_average > 0 else None
    is_long = plan.direction == TradeDirection.LONG
    body_closed_beyond = latest.close < float(plan.add_price) if is_long else latest.close > float(plan.add_price)
    body = max(abs(latest.close - latest.open), latest.close * 1e-6)
    wick = min(latest.open, latest.close) - latest.low if is_long else latest.high - max(latest.open, latest.close)
    rejection = wick / body >= 1.5 and (latest.close > latest.open if is_long else latest.close < latest.open)
    recent = candles_1h[-20:]
    recent_low, recent_high = min(c.low for c in recent), max(c.high for c in recent)
    vwap = (technical or {}).get("vwap")
    vwap_position = "数据不足" if not isinstance(vwap, (int, float)) else ("上方" if current_price > vwap else "下方")
    remaining = risk.add_to_stop_space_percent
    high_risk = bool(body_closed_beyond and (volume_ratio or 0) >= 1.5) or (remaining is not None and remaining < 0.35)
    support = rejection and not body_closed_beyond and (volume_ratio or 0) >= 0.8
    status = "突破风险较高" if high_risk else "支持加仓观察" if support else "等待人工确认"
    return {
        "status": status,
        "asOf": latest.timestamp + 3_600_000,
        "bodyClosedBeyond": body_closed_beyond,
        "volumeRatio20": volume_ratio,
        "rejectionCandle": rejection,
        "regime4H": regime.value,
        "vwap": vwap,
        "vwapPosition": vwap_position,
        "recentLow": recent_low,
        "recentHigh": recent_high,
        "fundingRate": funding_rate,
        "oiChangePercent": (oi_context or {}).get("changePercent"),
        "addToStopSpacePercent": remaining,
        "lossAtStopAfterAdd": risk.net_loss_at_stop,
        "warnings": list(risk.warnings),
    }


def build_trade_log(record: dict[str, Any], source: str = "live", regime: str | None = None) -> dict[str, Any]:
    plan = TradePlanDraft.model_validate(record["plan"])
    risk = RiskCalculation.model_validate(record["risk"])
    updated = recompute_execution(record)
    fills = normalized_actual_fills(updated)
    execution = updated["execution"]

    def price(key: str) -> float | None:
        fill = fills.get(key)
        return float(fill["price"]) if fill else None

    def quantity(key: str) -> float | None:
        fill = fills.get(key)
        return float(fill["quantityBtc"]) if fill else None

    initial_q = quantity("initial") or 0.0
    add_q = quantity("add") or 0.0
    return {
        "id": str(record.get("logId") or new_id()),
        "planId": record["id"],
        "source": source,
        "direction": plan.direction.value,
        "state": TradeState(record["state"]).value,
        "planPrices": {
            "initial": plan.initial_entry_price,
            "add": plan.add_price,
            "stop": plan.stop_price,
            "takeProfit": plan.take_profit_price,
            "reduce": risk.reduce_zone_price,
        },
        "actualPrices": {key: price(key) for key in ("initial", "add", "reduce", "exit")},
        "actualQuantitiesBtc": {key: quantity(key) for key in ("initial", "add", "reduce", "exit")},
        "initialQuantityBtc": initial_q,
        "addQuantityBtc": add_q,
        "totalQuantityBtc": initial_q + add_q,
        "initialMargin": risk.initial_margin,
        "addMargin": risk.add_margin if add_q > 0 else 0,
        "leverage": plan.leverage,
        "startingEquity": plan.equity,
        "makerFeeBps": plan.maker_fee_bps,
        "takerFeeBps": plan.taker_fee_bps,
        "slippageBps": plan.slippage_bps,
        "grossPnl": execution["realizedGrossPnl"],
        "fees": execution["fees"],
        "slippageUsdt": execution["slippageUsdt"],
        "netPnl": execution["realizedNetPnl"],
        "accountReturnPercent": execution["realizedNetPnl"] / plan.equity * 100,
        "execution": execution,
        "executionRisk": updated["executionRisk"],
        "realizedSegments": updated["realizedSegments"],
        "mfeUsdt": record.get("mfeUsdt") if execution["mfeMaeSupported"] else None,
        "maeUsdt": record.get("maeUsdt") if execution["mfeMaeSupported"] else None,
        "mfeMaeSupported": execution["mfeMaeSupported"],
        "regime4H": regime or record.get("regime4H") or "UNKNOWN",
        "addTriggered": add_q > 0,
        "returnedToReduceZone": "reduce" in fills,
        "notes": plan.notes,
        "screenshotPath": plan.screenshot_path,
        "closedAt": now_ms(),
    }


def trade_statistics(logs: list[dict[str, Any]]) -> dict[str, Any]:
    def finite_number(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    closed = [row for row in logs if finite_number(row.get("netPnl")) is not None]
    profits = [float(row["netPnl"]) for row in closed if float(row["netPnl"]) > 0]
    losses = [float(row["netPnl"]) for row in closed if float(row["netPnl"]) < 0]
    breakeven = [row for row in closed if float(row["netPnl"]) == 0]
    added = [row for row in closed if row.get("addTriggered")]
    direct_tp = [row for row in closed if not row.get("addTriggered") and row.get("state") == TradeState.TAKE_PROFIT.value]
    reduced = [row for row in added if row.get("returnedToReduceZone")]
    stopped_after_add = [row for row in added if row.get("state") == TradeState.STOPPED.value]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    streak = 0
    max_streak = 0
    chronological = sorted(closed, key=lambda item: (int(item.get("closedAt") or 0), str(item.get("id") or "")))
    # v0.4 logs did not persist startingEquity.  A later v0.5 baseline cannot
    # be moved backwards across older PnL without inventing historical account
    # state.  Only the chronologically earliest closed log may establish the
    # absolute-equity baseline; cumulative PnL remains available either way.
    starting_equity = finite_number(chronological[0].get("startingEquity")) if chronological else None
    equity_curve: list[dict[str, Any]] = []
    for row in chronological:
        pnl = float(row["netPnl"])
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        streak = streak + 1 if pnl < 0 else 0
        max_streak = max(max_streak, streak)
        equity_curve.append({
            "timestamp": int(row.get("closedAt") or 0),
            "cumulativeNetPnl": equity,
            "equity": starting_equity + equity if starting_equity is not None else None,
        })
    gross_profit = math.fsum(profits)
    gross_loss = abs(math.fsum(losses))
    total_fees = math.fsum(finite_number(row.get("fees")) or 0.0 for row in closed)
    total_slippage = math.fsum(finite_number(row.get("slippageUsdt")) or 0.0 for row in closed)

    def subset(rows: list[dict[str, Any]]) -> dict[str, Any]:
        values = [float(row["netPnl"]) for row in rows]
        return {
            "trades": len(rows),
            "netPnl": math.fsum(values),
            "winRate": sum(value > 0 for value in values) / len(values) if values else None,
            "averagePnl": sum(values) / len(values) if values else None,
        }

    return {
        "totalTrades": len(closed),
        "winningTrades": len(profits),
        "losingTrades": len(losses),
        "breakevenTrades": len(breakeven),
        "winRate": len(profits) / len(closed) if closed else None,
        "initialDirectTakeProfitRate": len(direct_tp) / len(closed) if closed else None,
        "addTriggerRate": len(added) / len(closed) if closed else None,
        "returnToReduceZoneRate": len(reduced) / len(added) if added else None,
        "hardStopAfterAddRate": len(stopped_after_add) / len(added) if added else None,
        "averageProfit": sum(profits) / len(profits) if profits else None,
        "averageLoss": sum(losses) / len(losses) if losses else None,
        "profitFactor": gross_profit / gross_loss if gross_loss > 0 else None,
        "totalFees": total_fees,
        "totalSlippage": total_slippage,
        "feesToGrossProfit": total_fees / gross_profit if gross_profit > 0 else None,
        "maxConsecutiveLosses": max_streak,
        "maxDrawdownUsdt": max_drawdown,
        "netPnl": math.fsum(float(row["netPnl"]) for row in closed),
        "startingEquity": starting_equity,
        "simulatedEquity": starting_equity + equity if starting_equity is not None else None,
        "equityCurve": equity_curve,
        "addTriggeredCount": len(added),
        "returnedToReduceZoneCount": len(reduced),
        "stoppedAfterAddCount": len(stopped_after_add),
        "byDirection": {
            "LONG": subset([row for row in closed if row.get("direction") == "LONG"]),
            "SHORT": subset([row for row in closed if row.get("direction") == "SHORT"]),
        },
        "byRegime": {
            label: subset([row for row in closed if row.get("regime4H") == label])
            for label in ("TREND", "RANGE", "TRANSITION", "STALE", "UNKNOWN")
        },
    }


def replay_candle_transition(record: dict[str, Any], candle: Candle) -> tuple[dict[str, Any], str | None]:
    """Apply one revealed candle. Stop wins every same-candle ambiguity."""
    plan = TradePlanDraft.model_validate(record["plan"])
    state = TradeState(record["state"])
    if state in TERMINAL_STATES:
        return record, None
    is_long = plan.direction == TradeDirection.LONG
    assert plan.stop_price and plan.take_profit_price and plan.add_price
    stop_hit = candle.low <= plan.stop_price if is_long else candle.high >= plan.stop_price
    target_hit = candle.high >= plan.take_profit_price if is_long else candle.low <= plan.take_profit_price
    updated = dict(record)
    favorable, adverse = candle_mark_to_market(record, candle)
    if recompute_execution(record)["execution"]["mfeMaeSupported"]:
        updated["mfeUsdt"] = max(float(record.get("mfeUsdt") or 0), favorable)
        updated["maeUsdt"] = max(float(record.get("maeUsdt") or 0), adverse)
    updated["updatedAt"] = now_ms()
    if stop_hit:
        updated["state"] = TradeState.STOPPED.value
        fills = normalized_actual_fills(record)
        quantity = recompute_execution(record)["execution"]["remainingQuantityBtc"]
        fills["exit"] = {"price": plan.stop_price, "quantityBtc": quantity}
        updated["actualFills"] = fills
        return recompute_execution(updated), "HARD_STOP"
    if target_hit:
        updated["state"] = TradeState.TAKE_PROFIT.value
        fills = normalized_actual_fills(record)
        quantity = recompute_execution(record)["execution"]["remainingQuantityBtc"]
        fills["exit"] = {"price": plan.take_profit_price, "quantityBtc": quantity}
        updated["actualFills"] = fills
        return recompute_execution(updated), "TAKE_PROFIT"
    if state in {TradeState.INITIAL_OPEN, TradeState.APPROACHING_ADD} and int(record.get("addCount", 0)) == 0:
        add_hit = candle.low <= plan.add_price if is_long else candle.high >= plan.add_price
        if add_hit:
            updated["state"] = TradeState.APPROACHING_ADD.value
            return updated, "APPROACHING_ADD"
    risk = RiskCalculation.model_validate(record["risk"])
    if state in {TradeState.ADDED, TradeState.REDUCE_ZONE} and risk.reduce_zone_price:
        reduce_hit = candle.high >= risk.reduce_zone_price if is_long else candle.low <= risk.reduce_zone_price
        if reduce_hit:
            updated["state"] = TradeState.REDUCE_ZONE.value
            updated["returnedToReduceZone"] = True
            return updated, "REDUCE_ZONE"
    return updated, None
