import csv
import io
import json
import os
import sqlite3
import tempfile
import uuid

import pytest

os.environ["OKX_DISABLE_NETWORK"] = "1"
os.environ.setdefault("OKX_ADVISOR_DB", os.path.join(tempfile.gettempdir(), f"okx-history-{uuid.uuid4()}.db"))

from fastapi.testclient import TestClient

from backend import main
from backend.db import Database
from backend.workbench import TradePlanDraft, make_plan_record


def _log(
    id: str,
    *,
    source: str = "live",
    closed_at: int = 1,
    pnl: float = 0.0,
    direction: str = "LONG",
    state: str = "TAKE_PROFIT",
    fees: float = 0.0,
    slippage: float = 0.0,
    equity: float = 80.0,
    added: bool = False,
    reduced: bool = False,
    notes: str = "",
    screenshot_path: str | None = None,
) -> dict:
    return {
        "id": id,
        "planId": f"plan-{id}",
        "source": source,
        "direction": direction,
        "state": state,
        "netPnl": pnl,
        "grossPnl": pnl + fees + slippage,
        "fees": fees,
        "slippageUsdt": slippage,
        "startingEquity": equity,
        "addTriggered": added,
        "returnedToReduceZone": reduced,
        "notes": notes,
        "screenshotPath": screenshot_path,
        "closedAt": closed_at,
    }


@pytest.fixture
def history_client(tmp_path, monkeypatch):
    local = Database(tmp_path / "history.db")
    monkeypatch.setattr(main, "db", local)
    with TestClient(main.app) as client:
        yield client, local


def _save(local: Database, *rows: dict) -> None:
    for row in rows:
        local.save_trade_log(row, row["source"])


def _save_replay_session(local: Database, session_id: str, log: dict | None) -> None:
    record = {
        "id": session_id,
        "status": "COMPLETE" if log else "RUNNING",
        "cursorTs": 1,
        "createdAt": 1,
        "updatedAt": 1,
        "result": log,
    }
    if log:
        record["logId"] = log["id"]
    local.save_replay_session(record)


def test_history_list_limit_and_live_replay_isolation(history_client):
    client, local = history_client
    _save(
        local,
        _log("live-old", closed_at=10, pnl=1),
        _log("live-new", closed_at=30, pnl=2),
        _log("live-middle", closed_at=20, pnl=3),
        _log("replay-new", source="replay", closed_at=40, pnl=99),
    )

    response = client.get("/api/trade-logs?source=live&limit=2")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["items"]] == ["live-new", "live-middle"]
    replay = client.get("/api/trade-logs?source=replay&limit=10").json()["items"]
    assert [row["id"] for row in replay] == ["replay-new"]


def test_trade_log_id_cannot_cross_live_replay_source(history_client):
    _, local = history_client
    local.save_trade_log(_log("shared-id", source="live"), "live")
    with pytest.raises(ValueError, match="another source"):
        local.save_trade_log(_log("shared-id", source="replay"), "replay")
    assert [row["id"] for row in local.trade_logs("live")] == ["shared-id"]
    assert local.trade_logs("replay") == []


def test_statistics_use_all_rows_and_not_the_history_page_limit(history_client):
    client, local = history_client
    records = [_log(f"row-{index}", closed_at=index + 1, pnl=1) for index in range(1_005)]
    # Seed one transaction so this boundary test measures the statistics API,
    # not a thousand independent SQLite commit cycles.
    with local.session() as connection:
        connection.executemany(
            "INSERT INTO trade_logs(id,plan_id,source,payload,closed_at) VALUES(?,?,?,?,?)",
            [
                (
                    row["id"],
                    row["planId"],
                    "live",
                    json.dumps(row, ensure_ascii=False, allow_nan=False),
                    row["closedAt"],
                )
                for row in records
            ],
        )

    stats = client.get("/api/trade-logs/statistics?source=live")
    assert stats.status_code == 200
    body = stats.json()
    assert body["totalTrades"] == 1_005
    assert body["winningTrades"] == 1_005
    assert body["netPnl"] == pytest.approx(1_005)
    assert body["equityCurve"][-1]["cumulativeNetPnl"] == pytest.approx(1_005)


def test_statistics_do_not_backfill_old_logs_from_a_later_starting_equity(history_client):
    client, local = history_client
    old_log = _log("v04-old", closed_at=10, pnl=-4)
    old_log.pop("startingEquity")
    _save(local, old_log, _log("v05-new", closed_at=20, pnl=7, equity=80))

    stats = client.get("/api/trade-logs/statistics?source=live").json()
    assert stats["netPnl"] == pytest.approx(3)
    assert stats["startingEquity"] is None
    assert stats["simulatedEquity"] is None
    assert [point["cumulativeNetPnl"] for point in stats["equityCurve"]] == pytest.approx([-4, 3])
    assert [point["equity"] for point in stats["equityCurve"]] == [None, None]


def test_statistics_only_use_the_earliest_log_starting_equity(history_client):
    client, local = history_client
    middle = _log("middle-old", closed_at=20, pnl=-2)
    middle.pop("startingEquity")
    _save(
        local,
        _log("first-v05", closed_at=10, pnl=1, equity=80),
        middle,
        _log("later-v05", closed_at=30, pnl=5, equity=999),
    )

    stats = client.get("/api/trade-logs/statistics?source=live").json()
    assert stats["startingEquity"] == pytest.approx(80)
    assert stats["simulatedEquity"] == pytest.approx(84)
    assert [point["equity"] for point in stats["equityCurve"]] == pytest.approx([81, 79, 84])


def test_statistics_cover_fees_slippage_drawdown_streak_direction_and_execution_counts(history_client):
    client, local = history_client
    rows = [
        _log("win-long", closed_at=10, pnl=10, direction="LONG", fees=1, slippage=0.5),
        _log("loss-short-1", closed_at=20, pnl=-3, direction="SHORT", state="STOPPED", fees=2, slippage=1, added=True),
        _log("loss-short-2", closed_at=30, pnl=-4, direction="SHORT", state="STOPPED", fees=3, slippage=1.5, added=True),
        _log("flat-long", closed_at=40, pnl=0, direction="LONG", fees=0.5, slippage=0.25, added=True, reduced=True),
        _log("win-short", closed_at=50, pnl=12, direction="SHORT", fees=1.5, slippage=0.75),
    ]
    _save(local, *rows)
    # A saved but unfinished plan is not a closed trade and must not enter history statistics.
    local.save_trade_plan(make_plan_record(TradePlanDraft(
        direction="LONG",
        initialEntryPrice=100,
        addPrice=90,
        stopPrice=80,
        takeProfitPrice=120,
        equity=80,
        leverage=10,
        initialMargin=4,
    )))
    local.save_trade_log({"id": "not-closed", "source": "live", "closedAt": 60}, "live")
    _save(local, _log("replay", source="replay", closed_at=60, pnl=1_000))

    response = client.get("/api/trade-logs/statistics?source=live")
    assert response.status_code == 200
    stats = response.json()
    assert stats["totalTrades"] == 5
    assert stats["winningTrades"] == 2
    assert stats["losingTrades"] == 2
    assert stats["breakevenTrades"] == 1
    assert stats["winRate"] == pytest.approx(2 / 5)
    assert stats["netPnl"] == pytest.approx(15)
    assert stats["totalFees"] == pytest.approx(8)
    assert stats["totalSlippage"] == pytest.approx(4)
    assert stats["maxConsecutiveLosses"] == 2
    assert stats["maxDrawdownUsdt"] == pytest.approx(7)
    assert stats["startingEquity"] == pytest.approx(80)
    assert stats["simulatedEquity"] == pytest.approx(95)
    assert stats["addTriggeredCount"] == 3
    assert stats["returnedToReduceZoneCount"] == 1
    assert stats["stoppedAfterAddCount"] == 2
    assert stats["byDirection"]["LONG"]["trades"] == 2
    assert stats["byDirection"]["LONG"]["netPnl"] == pytest.approx(10)
    assert stats["byDirection"]["SHORT"]["trades"] == 3
    assert stats["byDirection"]["SHORT"]["netPnl"] == pytest.approx(5)
    assert len(stats["equityCurve"]) == 5
    assert stats["equityCurve"][-1] == {
        "timestamp": 50,
        "cumulativeNetPnl": pytest.approx(15),
        "equity": pytest.approx(95),
    }
    replay = client.get("/api/trade-logs/statistics?source=replay").json()
    assert replay["totalTrades"] == 1 and replay["netPnl"] == pytest.approx(1_000)


def test_single_delete_is_source_scoped_and_recalculates_statistics(history_client):
    client, local = history_client
    _save(local, _log("live-a", pnl=3), _log("live-b", closed_at=2, pnl=-1), _log("replay-a", source="replay", pnl=50))

    deleted = client.delete("/api/trade-logs/live-a?source=live")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "deletedCount": 1, "source": "live", "id": "live-a"}
    assert client.get("/api/trade-logs/statistics?source=live").json()["netPnl"] == pytest.approx(-1)
    assert [row["id"] for row in client.get("/api/trade-logs?source=replay").json()["items"]] == ["replay-a"]

    wrong_source = client.delete("/api/trade-logs/replay-a?source=live")
    assert wrong_source.status_code == 200
    assert wrong_source.json() == {"deleted": False, "deletedCount": 0, "source": "live", "id": "replay-a"}
    assert client.get("/api/trade-logs/statistics?source=replay").json()["totalTrades"] == 1


def test_single_replay_delete_removes_owning_session_in_the_same_transaction(history_client):
    client, local = history_client
    first = _log("replay-a", source="replay", closed_at=1, pnl=3)
    second = _log("replay-b", source="replay", closed_at=2, pnl=4)
    _save(local, first, second)
    _save_replay_session(local, "session-a", first)
    _save_replay_session(local, "session-b", second)

    deleted = client.delete("/api/trade-logs/replay-a?source=replay")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert local.get_replay_session("session-a") is None
    assert local.get_replay_session("session-b") is not None
    assert [row["id"] for row in local.trade_logs("replay")] == ["replay-b"]


def test_replay_log_and_session_delete_roll_back_together(history_client):
    _, local = history_client
    replay = _log("atomic-replay", source="replay", pnl=2)
    _save(local, replay)
    _save_replay_session(local, "atomic-session", replay)
    with local.session() as connection:
        connection.execute(
            "CREATE TRIGGER block_replay_session_delete BEFORE DELETE ON replay_sessions "
            "WHEN OLD.id='atomic-session' BEGIN SELECT RAISE(ABORT,'blocked'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="blocked"):
        local.delete_trade_log("replay", "atomic-replay")
    assert [row["id"] for row in local.trade_logs("replay")] == ["atomic-replay"]
    assert local.get_replay_session("atomic-session") is not None


def test_bulk_delete_deduplicates_ids_and_clear_requires_explicit_confirmation(history_client):
    client, local = history_client
    _save(
        local,
        _log("a", pnl=1),
        _log("b", closed_at=2, pnl=2),
        _log("c", closed_at=3, pnl=3),
        _log("r", source="replay", pnl=4),
    )
    bulk = client.post("/api/trade-logs/bulk-delete", json={"source": "live", "ids": ["a", "a", "missing", "b"]})
    assert bulk.status_code == 200
    assert bulk.json()["deletedCount"] == 2 and bulk.json()["source"] == "live"
    assert [row["id"] for row in client.get("/api/trade-logs?source=live").json()["items"]] == ["c"]

    assert client.delete("/api/trade-logs?source=live").status_code in {400, 422}
    assert client.delete("/api/trade-logs?source=live&confirmation=WRONG").status_code in {400, 422}
    cleared = client.delete("/api/trade-logs?source=live&confirmation=DELETE")
    assert cleared.status_code == 200
    assert cleared.json() == {"deletedCount": 1, "source": "live"}
    assert client.get("/api/trade-logs/statistics?source=live").json()["totalTrades"] == 0
    assert client.get("/api/trade-logs/statistics?source=replay").json()["totalTrades"] == 1


def test_bulk_and_clear_replay_deletes_remove_only_linked_replay_sessions(history_client):
    client, local = history_client
    replay_rows = [
        _log("replay-a", source="replay", closed_at=1, pnl=1),
        _log("replay-b", source="replay", closed_at=2, pnl=2),
        _log("replay-c", source="replay", closed_at=3, pnl=3),
    ]
    live = _log("live-kept", source="live", closed_at=4, pnl=4)
    _save(local, *replay_rows, live)
    for suffix, row in zip(("a", "b", "c"), replay_rows):
        _save_replay_session(local, f"session-{suffix}", row)
    _save_replay_session(local, "unfinished-session", None)
    active_plan = make_plan_record(TradePlanDraft(
        direction="LONG",
        initialEntryPrice=100,
        addPrice=90,
        stopPrice=80,
        takeProfitPrice=120,
        equity=80,
        leverage=10,
        initialMargin=4,
    ))
    local.save_trade_plan(active_plan, active=True)

    bulk = client.post(
        "/api/trade-logs/bulk-delete",
        json={"source": "replay", "ids": ["replay-a", "missing", "replay-b"]},
    )
    assert bulk.status_code == 200 and bulk.json()["deletedCount"] == 2
    assert local.get_replay_session("session-a") is None
    assert local.get_replay_session("session-b") is None
    assert local.get_replay_session("session-c") is not None
    assert local.get_replay_session("unfinished-session") is not None

    cleared = client.delete("/api/trade-logs?source=replay&confirmation=DELETE")
    assert cleared.status_code == 200 and cleared.json()["deletedCount"] == 1
    assert local.get_replay_session("session-c") is None
    assert local.get_replay_session("unfinished-session") is not None
    assert [row["id"] for row in local.trade_logs("live")] == ["live-kept"]
    assert local.get_trade_plan(active_plan["id"])["id"] == active_plan["id"]


def test_json_export_is_data_not_html_and_preserves_source_isolation(history_client):
    client, local = history_client
    payload = '</script><script>window.__injected=true</script>'
    _save(local, _log("json-live", notes=payload), _log("json-replay", source="replay", notes="replay"))

    response = client.get("/api/trade-logs/export?source=live&format=json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"
    body = response.json()
    assert body["source"] == "live"
    assert isinstance(body["exportedAt"], int)
    assert [row["id"] for row in body["items"]] == ["json-live"]
    assert body["items"][0]["notes"] == payload


def test_csv_export_has_attachment_header_omits_screenshot_path_and_neutralizes_formula(history_client):
    client, local = history_client
    formula = '=HYPERLINK("https://evil.example","click")'
    private_path = r"C:\Users\person\private-fill.png"
    _save(local, _log("csv-live", pnl=-3.25, fees=0.5, slippage=0.25, notes=formula, screenshot_path=private_path))

    response = client.get("/api/trade-logs/export?source=live&format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    disposition = response.headers.get("content-disposition", "")
    assert "attachment" in disposition.lower() and ".csv" in disposition.lower()
    assert "screenshotPath" not in response.text
    assert private_path not in response.text

    rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert len(rows) == 2
    formula_cells = [cell for row in rows for cell in row if "HYPERLINK" in cell]
    assert formula_cells
    assert all(cell.lstrip().startswith("'=") for cell in formula_cells)
    exported = next(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert exported["netPnl"] == "-3.25"
    assert not exported["netPnl"].startswith("'")
    assert float(exported["netPnl"]) == pytest.approx(-3.25)
