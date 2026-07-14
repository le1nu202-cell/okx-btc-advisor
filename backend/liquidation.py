from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Mapping


AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
NO_OPEN_POSITION = "NO_OPEN_POSITION"
REFERENCE_STALE = "STALE"
DEFAULT_MARK_PRICE_MAX_AGE_MS = 30_000


@dataclass(frozen=True)
class LiquidationInputs:
    """Inputs for a single-position cross-margin liquidation post processor.

    ``supporting_equity_usdt`` is B in the documented equation. Callers are
    responsible for incorporating extra margin, realised gross PnL, incurred
    fees and (when selected) unsettled funding before invoking this function.
    """

    direction: str
    quantity_btc: float
    average_entry_price: float
    supporting_equity_usdt: float
    maintenance_margin_rate: float | None
    maintenance_margin_fixed_usdt: float = 0
    liquidation_fee_rate: float = 0
    stop_price: float | None = None
    mark_price: float | None = None
    mark_price_time: int | None = None
    evaluated_at: int | None = None
    mark_price_max_age_ms: int = DEFAULT_MARK_PRICE_MAX_AGE_MS
    tick_size: float = 0.1
    tier: int | None = None
    contracts: float | None = None
    parameter_source: str = "MANUAL"
    parameters_updated_at: int | None = None
    scope: str = "TEST"


def _finite(value: Any, *, positive: bool = False, nonnegative: bool = False) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(number):
        return False
    if positive:
        return number > 0
    if nonnegative:
        return number >= 0
    return True


def _unavailable(inputs: LiquidationInputs, errors: list[str], *, no_position: bool = False) -> dict[str, Any]:
    reference_status = _reference_mark_status(inputs)
    return {
        "status": NO_OPEN_POSITION if no_position else UNAVAILABLE,
        "estimatedLiquidationPrice": None,
        "referenceMarkPrice": float(inputs.mark_price) if _finite(inputs.mark_price, positive=True) else None,
        "referenceMarkTime": int(inputs.mark_price_time) if _finite(inputs.mark_price_time, positive=True) else None,
        "referenceMarkStatus": reference_status,
        "distanceStatus": UNAVAILABLE,
        "distancePercent": None,
        "distanceRisk": "不可用",
        "hardStopSequence": "UNAVAILABLE",
        "hardStopBufferPercent": None,
        "quantityBtc": max(0.0, float(inputs.quantity_btc)) if _finite(inputs.quantity_btc) else None,
        "averageEntryPrice": float(inputs.average_entry_price) if _finite(inputs.average_entry_price, positive=True) else None,
        "supportingEquityUsdt": float(inputs.supporting_equity_usdt) if _finite(inputs.supporting_equity_usdt) else None,
        "maintenanceMarginRate": float(inputs.maintenance_margin_rate) if _finite(inputs.maintenance_margin_rate, nonnegative=True) else None,
        "maintenanceMarginFixedUsdt": float(inputs.maintenance_margin_fixed_usdt) if _finite(inputs.maintenance_margin_fixed_usdt, nonnegative=True) else None,
        "liquidationFeeRate": float(inputs.liquidation_fee_rate) if _finite(inputs.liquidation_fee_rate, nonnegative=True) else None,
        "tier": inputs.tier,
        "contracts": float(inputs.contracts) if _finite(inputs.contracts, nonnegative=True) else None,
        "parameterSource": inputs.parameter_source if inputs.parameter_source in {"OKX_PUBLIC", "MANUAL"} else "UNAVAILABLE",
        "parametersUpdatedAt": int(inputs.parameters_updated_at) if _finite(inputs.parameters_updated_at, positive=True) else None,
        "scope": inputs.scope,
        "assumptions": [],
        "warnings": [],
        "errors": errors,
    }


def _reference_mark_status(inputs: LiquidationInputs) -> str:
    """Classify whether mark-relative output is safe to publish.

    The liquidation price formula itself does not use the mark price.  Its
    relative distance does, so callers can retain a valid estimate while
    explicitly withholding stale mark-relative fields.
    """
    if not _finite(inputs.mark_price, positive=True) or not _finite(inputs.mark_price_time, positive=True):
        return "MISSING"
    if inputs.evaluated_at is None:
        # Preserve deterministic low-level callers which do not provide a
        # clock. Production entry points always pass evaluated_at.
        return AVAILABLE
    if not _finite(inputs.evaluated_at, positive=True) or not _finite(inputs.mark_price_max_age_ms, positive=True):
        return REFERENCE_STALE
    age = int(inputs.evaluated_at) - int(inputs.mark_price_time)
    if age < -60_000 or age > int(inputs.mark_price_max_age_ms):
        return REFERENCE_STALE
    return AVAILABLE


def _conservative_tick(price: float, tick_size: float, direction: str) -> float:
    """Round toward an earlier adverse liquidation trigger."""
    price_decimal = Decimal(str(price))
    tick_decimal = Decimal(str(tick_size))
    rounding = ROUND_CEILING if direction == "LONG" else ROUND_FLOOR
    ticks = (price_decimal / tick_decimal).to_integral_value(rounding=rounding)
    return float(ticks * tick_decimal)


def estimate_liquidation(inputs: LiquidationInputs) -> dict[str, Any]:
    """Estimate a cross-margin liquidation price without changing risk sizing.

    For direction sign s (+1 long, -1 short), quantity Q, average A,
    supporting equity B, maintenance rate m, liquidation fee rate f and fixed
    maintenance requirement K:

        Pliq = (s*Q*A + K - B) / (Q*(s - m - f))
    """

    direction = str(inputs.direction).upper()
    if direction not in {"LONG", "SHORT"}:
        return _unavailable(inputs, ["direction 必须是 LONG 或 SHORT"])
    if not _finite(inputs.quantity_btc) or float(inputs.quantity_btc) <= 0:
        return _unavailable(inputs, [], no_position=True)

    errors: list[str] = []
    if not _finite(inputs.average_entry_price, positive=True):
        errors.append("averageEntryPrice 必须是有限正数")
    if not _finite(inputs.supporting_equity_usdt):
        errors.append("supportingEquityUsdt 必须是有限数值")
    if inputs.maintenance_margin_rate is None or not _finite(inputs.maintenance_margin_rate, nonnegative=True):
        errors.append("缺少有效维持保证金率")
    elif float(inputs.maintenance_margin_rate) >= 1:
        errors.append("维持保证金率必须小于 1")
    if not _finite(inputs.maintenance_margin_fixed_usdt, nonnegative=True):
        errors.append("固定维持保证金要求必须为非负有限数")
    if not _finite(inputs.liquidation_fee_rate, nonnegative=True) or float(inputs.liquidation_fee_rate) >= 1:
        errors.append("强平费率必须位于 [0,1) 区间")
    if not _finite(inputs.tick_size, positive=True):
        errors.append("tickSize 必须是有限正数")
    if errors:
        return _unavailable(inputs, errors)

    quantity = float(inputs.quantity_btc)
    average = float(inputs.average_entry_price)
    supporting = float(inputs.supporting_equity_usdt)
    mmr = float(inputs.maintenance_margin_rate)
    fixed = float(inputs.maintenance_margin_fixed_usdt)
    liquidation_fee = float(inputs.liquidation_fee_rate)
    sign = 1.0 if direction == "LONG" else -1.0
    denominator = quantity * (sign - mmr - liquidation_fee)
    if not math.isfinite(denominator) or abs(denominator) <= 1e-15:
        return _unavailable(inputs, ["强平公式分母为零或过小"])
    raw_price = (sign * quantity * average + fixed - supporting) / denominator
    if not math.isfinite(raw_price) or raw_price <= 0:
        return _unavailable(inputs, ["当前参数未产生有效正强平价格"])

    estimated = _conservative_tick(raw_price, float(inputs.tick_size), direction)
    reference_status = _reference_mark_status(inputs)
    mark = float(inputs.mark_price) if _finite(inputs.mark_price, positive=True) else None
    directional_distance = None
    if reference_status == AVAILABLE and mark is not None:
        directional_distance = (mark - estimated) / mark * 100 if direction == "LONG" else (estimated - mark) / mark * 100
    stop_sequence = "UNAVAILABLE"
    hard_stop_buffer = None
    if _finite(inputs.stop_price, positive=True):
        stop = float(inputs.stop_price)
        hard_stop_buffer = abs(stop - estimated) / stop * 100
        overlap_tolerance = max(float(inputs.tick_size), stop * 0.0005)
        if abs(stop - estimated) <= overlap_tolerance:
            stop_sequence = "OVERLAP_UNSAFE"
        elif (direction == "LONG" and stop > estimated) or (direction == "SHORT" and stop < estimated):
            stop_sequence = "STOP_FIRST"
        else:
            stop_sequence = "LIQUIDATION_FIRST"

    if reference_status != AVAILABLE or directional_distance is None:
        distance_risk = "不可用"
    elif stop_sequence in {"LIQUIDATION_FIRST", "OVERLAP_UNSAFE"}:
        distance_risk = "可能早于止损强平"
    elif directional_distance <= 1:
        distance_risk = "危险"
    elif directional_distance <= 3:
        distance_risk = "偏近"
    else:
        distance_risk = "充足"

    warnings: list[str] = []
    if directional_distance is not None and directional_distance <= 0:
        warnings.append("公开标记价格已经越过估算强平阈值，估算仅可作为异常提示")
    if reference_status == REFERENCE_STALE:
        warnings.append("公开标记价格已超过 30 秒未更新；当前价距离与强平距离风险不可用")
    elif reference_status == "MISSING":
        warnings.append("缺少带时间戳的公开标记价格；当前价距离与强平距离风险不可用")
    if stop_sequence in {"LIQUIDATION_FIRST", "OVERLAP_UNSAFE"}:
        warnings.append("估算强平可能早于或紧邻计划硬止损；止损单并不保证先成交")
    if inputs.parameter_source == "OKX_PUBLIC" and inputs.parameters_updated_at is None:
        warnings.append("公开维持保证金参数缺少更新时间")

    return {
        "status": AVAILABLE,
        "estimatedLiquidationPrice": estimated,
        "referenceMarkPrice": mark,
        "referenceMarkTime": int(inputs.mark_price_time) if _finite(inputs.mark_price_time, positive=True) else None,
        "referenceMarkStatus": reference_status,
        "distanceStatus": AVAILABLE if reference_status == AVAILABLE else UNAVAILABLE,
        "distancePercent": directional_distance,
        "distanceRisk": distance_risk,
        "hardStopSequence": stop_sequence,
        "hardStopBufferPercent": hard_stop_buffer,
        "quantityBtc": quantity,
        "averageEntryPrice": average,
        "supportingEquityUsdt": supporting,
        "maintenanceMarginRate": mmr,
        "maintenanceMarginFixedUsdt": fixed,
        "liquidationFeeRate": liquidation_fee,
        "tier": inputs.tier,
        "contracts": float(inputs.contracts) if _finite(inputs.contracts, nonnegative=True) else None,
        "parameterSource": inputs.parameter_source if inputs.parameter_source in {"OKX_PUBLIC", "MANUAL"} else "UNAVAILABLE",
        "parametersUpdatedAt": int(inputs.parameters_updated_at) if _finite(inputs.parameters_updated_at, positive=True) else None,
        "scope": inputs.scope,
        "assumptions": [
            "线性永续合约按 B+sQ(P-A)=QP(mmr+liquidationFee)+K 求解",
            "价格按合约 tickSize 向更早触发强平的方向保守取整",
            "估算不代表 OKX 最终强平引擎或强平订单的实际成交价",
        ],
        "warnings": warnings,
        "errors": [],
    }


def _number(mapping: Mapping[str, Any], *names: str, default: float | None = None) -> float | None:
    for name in names:
        if name in mapping and mapping[name] is not None:
            try:
                value = float(mapping[name])
            except (TypeError, ValueError, OverflowError):
                return default
            return value if math.isfinite(value) else default
    return default


def select_public_tier(quantity_btc: float, context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Convert BTC quantity to contracts and select a validated public tier."""
    if not context or not _finite(quantity_btc, positive=True):
        return None
    contract_value = _number(context, "contractValueBtc")
    lot_size = _number(context, "lotSizeContracts", default=1.0)
    tick_size = _number(context, "tickSize", default=0.1)
    if not contract_value or contract_value <= 0 or not lot_size or lot_size <= 0:
        return None
    raw_contracts = Decimal(str(abs(float(quantity_btc)))) / Decimal(str(contract_value))
    lot = Decimal(str(lot_size))
    contracts = float((raw_contracts / lot).to_integral_value(rounding=ROUND_CEILING) * lot)
    for row in context.get("tiers", []):
        if not isinstance(row, Mapping):
            continue
        minimum = _number(row, "minContracts")
        maximum = _number(row, "maxContracts")
        mmr = _number(row, "maintenanceMarginRate")
        fixed = _number(row, "maintenanceMarginFixedUsdt", default=0.0)
        if minimum is None or maximum is None or mmr is None or fixed is None or fixed < 0:
            continue
        if minimum <= contracts <= maximum:
            return {
                "tier": int(row.get("tier")),
                "contracts": contracts,
                "maintenanceMarginRate": mmr,
                "maintenanceMarginFixedUsdt": fixed,
                "maxLeverage": _number(row, "maxLeverage"),
                "tickSize": tick_size or 0.1,
                "parametersUpdatedAt": int(context.get("updatedAt")) if context.get("updatedAt") else None,
                "stale": bool(context.get("stale")),
            }
    return None


def liquidation_parameters(plan: Mapping[str, Any], quantity_btc: float, context: Mapping[str, Any] | None) -> dict[str, Any]:
    source = str(plan.get("maintenanceMarginSource", "AUTO")).upper()
    if source == "MANUAL":
        rate = _number(plan, "maintenanceMarginRate")
        fixed = _number(plan, "maintenanceMarginFixedUsdt", "maintenance_margin_fixed_usdt", default=0.0)
        return {
            "maintenanceMarginRate": rate,
            "maintenanceMarginFixedUsdt": fixed,
            "tier": None,
            "contracts": None,
            "tickSize": _number(context or {}, "tickSize", default=0.1) or 0.1,
            "parameterSource": "MANUAL" if rate is not None else "UNAVAILABLE",
            "parametersUpdatedAt": None,
            "warnings": [],
        }
    selected = select_public_tier(quantity_btc, context)
    if not selected:
        return {
            "maintenanceMarginRate": None,
            "maintenanceMarginFixedUsdt": None,
            "tier": None,
            "contracts": None,
            "tickSize": 0.1,
            "parameterSource": "UNAVAILABLE",
            "parametersUpdatedAt": None,
            "warnings": ["公开维持保证金档位不可用；AUTO 模式不输出强平价格"],
        }
    warnings = ["正在使用超过 6 小时但仍在允许回退窗口内的公开参数缓存"] if selected.get("stale") else []
    leverage = _number(plan, "leverage")
    max_leverage = _number(selected, "maxLeverage")
    if leverage is not None and max_leverage is not None and leverage > max_leverage:
        warnings.append(f"计划杠杆 {leverage:g}x 高于公开档位最大杠杆 {max_leverage:g}x")
    return {
        **selected,
        "parameterSource": "OKX_PUBLIC",
        "warnings": warnings,
    }


def _plan_value(plan: Mapping[str, Any], camel: str, snake: str | None = None, default: Any = None) -> Any:
    if camel in plan:
        return plan[camel]
    if snake and snake in plan:
        return plan[snake]
    return default


def _mode_value(plan: Mapping[str, Any], camel: str, snake: str, default: str | None = None) -> str | None:
    raw = _plan_value(plan, camel, snake, default)
    if raw is None:
        return None
    return str(getattr(raw, "value", raw)).upper()


def _different_numbers(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    return not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def _resolved_cross_equity(plan: Mapping[str, Any]) -> tuple[str, float | None]:
    equity = _number(plan, "equity", default=80.0)
    manual = _number(plan, "crossAvailableEquity", "cross_available_equity")
    mode = _mode_value(plan, "crossEquityMode", "cross_equity_mode")
    if mode is None:
        # v0.5 compatibility: a distinct persisted value was intentionally
        # manual; equal/missing values migrate to the new FOLLOW default.
        mode = "MANUAL" if manual is not None and _different_numbers(manual, equity) else "FOLLOW_EQUITY"
    if mode == "MANUAL":
        return mode, manual
    return "FOLLOW_EQUITY", equity


def _resolved_liquidation_fee(plan: Mapping[str, Any]) -> tuple[str, float | None]:
    taker = _number(plan, "takerFeeBps", "taker_fee_bps", default=5.0)
    manual = _number(plan, "liquidationFeeBps", "liquidation_fee_bps")
    mode = _mode_value(plan, "liquidationFeeMode", "liquidation_fee_mode")
    if mode is None:
        mode = "MANUAL" if manual is not None and _different_numbers(manual, taker) else "FOLLOW_TAKER"
    if mode == "MANUAL":
        return mode, manual
    return "FOLLOW_TAKER", taker


def _supporting_equity(
    plan: Mapping[str, Any],
    *,
    realised_gross_pnl: float = 0.0,
    incurred_fees_usdt: float = 0.0,
    available_equity_override: float | None = None,
) -> float:
    _, resolved_available = _resolved_cross_equity(plan)
    available = available_equity_override if available_equity_override is not None else resolved_available
    extra = _plan_value(plan, "extraMarginUsdt", "extra_margin_usdt", 0.0) or 0.0
    funding = 0.0
    include_funding = bool(_plan_value(plan, "includeUnsettledFunding", "include_unsettled_funding", False))
    if include_funding:
        funding = _plan_value(plan, "unsettledFundingUsdt", "unsettled_funding_usdt", 0.0) or 0.0
    try:
        return float(available) + float(extra) + float(realised_gross_pnl) - float(incurred_fees_usdt) - float(funding)
    except (TypeError, ValueError, OverflowError):
        return math.nan


def _estimate_for_position(
    plan: Mapping[str, Any],
    *,
    quantity_btc: float,
    average_entry_price: float | None,
    realised_gross_pnl: float,
    incurred_fees_usdt: float,
    mark_price: float | None,
    mark_price_time: int | None,
    evaluated_at: int | None,
    public_context: Mapping[str, Any] | None,
    scope: str,
    equity_basis_override: float | None = None,
    equity_basis_frozen: bool = False,
) -> dict[str, Any]:
    parameters = liquidation_parameters(plan, quantity_btc, public_context)
    equity_mode, configured_equity_basis = _resolved_cross_equity(plan)
    equity_basis = equity_basis_override if equity_basis_override is not None else configured_equity_basis
    liquidation_fee_mode, liquidation_fee_bps = _resolved_liquidation_fee(plan)
    inputs = LiquidationInputs(
        direction=str(_plan_value(plan, "direction", default="")),
        quantity_btc=float(quantity_btc or 0),
        average_entry_price=float(average_entry_price or 0),
        supporting_equity_usdt=_supporting_equity(
            plan,
            realised_gross_pnl=realised_gross_pnl,
            incurred_fees_usdt=incurred_fees_usdt,
            available_equity_override=equity_basis_override,
        ),
        maintenance_margin_rate=parameters.get("maintenanceMarginRate"),
        # AUTO deliberately ignores any manual fields left in a persisted form
        # after switching modes; only the selected public tier is authoritative.
        maintenance_margin_fixed_usdt=float(parameters.get("maintenanceMarginFixedUsdt") or 0.0),
        liquidation_fee_rate=float(liquidation_fee_bps) / 10_000 if liquidation_fee_bps is not None else math.nan,
        stop_price=_number(plan, "stopPrice", "stop_price"),
        mark_price=mark_price,
        mark_price_time=mark_price_time,
        evaluated_at=evaluated_at,
        tick_size=float(parameters.get("tickSize") or 0.1),
        tier=parameters.get("tier"),
        contracts=parameters.get("contracts"),
        parameter_source=str(parameters.get("parameterSource") or "UNAVAILABLE"),
        parameters_updated_at=parameters.get("parametersUpdatedAt"),
        scope=scope,
    )
    margin_mode = str(_plan_value(plan, "marginMode", "margin_mode", "CROSS")).upper()
    assume_no_other_positions = bool(_plan_value(plan, "assumeNoOtherPositions", "assume_no_other_positions", True))
    if margin_mode != "CROSS":
        result = _unavailable(inputs, ["本估算仅支持全仓 CROSS；逐仓保证金余额模型不同"])
    elif not assume_no_other_positions:
        result = _unavailable(inputs, ["全仓估算要求 assumeNoOtherPositions=true；共享仓位权益未知"])
    else:
        result = estimate_liquidation(inputs)
    result["crossEquityMode"] = equity_mode
    result["crossEquityBasisUsdt"] = float(equity_basis) if _finite(equity_basis, nonnegative=True) else None
    result["crossEquityBasisFrozen"] = bool(equity_basis_frozen)
    result["liquidationFeeMode"] = liquidation_fee_mode
    result["assumptions"] = [
        *list(result.get("assumptions") or []),
        (
            "实际开仓时的全仓支持余额基准已冻结，不随之后的计划草稿变化"
            if equity_basis_frozen
            else "开仓前的全仓支持余额基准跟随账户权益"
            if equity_mode == "FOLLOW_EQUITY"
            else "开仓前的全仓支持余额基准使用手动输入值"
        ),
        "全仓支持余额基准是支撑当前单一全仓仓位的账户余额基准，不是扣除仓位或挂单占用后的可用保证金；当前仓位未实现盈亏单独计算",
        "假设只存在 BTC-USDT-SWAP 这一项全仓仓位，没有其他全仓或逐仓仓位影响账户权益，也没有待成交挂单占用保证金",
        "假设没有未知账户级费用或资产折算；实际强平以 OKX 标记价格和账户页面为准",
        "预计强平手续费率跟随 Taker 费率" if liquidation_fee_mode == "FOLLOW_TAKER" else "预计强平手续费率使用手动输入值",
    ]
    result["warnings"] = list(parameters.get("warnings") or []) + list(result.get("warnings") or [])
    if not assume_no_other_positions:
        result["warnings"].append("账户其他仓位会共享权益，因此不输出可能误导的强平价格")
    return result


def planned_liquidation_scenarios(
    plan: Mapping[str, Any],
    risk: Mapping[str, Any],
    *,
    mark_price: float | None,
    mark_price_time: int | None,
    public_context: Mapping[str, Any] | None,
    evaluated_at: int | None = None,
) -> dict[str, Any]:
    q0 = _number(risk, "initialQuantityBtc", "initial_quantity_btc", default=0.0) or 0.0
    qa = _number(risk, "addQuantityBtc", "add_quantity_btc", default=0.0) or 0.0
    total = _number(risk, "totalQuantityBtc", "total_quantity_btc", default=q0 + qa) or 0.0
    remaining = _number(risk, "remainingQuantityAfterPlannedReduce", "remaining_quantity_after_planned_reduce", default=q0) or 0.0
    initial_average = _number(risk, "initialFillPrice", "initial_fill_price")
    combined_average = _number(risk, "averageEntryPrice", "average_entry_price")
    opening_fee = _number(risk, "openingFee", "opening_fee", default=0.0) or 0.0
    add_fee = _number(risk, "addFee", "add_fee", default=0.0) or 0.0
    reduce_fee = _number(risk, "estimatedReduceFee", "estimated_reduce_fee", default=0.0) or 0.0
    reduce_price = _number(risk, "reduceZonePrice", "reduce_zone_price")
    direction = str(_plan_value(plan, "direction", default="")).upper()
    sign = 1.0 if direction == "LONG" else -1.0
    reduce_quantity = max(0.0, total - remaining)
    realised_after_reduce = 0.0
    if combined_average and reduce_price:
        realised_after_reduce = sign * reduce_quantity * (reduce_price - combined_average)

    scenarios = {
        "initialOnly": _estimate_for_position(
            plan,
            quantity_btc=q0,
            average_entry_price=initial_average,
            realised_gross_pnl=0.0,
            incurred_fees_usdt=opening_fee,
            mark_price=mark_price,
            mark_price_time=mark_price_time,
            evaluated_at=evaluated_at,
            public_context=public_context,
            scope="INITIAL_ONLY",
        ),
        "afterAdd": _estimate_for_position(
            plan,
            quantity_btc=total,
            average_entry_price=combined_average,
            realised_gross_pnl=0.0,
            incurred_fees_usdt=opening_fee + add_fee,
            mark_price=mark_price,
            mark_price_time=mark_price_time,
            evaluated_at=evaluated_at,
            public_context=public_context,
            scope="AFTER_ADD",
        ),
        "afterPlannedReduce": _estimate_for_position(
            plan,
            quantity_btc=remaining,
            average_entry_price=combined_average,
            realised_gross_pnl=realised_after_reduce,
            incurred_fees_usdt=opening_fee + add_fee + reduce_fee,
            mark_price=mark_price,
            mark_price_time=mark_price_time,
            evaluated_at=evaluated_at,
            public_context=public_context,
            scope="AFTER_PLANNED_REDUCE",
        ),
    }
    previous_price = scenarios["initialOnly"].get("estimatedLiquidationPrice")
    after_add_price = scenarios["afterAdd"].get("estimatedLiquidationPrice")
    after_reduce_price = scenarios["afterPlannedReduce"].get("estimatedLiquidationPrice")
    scenarios["initialOnly"]["changeFromPreviousUsdt"] = None
    scenarios["afterAdd"]["changeFromPreviousUsdt"] = after_add_price - previous_price if after_add_price is not None and previous_price is not None else None
    scenarios["afterPlannedReduce"]["changeFromPreviousUsdt"] = after_reduce_price - after_add_price if after_reduce_price is not None and after_add_price is not None else None
    return scenarios


def actual_liquidation_estimate(
    record: Mapping[str, Any],
    *,
    mark_price: float | None,
    mark_price_time: int | None,
    public_context: Mapping[str, Any] | None,
    evaluated_at: int | None = None,
) -> dict[str, Any]:
    plan = record.get("plan") if isinstance(record.get("plan"), Mapping) else {}
    execution = record.get("execution") if isinstance(record.get("execution"), Mapping) else {}
    quantity = _number(execution, "remainingQuantityBtc", default=0.0) or 0.0
    average = _number(execution, "averageEntryPrice")
    realised_gross = _number(execution, "realizedGrossPnl", default=0.0) or 0.0
    fills = record.get("actualFills") if isinstance(record.get("actualFills"), Mapping) else {}
    has_initial_fill = "initial" in fills
    frozen_equity_basis = _number(record, "liquidationEquityBasisUsdt", "liquidation_equity_basis_usdt")
    if has_initial_fill and frozen_equity_basis is None:
        # Lazy v0.5 compatibility for records not yet re-saved by
        # recompute_execution: infer old independent values before estimating.
        _, frozen_equity_basis = _resolved_cross_equity(plan)
    maker = (_number(plan, "makerFeeBps", "maker_fee_bps", default=0.0) or 0.0) / 10_000
    taker = (_number(plan, "takerFeeBps", "taker_fee_bps", default=0.0) or 0.0) / 10_000
    incurred_fees = 0.0
    for key in ("initial", "add", "reduce", "exit"):
        fill = fills.get(key) if isinstance(fills, Mapping) else None
        if not isinstance(fill, Mapping):
            continue
        price = _number(fill, "price")
        fill_quantity = _number(fill, "quantityBtc", "quantity_btc")
        if price and fill_quantity:
            incurred_fees += price * fill_quantity * (maker if key == "initial" else taker)
    return _estimate_for_position(
        plan,
        quantity_btc=quantity,
        average_entry_price=average,
        realised_gross_pnl=realised_gross,
        incurred_fees_usdt=incurred_fees,
        mark_price=mark_price,
        mark_price_time=mark_price_time,
        evaluated_at=evaluated_at,
        public_context=public_context,
        scope="ACTUAL_REMAINING",
        equity_basis_override=frozen_equity_basis if has_initial_fill else None,
        equity_basis_frozen=has_initial_fill,
    )
