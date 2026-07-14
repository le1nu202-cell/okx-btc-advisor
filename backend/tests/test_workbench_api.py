import os
import asyncio
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

os.environ["OKX_DISABLE_NETWORK"] = "1"
os.environ.setdefault("OKX_ADVISOR_DB", os.path.join(tempfile.gettempdir(), f"okx-workbench-api-{uuid.uuid4()}.db"))

from fastapi.testclient import TestClient

from backend import main
from backend.db import Database
from backend.models import Candle


BASE_PLAN = {
    "direction": "LONG",
    "initialEntryPrice": 100,
    "addPrice": 90,
    "stopPrice": 80,
    "takeProfitPrice": 120,
    "equity": 80,
    "leverage": 66,
    "initialMarginPercent": 4,
    "addMultiplier": 2,
    "makerFeeBps": 2,
    "takerFeeBps": 5,
    "slippageBps": 5,
    "marginMode": "ISOLATED",
}


def test_workbench_calculate_and_plan_restart_recovery(tmp_path, monkeypatch):
    database_path = tmp_path / "plans.db"
    local = Database(database_path)
    monkeypatch.setattr(main, "db", local)
    with TestClient(main.app) as client:
        calculated = client.post("/api/workbench/calculate", json=BASE_PLAN)
        assert calculated.status_code == 200
        risk = calculated.json()
        assert risk["valid"] and risk["initialMargin"] == 3.2
        assert risk["initialQuantityBtc"] != risk["addQuantityBtc"] / 2
        created = client.post("/api/trade-plans", json=BASE_PLAN)
        assert created.status_code == 201
        plan_id = created.json()["id"]
        assert client.get("/api/trade-plans/current").json()["id"] == plan_id
    # Reopen the SQLite file through a new Database instance, as a restarted
    # service would, rather than merely constructing a second HTTP client.
    reopened = Database(database_path)
    monkeypatch.setattr(main, "db", reopened)
    with TestClient(main.app) as client:
        restored = client.get("/api/trade-plans/current").json()
        assert restored["id"] == plan_id and restored["state"] == "PLANNED"


def test_plan_actions_enforce_single_add_and_create_terminal_log(tmp_path, monkeypatch):
    local = Database(tmp_path / "actions.db")
    monkeypatch.setattr(main, "db", local)
    with TestClient(main.app) as client:
        record = client.post("/api/trade-plans", json=BASE_PLAN).json()
        plan_id = record["id"]
        q0 = record["risk"]["initialQuantityBtc"]
        qa = record["risk"]["addQuantityBtc"]
        assert client.post(f"/api/trade-plans/{plan_id}/actions", json={"action": "CONFIRM_INITIAL", "price": 100, "quantityBtc": q0}).status_code == 200
        added = client.post(f"/api/trade-plans/{plan_id}/actions", json={"action": "CONFIRM_ADD", "price": 90, "quantityBtc": qa})
        assert added.status_code == 200 and added.json()["plan"]["addCount"] == 1
        # Even a corrupted/replayed state cannot generate a third ladder leg.
        damaged = local.get_trade_plan(plan_id)
        damaged["state"] = "APPROACHING_ADD"
        local.save_trade_plan(damaged)
        repeated = client.post(f"/api/trade-plans/{plan_id}/actions", json={"action": "CONFIRM_ADD", "price": 89, "quantityBtc": qa})
        assert repeated.status_code == 409 and "最多只允许一次" in repeated.json()["detail"]
        damaged["state"] = "ADDED"
        local.save_trade_plan(damaged)
        stopped = client.post(f"/api/trade-plans/{plan_id}/actions", json={"action": "CONFIRM_STOP", "price": 80, "quantityBtc": q0 + qa})
        assert stopped.status_code == 200 and stopped.json()["log"]["source"] == "live"
        assert client.get("/api/trade-logs?source=live").json()["items"][0]["planId"] == plan_id
        assert client.get("/api/trade-logs/statistics?source=live").json()["totalTrades"] == 1


def test_invalid_price_order_is_visible_and_not_saved(tmp_path, monkeypatch):
    local = Database(tmp_path / "invalid.db")
    monkeypatch.setattr(main, "db", local)
    invalid = {**BASE_PLAN, "addPrice": 105}
    with TestClient(main.app) as client:
        calculation = client.post("/api/workbench/calculate", json=invalid)
        assert calculation.status_code == 200 and calculation.json()["valid"] is False
        saved = client.post("/api/trade-plans", json=invalid)
        assert saved.status_code == 422
        assert client.get("/api/trade-plans/current").json()["state"] == "IDLE"


def test_replay_api_hides_future_and_stop_wins_same_candle(tmp_path, monkeypatch):
    local = Database(tmp_path / "replay.db")
    monkeypatch.setattr(main, "db", local)
    base = 1_700_000_000_000
    one = [Candle(timestamp=base+i*3_600_000,open=100,high=101,low=99,close=100,volume=1,timeframe="1H",confirm=True) for i in range(320)]
    # The next revealed candle hits stop, target and add; stop must win.
    one[221] = Candle(timestamp=one[221].timestamp,open=100,high=130,low=70,close=110,volume=2,timeframe="1H",confirm=True)
    four = [Candle(timestamp=base+i*4*3_600_000,open=100,high=102,low=98,close=100,volume=4,timeframe="4H",confirm=True) for i in range(80)]
    local.upsert_candles(main.INSTRUMENT, [*one, *four])
    with TestClient(main.app) as client:
        created = client.post("/api/replay/sessions", json={"mode":"manual","startAt":one[220].timestamp})
        assert created.status_code == 201
        body = created.json(); session_id = body["id"]
        assert max(row["timestamp"] for row in body["candles1H"]) == one[220].timestamp
        decision_close = one[220].timestamp + 3_600_000
        assert all(row["timestamp"] + 4*3_600_000 <= decision_close for row in body["candles4H"])
        planned = client.put(f"/api/replay/sessions/{session_id}/plan", json={"plan":BASE_PLAN})
        assert planned.status_code == 200 and planned.json()["state"] == "INITIAL_OPEN"
        stepped = client.post(f"/api/replay/sessions/{session_id}/step?count=1")
        result = stepped.json()
        assert result["state"] == "STOPPED" and result["status"] == "COMPLETE"
        assert result["result"]["source"] == "replay"
        assert client.get("/api/trade-logs?source=replay").json()["items"][0]["planId"] == session_id


def test_manual_fill_api_requires_both_price_and_quantity(tmp_path, monkeypatch):
    local = Database(tmp_path / "required-fill.db")
    monkeypatch.setattr(main, "db", local)
    with TestClient(main.app) as client:
        record = client.post("/api/trade-plans", json=BASE_PLAN).json()
        response = client.post(
            f"/api/trade-plans/{record['id']}/actions",
            json={"action": "CONFIRM_INITIAL", "price": 100},
        )
    assert response.status_code == 422
    assert "实际 BTC 数量" in str(response.json())


def test_partial_reduce_and_final_exit_persist_execution_without_double_count(tmp_path, monkeypatch):
    local = Database(tmp_path / "partial.db")
    monkeypatch.setattr(main, "db", local)
    with TestClient(main.app) as client:
        record = client.post("/api/trade-plans", json={**BASE_PLAN, "makerFeeBps": 0, "takerFeeBps": 0, "slippageBps": 0}).json()
        plan_id = record["id"]
        client.post(f"/api/trade-plans/{plan_id}/actions", json={"action":"CONFIRM_INITIAL","price":100,"quantityBtc":0.4})
        client.post(f"/api/trade-plans/{plan_id}/actions", json={"action":"CONFIRM_ADD","price":90,"quantityBtc":0.8})
        reduced = client.post(f"/api/trade-plans/{plan_id}/actions", json={"action":"CONFIRM_REDUCE","price":95,"quantityBtc":0.8})
        assert reduced.status_code == 200
        execution = reduced.json()["plan"]["execution"]
        assert execution["remainingQuantityBtc"] == pytest.approx(0.4)
        assert execution["realizedGrossPnl"] == pytest.approx((95 - 112 / 1.2) * 0.8)
        assert execution["mfeMaeSupported"] is False
        execution_risk = reduced.json()["plan"]["executionRisk"]
        assert execution_risk["quantityBtc"] == pytest.approx(0.4)
        assert execution_risk["grossBreakevenPrice"] == pytest.approx(90)
        assert execution_risk["fullCostBreakevenPrice"] == pytest.approx(90)
        assert execution_risk["remainingNetLossAtStop"] == pytest.approx((112 / 1.2 - 80) * 0.4)
        assert execution_risk["netLossAtStop"] == pytest.approx(4)
        assert execution_risk["totalNetPnlIfStopped"] == pytest.approx(-4)
        final = client.post(f"/api/trade-plans/{plan_id}/actions", json={"action":"CONFIRM_STOP","price":80,"quantityBtc":0.4})
        assert final.status_code == 200
        body = final.json()
        assert body["plan"]["execution"]["remainingQuantityBtc"] == pytest.approx(0)
        assert body["log"]["grossPnl"] == pytest.approx(-4)
        assert len(body["log"]["realizedSegments"]) == 2
        restored = client.get("/api/trade-plans/current").json()
        assert restored["execution"] == body["plan"]["execution"]
        assert restored["executionRisk"] == body["plan"]["executionRisk"]


def test_live_price_stop_only_creates_pending_reminder_and_never_log(tmp_path, monkeypatch):
    local = Database(tmp_path / "reminder.db")
    monkeypatch.setattr(main, "db", local)
    monkeypatch.setattr(main, "_live_market_ready", lambda now_ms=None: (True, "fresh"))
    record = main.make_plan_record(main.TradePlanDraft.model_validate({**BASE_PLAN, "slippageBps": 0}))
    opened, _ = main.apply_action(record, main.TradeActionRequest(action="CONFIRM_INITIAL", price=100, quantity_btc=0.4))
    local.save_trade_plan(opened, active=True)
    main.runtime["plan_check_ts"] = 0
    asyncio.run(main.evaluate_active_plan_price(79))
    restored = local.get_active_trade_plan()
    assert restored["state"] == "INITIAL_OPEN"
    assert restored["activeReminder"]["type"] == "STOP_HIT_PENDING_CONFIRMATION"
    assert "exit" not in restored["actualFills"] and "logId" not in restored
    assert local.trade_logs("live") == []


def test_planned_update_clears_stale_price_reminder(tmp_path, monkeypatch):
    local = Database(tmp_path / "update-reminder.db")
    monkeypatch.setattr(main, "db", local)
    record = main.make_plan_record(main.TradePlanDraft.model_validate(BASE_PLAN))
    record["activeReminder"] = {
        "type": "ENTRY_APPROACH",
        "message": "old entry",
        "price": 100,
        "createdAt": 1,
        "at": 1,
    }
    local.save_trade_plan(record, active=True)
    with TestClient(main.app) as client:
        response = client.put(
            f"/api/trade-plans/{record['id']}",
            json={**BASE_PLAN, "initialEntryPrice": 101, "addPrice": 90},
        )
    assert response.status_code == 200
    assert "activeReminder" not in response.json()
    assert "activeReminder" not in local.get_trade_plan(record["id"])


def test_concurrent_terminal_confirm_is_idempotent_and_creates_one_live_log(tmp_path, monkeypatch):
    local = Database(tmp_path / "concurrent-terminal.db")
    monkeypatch.setattr(main, "db", local)
    with TestClient(main.app) as client:
        record = client.post("/api/trade-plans", json=BASE_PLAN).json()
        plan_id = record["id"]
        quantity = record["risk"]["initialQuantityBtc"]
        opened = client.post(
            f"/api/trade-plans/{plan_id}/actions",
            json={"action": "CONFIRM_INITIAL", "price": 100, "quantityBtc": quantity},
        )
        assert opened.status_code == 200

        start = threading.Event()

        def stop_once():
            start.wait()
            return client.post(
                f"/api/trade-plans/{plan_id}/actions",
                json={"action": "CONFIRM_STOP", "price": 80, "quantityBtc": quantity},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(stop_once) for _ in range(2)]
            start.set()
            responses = [future.result() for future in futures]

    assert sorted(response.status_code for response in responses) == [200, 409]
    logs = local.trade_logs("live")
    assert len(logs) == 1
    assert logs[0]["id"] == f"live:{plan_id}"


def test_extreme_price_input_returns_422_instead_of_500(tmp_path, monkeypatch):
    local = Database(tmp_path / "extreme.db")
    monkeypatch.setattr(main, "db", local)
    tiny = {**BASE_PLAN, "initialEntryPrice": 1e-300, "addPrice": 1e-301, "stopPrice": 1e-302, "takeProfitPrice": 1e-299}
    with TestClient(main.app) as client:
        response = client.post("/api/workbench/calculate", json=tiny)
    assert response.status_code == 422


def test_add_check_degrades_to_data_insufficient_when_market_is_stale(tmp_path, monkeypatch):
    local = Database(tmp_path / "check.db")
    monkeypatch.setattr(main, "db", local)
    local.save_trade_plan(main.make_plan_record(main.TradePlanDraft.model_validate(BASE_PLAN)))
    monkeypatch.setattr(main, "_live_market_ready", lambda now_ms=None: (False, "stale"))
    with TestClient(main.app) as client:
        response = client.get("/api/workbench/add-check")
    assert response.status_code == 200 and response.json()["status"] == "数据不足"
