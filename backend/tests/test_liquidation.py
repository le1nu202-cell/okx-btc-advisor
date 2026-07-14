import math

import pytest

from backend.liquidation import (
    LiquidationInputs,
    actual_liquidation_estimate,
    estimate_liquidation,
    planned_liquidation_scenarios,
)
from backend.workbench import TradePlanDraft, _risk_level


def _expected_price(
    direction: str,
    *,
    quantity: float,
    average: float,
    equity: float,
    mmr: float,
    fixed: float = 0.0,
    fee: float = 0.0,
) -> float:
    if direction == "LONG":
        return (quantity * average + fixed - equity) / (quantity * (1 - mmr - fee))
    return (equity + quantity * average - fixed) / (quantity * (1 + mmr + fee))


def _estimate(direction: str, **changes):
    values = {
        "direction": direction,
        "quantity_btc": 2.0,
        "average_entry_price": 100.0,
        "supporting_equity_usdt": 40.0,
        "maintenance_margin_rate": 0.05,
        "maintenance_margin_fixed_usdt": 1.0,
        "liquidation_fee_rate": 0.01,
        "mark_price": 100.0,
        "mark_price_time": 1_800_000_000_000,
        "tick_size": 1e-9,
        "tier": 2,
        "contracts": 200.0,
        "parameter_source": "OKX_PUBLIC",
        "parameters_updated_at": 1_799_999_999_000,
        "scope": "TEST",
    }
    values.update(changes)
    return estimate_liquidation(LiquidationInputs(**values))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_independent_long_and_short_formula(direction):
    result = _estimate(direction)
    expected = _expected_price(
        direction,
        quantity=2,
        average=100,
        equity=40,
        mmr=0.05,
        fixed=1,
        fee=0.01,
    )

    assert result["status"] == "AVAILABLE"
    assert result["estimatedLiquidationPrice"] == pytest.approx(expected, abs=2e-9)
    assert result["quantityBtc"] == 2
    assert result["averageEntryPrice"] == 100
    assert result["supportingEquityUsdt"] == 40
    assert result["maintenanceMarginRate"] == 0.05
    assert result["maintenanceMarginFixedUsdt"] == 1
    assert result["liquidationFeeRate"] == 0.01
    assert result["referenceMarkPrice"] == 100
    assert result["referenceMarkTime"] == 1_800_000_000_000
    assert result["parameterSource"] == "OKX_PUBLIC"
    assert result["parametersUpdatedAt"] == 1_799_999_999_000
    assert result["scope"] == "TEST"
    assert result["tier"] == 2 and result["contracts"] == 200


@pytest.mark.parametrize(
    ("scope", "quantity", "average", "equity"),
    [
        ("INITIAL_ONLY", 0.4, 100.0, 4.0),
        ("AFTER_ADD", 1.2, 280 / 3, 12.0),
        ("AFTER_PLANNED_REDUCE", 0.4, 90.0, 4.5),
        ("ACTUAL_EXECUTION", 0.371234567, 101.2345, 3.75),
    ],
)
def test_initial_add_reduce_and_actual_quantity_are_not_reconstructed(scope, quantity, average, equity):
    result = _estimate(
        "LONG",
        scope=scope,
        quantity_btc=quantity,
        average_entry_price=average,
        supporting_equity_usdt=equity,
        maintenance_margin_rate=0.005,
        maintenance_margin_fixed_usdt=0.25,
        liquidation_fee_rate=0.001,
        contracts=quantity / 0.01,
    )
    expected = _expected_price(
        "LONG",
        quantity=quantity,
        average=average,
        equity=equity,
        mmr=0.005,
        fixed=0.25,
        fee=0.001,
    )

    assert result["scope"] == scope
    assert result["quantityBtc"] == pytest.approx(quantity)
    assert result["averageEntryPrice"] == pytest.approx(average)
    assert result["supportingEquityUsdt"] == pytest.approx(equity)
    assert result["estimatedLiquidationPrice"] == pytest.approx(expected, abs=2e-9)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_equity_mmr_fixed_amount_and_liquidation_fee_each_affect_formula(direction):
    base = _estimate(
        direction,
        supporting_equity_usdt=20,
        maintenance_margin_rate=0.01,
        maintenance_margin_fixed_usdt=0,
        liquidation_fee_rate=0,
    )["estimatedLiquidationPrice"]
    more_equity = _estimate(
        direction,
        supporting_equity_usdt=30,
        maintenance_margin_rate=0.01,
        maintenance_margin_fixed_usdt=0,
        liquidation_fee_rate=0,
    )["estimatedLiquidationPrice"]
    more_mmr = _estimate(
        direction,
        supporting_equity_usdt=20,
        maintenance_margin_rate=0.02,
        maintenance_margin_fixed_usdt=0,
        liquidation_fee_rate=0,
    )["estimatedLiquidationPrice"]
    fixed = _estimate(
        direction,
        supporting_equity_usdt=20,
        maintenance_margin_rate=0.01,
        maintenance_margin_fixed_usdt=2,
        liquidation_fee_rate=0,
    )["estimatedLiquidationPrice"]
    fee = _estimate(
        direction,
        supporting_equity_usdt=20,
        maintenance_margin_rate=0.01,
        maintenance_margin_fixed_usdt=0,
        liquidation_fee_rate=0.01,
    )["estimatedLiquidationPrice"]

    if direction == "LONG":
        assert more_equity < base < more_mmr
        assert fixed > base and fee > base
    else:
        assert more_equity > base > more_mmr
        assert fixed < base and fee < base


@pytest.mark.parametrize(
    ("direction", "stop", "sequence"),
    [
        ("LONG", 90.0, "STOP_FIRST"),
        ("LONG", 80.0, "LIQUIDATION_FIRST"),
        ("SHORT", 110.0, "STOP_FIRST"),
        ("SHORT", 120.0, "LIQUIDATION_FIRST"),
    ],
)
def test_hard_stop_sequence_is_directional(direction, stop, sequence):
    result = _estimate(direction, stop_price=stop)
    assert result["hardStopSequence"] == sequence
    assert result["hardStopBufferPercent"] is not None
    assert result["distanceRisk"] in {"充足", "偏近", "危险", "可能早于止损强平"}


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_equal_stop_and_liquidation_price_is_overlap_unsafe(direction):
    price = _expected_price(
        direction,
        quantity=2,
        average=100,
        equity=40,
        mmr=0.05,
        fixed=1,
        fee=0.01,
    )
    result = _estimate(direction, stop_price=price)
    assert result["hardStopSequence"] == "OVERLAP_UNSAFE"


@pytest.mark.parametrize(
    "changes",
    [
        {"maintenance_margin_rate": None},
        {"maintenance_margin_rate": -0.01},
        {"maintenance_margin_rate": 1.0},
        {"average_entry_price": float("nan")},
        {"tick_size": 0},
    ],
)
def test_missing_or_invalid_parameters_return_unavailable_without_nonfinite_json(changes):
    result = _estimate("LONG", **changes)
    assert result["status"] == "UNAVAILABLE"
    assert result["estimatedLiquidationPrice"] is None
    assert result["distanceRisk"] == "不可用"
    assert result["hardStopSequence"] == "UNAVAILABLE"
    assert result["errors"]

    def assert_finite(value):
        if isinstance(value, float):
            assert math.isfinite(value)
        elif isinstance(value, dict):
            for item in value.values():
                assert_finite(item)
        elif isinstance(value, list):
            for item in value:
                assert_finite(item)

    assert_finite(result)


def test_zero_quantity_is_no_open_position_and_extreme_valid_input_stays_finite():
    empty = _estimate("LONG", quantity_btc=0)
    assert empty["status"] == "NO_OPEN_POSITION"
    assert empty["estimatedLiquidationPrice"] is None
    assert empty["quantityBtc"] == 0

    extreme = _estimate(
        "SHORT",
        quantity_btc=1_000_000,
        average_entry_price=1_000_000_000,
        supporting_equity_usdt=1e-8,
        maintenance_margin_rate=0.999,
        liquidation_fee_rate=0,
        tick_size=1e-8,
        mark_price=1_000_000_000,
    )
    assert extreme["status"] == "AVAILABLE"
    assert math.isfinite(extreme["estimatedLiquidationPrice"])
    assert math.isfinite(extreme["distancePercent"])


def test_trade_risk_3_5_8_boundaries_are_inclusive_and_not_liquidation_distance_labels():
    plan = TradePlanDraft(
        direction="LONG",
        initialEntryPrice=100,
        addPrice=90,
        stopPrice=80,
        takeProfitPrice=120,
        equity=80,
        leverage=10,
        initialMargin=4,
        riskLowMaxPercent=3,
        riskMediumMaxPercent=5,
        riskHighMaxPercent=8,
    )
    assert _risk_level(3, plan) == "低"
    assert _risk_level(3.0000001, plan) == "中"
    assert _risk_level(5, plan) == "中"
    assert _risk_level(5.0000001, plan) == "高"
    assert _risk_level(8, plan) == "高"
    assert _risk_level(8.0000001, plan) == "极高"
    assert {"低", "中", "高", "极高"}.isdisjoint({"充足", "偏近", "危险", "可能早于止损强平", "不可用"})


def _public_context(*, max_leverage: float = 100) -> dict:
    return {
        "contractValueBtc": 0.01,
        "lotSizeContracts": 1,
        "tickSize": 0.1,
        "updatedAt": 1_799_999_999_000,
        "stale": False,
        "tiers": [{
            "tier": 1,
            "minContracts": 0,
            "maxContracts": 10_000,
            "maintenanceMarginRate": 0.005,
            "maxLeverage": max_leverage,
        }],
    }


def _scenario_plan(**changes) -> dict:
    result = {
        "direction": "LONG",
        "marginMode": "CROSS",
        "equity": 80,
        "crossAvailableEquity": 4,
        "leverage": 10,
        "stopPrice": 80,
        "maintenanceMarginSource": "AUTO",
        "liquidationFeeBps": 5,
        "assumeNoOtherPositions": True,
    }
    result.update(changes)
    return result


def _scenario_risk() -> dict:
    return {
        "initialQuantityBtc": 0.4,
        "addQuantityBtc": 0.8,
        "totalQuantityBtc": 1.2,
        "remainingQuantityAfterPlannedReduce": 0.4,
        "initialFillPrice": 100,
        "averageEntryPrice": 280 / 3,
        "openingFee": 0.04,
        "addFee": 0.08,
        "estimatedReduceFee": 0.04,
        "reduceZonePrice": 95,
    }


def test_cross_margin_without_no_other_positions_assumption_disables_all_planned_and_actual_estimates():
    plan = _scenario_plan(assumeNoOtherPositions=False)
    planned = planned_liquidation_scenarios(
        plan,
        _scenario_risk(),
        mark_price=100,
        mark_price_time=1_800_000_000_000,
        public_context=_public_context(),
    )
    assert set(planned) == {"initialOnly", "afterAdd", "afterPlannedReduce"}
    assert {row["status"] for row in planned.values()} == {"UNAVAILABLE"}
    assert all(row["estimatedLiquidationPrice"] is None for row in planned.values())

    actual = actual_liquidation_estimate(
        {
            "plan": plan,
            "actualFills": {"initial": {"price": 100, "quantityBtc": 0.4}},
            "execution": {
                "remainingQuantityBtc": 0.4,
                "averageEntryPrice": 100,
                "realizedGrossPnl": 0,
            },
        },
        mark_price=100,
        mark_price_time=1_800_000_000_000,
        public_context=_public_context(),
    )
    assert actual["status"] == "UNAVAILABLE"
    assert actual["estimatedLiquidationPrice"] is None


def test_isolated_mode_does_not_reuse_cross_margin_estimate():
    planned = planned_liquidation_scenarios(
        _scenario_plan(marginMode="ISOLATED"),
        _scenario_risk(),
        mark_price=100,
        mark_price_time=1_800_000_000_000,
        public_context=_public_context(),
    )
    assert {row["status"] for row in planned.values()} == {"UNAVAILABLE"}
    assert all("仅支持全仓" in row["errors"][0] for row in planned.values())


def test_leverage_above_public_tier_limit_is_warned_and_never_silently_clamped():
    planned = planned_liquidation_scenarios(
        _scenario_plan(leverage=20),
        _scenario_risk(),
        mark_price=100,
        mark_price_time=1_800_000_000_000,
        public_context=_public_context(max_leverage=10),
    )
    for estimate in planned.values():
        assert estimate["status"] == "AVAILABLE"
        assert any("杠杆" in warning and "10" in warning for warning in estimate["warnings"])
        assert estimate["maintenanceMarginRate"] == pytest.approx(0.005)


def test_switching_manual_to_auto_ignores_all_residual_manual_maintenance_fields():
    residual = _scenario_plan(
        maintenanceMarginSource="AUTO",
        maintenanceMarginRate=0.25,
        maintenanceMarginFixedUsdt=25,
    )
    clean = _scenario_plan(
        maintenanceMarginSource="AUTO",
        maintenanceMarginRate=None,
        maintenanceMarginFixedUsdt=0,
    )
    residual_result = planned_liquidation_scenarios(
        residual,
        _scenario_risk(),
        mark_price=100,
        mark_price_time=1_800_000_000_000,
        public_context=_public_context(),
    )
    clean_result = planned_liquidation_scenarios(
        clean,
        _scenario_risk(),
        mark_price=100,
        mark_price_time=1_800_000_000_000,
        public_context=_public_context(),
    )

    for scope in ("initialOnly", "afterAdd", "afterPlannedReduce"):
        switched = residual_result[scope]
        assert switched["status"] == "AVAILABLE"
        assert switched["parameterSource"] == "OKX_PUBLIC"
        assert switched["maintenanceMarginRate"] == pytest.approx(0.005)
        assert switched["maintenanceMarginFixedUsdt"] == 0
        assert switched["estimatedLiquidationPrice"] == clean_result[scope]["estimatedLiquidationPrice"]

    manual_result = planned_liquidation_scenarios(
        {**residual, "maintenanceMarginSource": "MANUAL"},
        _scenario_risk(),
        mark_price=100,
        mark_price_time=1_800_000_000_000,
        public_context=_public_context(),
    )
    assert manual_result["initialOnly"]["parameterSource"] == "MANUAL"
    assert manual_result["initialOnly"]["maintenanceMarginRate"] == pytest.approx(0.25)
    assert manual_result["initialOnly"]["maintenanceMarginFixedUsdt"] == 25
    assert manual_result["initialOnly"]["estimatedLiquidationPrice"] != residual_result["initialOnly"]["estimatedLiquidationPrice"]


def test_stale_mark_keeps_estimate_but_withholds_all_mark_relative_distance_fields():
    now = 1_800_000_000_000
    stale = planned_liquidation_scenarios(
        _scenario_plan(),
        _scenario_risk(),
        mark_price=100,
        mark_price_time=now - 30_001,
        public_context=_public_context(),
        evaluated_at=now,
    )["afterAdd"]

    assert stale["status"] == "AVAILABLE"
    assert stale["estimatedLiquidationPrice"] is not None
    assert stale["referenceMarkStatus"] == "STALE"
    assert stale["distanceStatus"] == "UNAVAILABLE"
    assert stale["distancePercent"] is None
    assert stale["distanceRisk"] == "不可用"
    assert any("标记价格已超过 30 秒" in warning for warning in stale["warnings"])

    fresh_boundary = planned_liquidation_scenarios(
        _scenario_plan(),
        _scenario_risk(),
        mark_price=100,
        mark_price_time=now - 30_000,
        public_context=_public_context(),
        evaluated_at=now,
    )["afterAdd"]
    assert fresh_boundary["referenceMarkStatus"] == "AVAILABLE"
    assert fresh_boundary["distanceStatus"] == "AVAILABLE"
    assert fresh_boundary["distancePercent"] is not None
