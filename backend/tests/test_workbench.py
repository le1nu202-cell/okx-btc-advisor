import random

import pytest

from backend.db import Database
from backend.models import Candle
from backend.workbench import (
    CrossEquityMode,
    LiquidationFeeMode,
    MarginMode,
    ReplayCreateRequest,
    RiskCalculation,
    SizingMode,
    TradeAction,
    TradeActionRequest,
    TradeDirection,
    TradePlanDraft,
    TradeState,
    apply_action,
    build_trade_log,
    calculate_risk,
    make_plan_record,
    recompute_execution,
    price_trigger,
    replay_candle_transition,
    replay_start_index,
    trade_statistics,
    visible_replay_candles,
)


def plan(**changes):
    values = dict(
        direction="LONG",
        initial_entry_price=100,
        add_price=90,
        stop_price=80,
        take_profit_price=120,
        equity=80,
        leverage=10,
        initial_margin=4,
        initial_margin_percent=5,
        add_multiplier=2,
        maker_fee_bps=0,
        taker_fee_bps=0,
        slippage_bps=0,
    )
    values.update(changes)
    return TradePlanDraft(**values)


def test_long_fixed_vector_uses_fill_specific_quantities_and_exact_average():
    result = calculate_risk(plan())
    assert result.valid
    assert result.initial_notional == pytest.approx(40)
    assert result.initial_quantity_btc == pytest.approx(0.4)
    assert result.add_notional == pytest.approx(80)
    assert result.add_quantity_btc == pytest.approx(80 / 90)
    assert result.total_quantity_btc == pytest.approx(0.4 + 80 / 90)
    assert result.average_entry_price == pytest.approx(120 / (0.4 + 80 / 90))
    assert result.average_entry_price != pytest.approx((100 + 2 * 90) / 3)
    assert result.net_loss_at_stop == pytest.approx(16.8888888889)
    assert result.net_profit_at_take_profit == pytest.approx(10.7586206897)


def test_follow_modes_sync_and_legacy_json_infers_manual_only_for_distinct_values():
    followed = TradePlanDraft.model_validate({
        **plan().model_dump(by_alias=True),
        "equity": 100,
        "crossEquityMode": "FOLLOW_EQUITY",
        "crossAvailableEquity": 12,
        "takerFeeBps": 7,
        "liquidationFeeMode": "FOLLOW_TAKER",
        "liquidationFeeBps": 99,
    })
    assert followed.cross_equity_mode == CrossEquityMode.FOLLOW_EQUITY
    assert followed.cross_available_equity == 100
    assert followed.liquidation_fee_mode == LiquidationFeeMode.FOLLOW_TAKER
    assert followed.liquidation_fee_bps == 7

    # Simulate fields being absent, not explicit null, in a v0.5 JSON blob.
    legacy_payload = followed.model_dump(by_alias=True)
    legacy_payload.pop("crossEquityMode")
    legacy_payload.pop("liquidationFeeMode")
    legacy_payload["crossAvailableEquity"] = 40
    legacy_payload["liquidationFeeBps"] = 11
    inferred_manual = TradePlanDraft.model_validate(legacy_payload)
    assert inferred_manual.cross_equity_mode == CrossEquityMode.MANUAL
    assert inferred_manual.cross_available_equity == 40
    assert inferred_manual.liquidation_fee_mode == LiquidationFeeMode.MANUAL
    assert inferred_manual.liquidation_fee_bps == 11

    equal_payload = {**legacy_payload, "crossAvailableEquity": 100, "liquidationFeeBps": 7}
    inferred_follow = TradePlanDraft.model_validate(equal_payload)
    assert inferred_follow.cross_equity_mode == CrossEquityMode.FOLLOW_EQUITY
    assert inferred_follow.cross_available_equity == inferred_follow.equity == 100
    assert inferred_follow.liquidation_fee_mode == LiquidationFeeMode.FOLLOW_TAKER
    assert inferred_follow.liquidation_fee_bps == inferred_follow.taker_fee_bps == 7

    with pytest.raises(ValueError, match="crossAvailableEquity"):
        TradePlanDraft.model_validate({**equal_payload, "crossEquityMode": "MANUAL", "crossAvailableEquity": None})


def test_initial_fill_freezes_equity_basis_and_legacy_open_fill_is_lazily_migrated():
    configured = plan(equity=80, cross_equity_mode="FOLLOW_EQUITY", cross_available_equity=12)
    opened, _ = apply_action(
        make_plan_record(configured),
        TradeActionRequest(action=TradeAction.CONFIRM_INITIAL, price=100, quantity_btc=0.4),
    )
    assert opened["liquidationEquityBasisUsdt"] == 80

    changed_plan = {**opened["plan"], "equity": 100, "crossAvailableEquity": 100}
    recomputed = recompute_execution({**opened, "plan": changed_plan})
    assert recomputed["liquidationEquityBasisUsdt"] == 80

    legacy = make_plan_record(plan(equity=80, cross_equity_mode="MANUAL", cross_available_equity=40))
    legacy["plan"].pop("crossEquityMode")
    legacy["actualFills"] = {"initial": {"price": 100, "quantityBtc": 0.4}}
    migrated = recompute_execution(legacy)
    assert migrated["liquidationEquityBasisUsdt"] == 40


def test_short_fixed_vector_and_directional_order():
    result = calculate_risk(plan(direction="SHORT", add_price=110, stop_price=120, take_profit_price=80))
    assert result.valid
    assert result.initial_quantity_btc == pytest.approx(0.4)
    assert result.add_quantity_btc == pytest.approx(80 / 110)
    assert result.average_entry_price == pytest.approx(120 / (0.4 + 80 / 110))
    assert result.net_loss_at_stop > 0
    invalid = calculate_risk(plan(direction="SHORT"))
    assert not invalid.valid and "做空价格顺序" in invalid.errors[0]


def test_fee_adjusted_breakeven_includes_entry_exit_and_slippage():
    configured = plan(maker_fee_bps=2, taker_fee_bps=5, slippage_bps=10)
    result = calculate_risk(configured)
    assert result.valid
    assert result.initial_fill_price == pytest.approx(100.1)
    assert result.add_fill_price == pytest.approx(90.09)
    assert result.fee_adjusted_breakeven_price > result.gross_breakeven_price
    assert result.total_fees_at_take_profit == pytest.approx(
        result.opening_fee + result.add_fee + result.estimated_reduce_fee + result.estimated_close_fee
    )
    assert result.net_profit_at_take_profit == pytest.approx(
        result.gross_profit_at_take_profit - result.total_fees_at_take_profit - result.estimated_slippage_at_take_profit
    )


def test_reverse_size_by_max_loss_is_linear_and_does_not_hide_original_limit():
    configured = plan(
        initial_margin=None,
        initial_margin_percent=5,
        sizing_mode=SizingMode.MAX_LOSS,
        max_loss_usdt=3,
        loss_limit_usdt=3,
    )
    result = calculate_risk(configured)
    assert result.valid
    assert result.max_initial_margin_by_loss is not None
    assert result.initial_margin == pytest.approx(min(result.max_initial_margin_by_loss, 80 / 3))
    assert result.net_loss_at_stop == pytest.approx(3)
    oversized = calculate_risk(plan(initial_margin=10, loss_limit_usdt=3, max_loss_usdt=3))
    assert oversized.loss_limit_exceeded
    assert oversized.max_initial_margin_by_loss < oversized.initial_margin


@pytest.mark.parametrize(
    "changes",
    [
        {"add_price": 100},
        {"stop_price": 90},
        {"take_profit_price": 99},
        {"direction": "SHORT", "add_price": 90, "stop_price": 80, "take_profit_price": 120},
    ],
)
def test_zero_distance_and_illegal_price_order_are_rejected(changes):
    result = calculate_risk(plan(**changes))
    assert not result.valid and result.errors


def test_extreme_leverage_remains_finite_and_warns_about_liquidation():
    result = calculate_risk(plan(leverage=125, initial_margin=2, margin_mode=MarginMode.ISOLATED))
    assert result.valid
    assert all(value is None or isinstance(value, (int, float)) for value in [result.net_loss_at_stop, result.average_entry_price])
    assert result.liquidation_warning


def test_state_machine_allows_only_one_add_and_persists_events(tmp_path):
    record = make_plan_record(plan())
    initial, _ = apply_action(record, TradeActionRequest(action=TradeAction.CONFIRM_INITIAL, price=100, quantity_btc=0.4))
    near, event, _ = price_trigger(initial, 90, True)
    assert near["state"] == TradeState.INITIAL_OPEN and event == "APPROACHING_ADD"
    assert near["activeReminder"]["type"] == "APPROACHING_ADD"
    added, _ = apply_action(near, TradeActionRequest(action=TradeAction.CONFIRM_ADD, price=90, quantity_btc=0.8))
    assert added["addCount"] == 1 and added["state"] == TradeState.ADDED
    with pytest.raises(ValueError, match="最多只允许一次"):
        apply_action({**added, "state": TradeState.APPROACHING_ADD.value}, TradeActionRequest(action=TradeAction.CONFIRM_ADD, price=89, quantity_btc=0.8))
    db = Database(tmp_path / "state.db")
    db.save_trade_plan(added)
    db.save_trade_plan_event(added["id"], TradeState.INITIAL_OPEN.value, TradeState.ADDED.value, "CONFIRM_ADD", 90)
    restored = db.get_active_trade_plan()
    assert restored["state"] == TradeState.ADDED and restored["addCount"] == 1
    assert db.trade_plan_events(added["id"])[0]["eventType"] == "CONFIRM_ADD"


def test_replay_visibility_never_returns_future_1h_or_unclosed_4h():
    base = 1_700_000_000_000
    one = [Candle(timestamp=base + i * 3_600_000, open=100, high=101, low=99, close=100, volume=1, timeframe="1H") for i in range(300)]
    four = [Candle(timestamp=base + i * 4 * 3_600_000, open=100, high=101, low=99, close=100, volume=4, timeframe="4H") for i in range(75)]
    index = replay_start_index(one, "random", None, random.Random(7))
    visible_one, visible_four = visible_replay_candles(one, four, index)
    assert max(c.timestamp for c in visible_one) == one[index].timestamp
    decision_close = one[index].timestamp + 3_600_000
    assert all(c.timestamp + 4 * 3_600_000 <= decision_close for c in visible_four)
    manual = replay_start_index(one, "manual", one[220].timestamp)
    assert manual == 220


def test_replay_same_candle_stop_wins_over_target_and_add():
    record = make_plan_record(plan())
    record["state"] = TradeState.INITIAL_OPEN.value
    candle = Candle(timestamp=1, open=100, high=130, low=70, close=110, volume=1, timeframe="1H")
    updated, event = replay_candle_transition(record, candle)
    assert event == "HARD_STOP" and updated["state"] == TradeState.STOPPED


def test_logs_and_statistics_keep_replay_separate(tmp_path):
    record = make_plan_record(plan())
    record["state"] = TradeState.TAKE_PROFIT.value
    record["actualFills"] = {
        "initial": {"price": 100, "quantityBtc": 0.4},
        "exit": {"price": 120, "quantityBtc": 0.4},
    }
    log = build_trade_log(record, "live", "TREND")
    db = Database(tmp_path / "logs.db")
    db.save_trade_log(log, "live")
    replay = {**log, "id": "replay-log", "source": "replay"}
    db.save_trade_log(replay, "replay")
    assert len(db.trade_logs("live")) == 1 and len(db.trade_logs("replay")) == 1
    stats = trade_statistics(db.trade_logs("live"))
    assert stats["totalTrades"] == 1
    assert stats["byDirection"]["LONG"]["trades"] == 1
    assert stats["byRegime"]["TREND"]["trades"] == 1


def test_request_models_reject_invalid_replay_mode_and_threshold_order():
    with pytest.raises(ValueError):
        ReplayCreateRequest(mode="future")
    with pytest.raises(ValueError):
        plan(risk_low_max_percent=5, risk_medium_max_percent=4)


def test_three_breakevens_and_short_frozen_vector_are_exact():
    result = calculate_risk(plan(maker_fee_bps=2, taker_fee_bps=5, slippage_bps=10))
    assert result.gross_breakeven_price == pytest.approx(result.average_entry_price)
    assert result.gross_breakeven_price < result.fee_breakeven_price < result.full_cost_breakeven_price
    assert result.all_in_breakeven_price == pytest.approx(result.full_cost_breakeven_price)
    assert result.fee_adjusted_breakeven_price == pytest.approx(result.full_cost_breakeven_price)
    assert result.total_margin == pytest.approx(result.initial_margin + result.add_margin)
    assert result.total_notional == pytest.approx(result.initial_notional + result.add_notional)
    assert result.remaining_quantity_after_planned_reduce == pytest.approx(result.initial_quantity_btc)

    short = calculate_risk(TradePlanDraft(
        direction="SHORT", initial_entry_price=100_000, add_price=101_000,
        stop_price=102_000, take_profit_price=99_000, equity=80,
        leverage=66, initial_margin=3.2, add_multiplier=2,
        maker_fee_bps=2, taker_fee_bps=5, slippage_bps=5,
    ))
    assert short.full_cost_breakeven_price == pytest.approx(100473.3754596848)
    assert short.net_loss_at_stop == pytest.approx(9.6232698125)
    assert short.net_loss_at_stop == pytest.approx(
        short.gross_loss_at_stop + short.total_fees_at_stop + short.estimated_slippage_at_stop
    )


def test_confirmations_require_price_and_quantity_and_terminal_blocks_more_actions():
    with pytest.raises(ValueError, match="实际价格和实际 BTC 数量"):
        TradeActionRequest(action=TradeAction.CONFIRM_INITIAL, price=100)
    record = make_plan_record(plan())
    opened, _ = apply_action(record, TradeActionRequest(
        action=TradeAction.CONFIRM_INITIAL, price=100, quantity_btc=0.4,
    ))
    assert opened["actualFills"]["initial"]["confirmedAt"] == opened["updatedAt"]
    assert recompute_execution(opened)["actualFills"]["initial"]["confirmedAt"] == opened["updatedAt"]
    closed, _ = apply_action(opened, TradeActionRequest(
        action=TradeAction.CONFIRM_STOP, price=80, quantity_btc=0.4,
    ))
    with pytest.raises(ValueError, match="已经结束"):
        apply_action(closed, TradeActionRequest(
            action=TradeAction.CONFIRM_ADD, price=90, quantity_btc=0.8,
        ))


def test_open_execution_reports_all_incurred_entry_fees_without_realizing_them():
    record = make_plan_record(plan(maker_fee_bps=2, taker_fee_bps=5))
    opened, _ = apply_action(record, TradeActionRequest(
        action=TradeAction.CONFIRM_INITIAL, price=100, quantity_btc=0.4,
    ))
    execution = opened["execution"]
    assert execution["incurredFees"] == pytest.approx(100 * 0.4 * 2 / 10_000)
    assert execution["fees"] == pytest.approx(0)
    assert execution["realizedNetPnl"] == pytest.approx(0)


def test_market_triggers_only_persist_reminders_and_stale_data_does_nothing():
    planned = make_plan_record(plan())
    stale, event, _ = price_trigger(planned, 100, False)
    assert stale == planned and event is None
    entry, event, _ = price_trigger(planned, 100, True)
    assert entry["state"] == TradeState.PLANNED and event == "ENTRY_APPROACH"
    assert entry["actualFills"] == {} and "logId" not in entry

    opened, _ = apply_action(planned, TradeActionRequest(
        action=TradeAction.CONFIRM_INITIAL, price=100, quantity_btc=0.4,
    ))
    approaching_add, event, message = price_trigger(opened, 90, True)
    assert approaching_add["state"] == TradeState.INITIAL_OPEN
    assert event == "APPROACHING_ADD" and "不会自动加仓" in message
    assert approaching_add["actualFills"] == opened["actualFills"]

    added, _ = apply_action(approaching_add, TradeActionRequest(
        action=TradeAction.CONFIRM_ADD, price=90, quantity_btc=0.8,
    ))
    reduce_price = float(added["risk"]["reduceZonePrice"])
    reduce_zone, event, message = price_trigger(added, reduce_price, True)
    assert reduce_zone["state"] == TradeState.ADDED
    assert event == "REDUCE_ZONE" and "人工确认" in message
    assert reduce_zone["actualFills"] == added["actualFills"]

    stopped, event, message = price_trigger(opened, 79, True)
    assert stopped["state"] == TradeState.INITIAL_OPEN
    assert event == "STOP_HIT_PENDING_CONFIRMATION" and "人工确认" in message
    assert "exit" not in stopped["actualFills"] and "logId" not in stopped
    repeated, repeated_event, _ = price_trigger(stopped, 79, True)
    assert repeated == stopped and repeated_event is None


def test_partial_reduce_uses_actual_cost_pool_and_final_exit_never_double_counts():
    record = make_plan_record(plan())
    opened, _ = apply_action(record, TradeActionRequest(
        action=TradeAction.CONFIRM_INITIAL, price=100, quantity_btc=0.4,
    ))
    added, _ = apply_action(opened, TradeActionRequest(
        action=TradeAction.CONFIRM_ADD, price=90, quantity_btc=0.8,
    ))
    reduced, _ = apply_action(added, TradeActionRequest(
        action=TradeAction.CONFIRM_REDUCE, price=85, quantity_btc=0.8,
    ))
    execution = reduced["execution"]
    assert execution["averageEntryPrice"] == pytest.approx(112 / 1.2)
    assert execution["remainingQuantityBtc"] == pytest.approx(0.4)
    assert execution["remainingEntryCost"] == pytest.approx((112 / 1.2) * 0.4)
    assert execution["realizedGrossPnl"] == pytest.approx((85 - 112 / 1.2) * 0.8)
    assert not execution["mfeMaeSupported"] and reduced["mfeUsdt"] is None
    actual_risk = reduced["executionRisk"]
    assert actual_risk["quantityBtc"] == pytest.approx(0.4)
    assert actual_risk["averageEntryPrice"] == pytest.approx(112 / 1.2)
    # After a losing partial reduce, the whole-trade breakeven must recover
    # the realized loss rather than reverting to the remaining-leg average.
    assert actual_risk["grossBreakevenPrice"] == pytest.approx(110)
    assert actual_risk["feeBreakevenPrice"] == pytest.approx(110)
    assert actual_risk["fullCostBreakevenPrice"] == pytest.approx(110)
    assert actual_risk["remainingNetLossAtStop"] == pytest.approx((112 / 1.2 - 80) * 0.4)
    assert actual_risk["netLossAtStop"] == pytest.approx(12.0)
    assert actual_risk["totalNetPnlIfStopped"] == pytest.approx(-12.0)
    assert actual_risk["maxLossEquityPercent"] == pytest.approx(15.0)

    final, _ = apply_action(reduced, TradeActionRequest(
        action=TradeAction.CONFIRM_STOP, price=80, quantity_btc=0.4,
    ))
    assert final["execution"]["remainingQuantityBtc"] == pytest.approx(0)
    assert final["execution"]["realizedGrossPnl"] == pytest.approx(-12.0)
    assert len(final["realizedSegments"]) == 2
    assert final["executionRisk"]["quantityBtc"] == pytest.approx(0)
    assert final["executionRisk"]["totalNetPnlIfStopped"] == pytest.approx(-12.0)
    log = build_trade_log(final)
    assert log["grossPnl"] == pytest.approx(-12.0)
    assert log["netPnl"] == pytest.approx(-12.0)


def test_execution_allocates_entry_fees_proportionally_and_reads_legacy_numeric_fills():
    configured = plan(maker_fee_bps=10, taker_fee_bps=20)
    record = make_plan_record(configured)
    record["state"] = TradeState.TAKE_PROFIT.value
    record["addCount"] = 1
    record["actualFills"] = {"initial": 100, "add": 90, "reduce": 95, "exit": 120}
    updated = recompute_execution(record)
    assert all(isinstance(fill, dict) for fill in updated["actualFills"].values())
    assert updated["execution"]["remainingQuantityBtc"] == pytest.approx(0)
    entry_fees = 0.4 * 100 * 0.001 + (80 / 90) * 90 * 0.002
    assert updated["execution"]["allocatedEntryFees"] == pytest.approx(entry_fees)
    assert updated["execution"]["fees"] == pytest.approx(
        entry_fees + (80 / 90) * 95 * 0.002 + 0.4 * 120 * 0.002
    )


def test_execution_risk_uses_actual_fills_remaining_entry_fee_and_future_exit_costs():
    configured = plan(maker_fee_bps=10, taker_fee_bps=20, slippage_bps=10)
    record = make_plan_record(configured)
    opened, _ = apply_action(record, TradeActionRequest(
        action=TradeAction.CONFIRM_INITIAL, price=101, quantity_btc=0.4,
    ))
    assert opened["executionRisk"]["quantityBtc"] == pytest.approx(0.4)
    assert opened["executionRisk"]["averageEntryPrice"] == pytest.approx(101)
    added, _ = apply_action(opened, TradeActionRequest(
        action=TradeAction.CONFIRM_ADD, price=89, quantity_btc=0.8,
    ))
    actual = added["executionRisk"]
    average = (101 * 0.4 + 89 * 0.8) / 1.2
    entry_fee = 101 * 0.4 * 0.001 + 89 * 0.8 * 0.002
    fee_be = (average * 1.2 + entry_fee) / (1.2 * (1 - 0.002))
    assert actual["quantityBtc"] == pytest.approx(1.2)
    assert actual["averageEntryPrice"] == pytest.approx(average)
    assert actual["remainingEntryFee"] == pytest.approx(entry_fee)
    assert actual["feeBreakevenPrice"] == pytest.approx(fee_be)
    assert actual["fullCostBreakevenPrice"] == pytest.approx(fee_be / (1 - 0.001))
    stop_fill = 80 * (1 - 0.001)
    expected_net = stop_fill * 1.2 - average * 1.2 - entry_fee - stop_fill * 1.2 * 0.002
    assert actual["remainingNetPnlAtStop"] == pytest.approx(expected_net)
    assert actual["totalNetPnlIfStopped"] == pytest.approx(expected_net)


def test_short_execution_risk_uses_directional_stop_math():
    record = make_plan_record(plan(direction="SHORT", add_price=110, stop_price=120, take_profit_price=80))
    opened, _ = apply_action(record, TradeActionRequest(
        action=TradeAction.CONFIRM_INITIAL, price=100, quantity_btc=0.4,
    ))
    added, _ = apply_action(opened, TradeActionRequest(
        action=TradeAction.CONFIRM_ADD, price=110, quantity_btc=0.8,
    ))
    actual = added["executionRisk"]
    assert actual["averageEntryPrice"] == pytest.approx(128 / 1.2)
    assert actual["netLossAtStop"] == pytest.approx(16)
    assert actual["totalNetPnlIfStopped"] == pytest.approx(-16)
    assert actual["maxLossEquityPercent"] == pytest.approx(20)


def test_tiny_price_is_rejected_before_math_can_overflow():
    with pytest.raises(ValueError):
        plan(initial_entry_price=1e-300, add_price=1e-301, stop_price=1e-302, take_profit_price=1e-299)
