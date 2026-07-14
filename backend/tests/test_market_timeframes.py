import asyncio
import time

import pytest

from backend import main
from backend.db import Database
from backend.models import Candle
from backend.okx import CANDLE_TIMEFRAMES, CHANNEL_TO_TIMEFRAME, OKXPublicClient


EXPECTED_TIMEFRAMES = {
    "1m": {"channel": "candle1m", "durationMs": 60_000, "retentionMs": 7 * 86_400_000},
    "15m": {"channel": "candle15m", "durationMs": 15 * 60_000, "retentionMs": 90 * 86_400_000},
    "1H": {"channel": "candle1H", "durationMs": 60 * 60_000, "retentionMs": None},
    "4H": {"channel": "candle4H", "durationMs": 4 * 60 * 60_000, "retentionMs": None},
}


def _row(timestamp: int, *, confirm: str = "1", close: str = "101", high: str = "102", low: str = "99", volume: str = "10"):
    return [str(timestamp), "100", high, low, close, volume, "5", "0", confirm]


def _candle(timestamp: int, timeframe: str, *, confirm: bool = True) -> Candle:
    return Candle(
        timestamp=timestamp,
        open=100,
        high=102,
        low=99,
        close=101,
        volume=10,
        volume_ccy=5,
        timeframe=timeframe,
        confirm=confirm,
    )


def test_timeframe_duration_channel_and_retention_contract_is_exact():
    assert set(CANDLE_TIMEFRAMES) == set(EXPECTED_TIMEFRAMES)
    for timeframe, expected in EXPECTED_TIMEFRAMES.items():
        assert CANDLE_TIMEFRAMES[timeframe]["channel"] == expected["channel"]
        assert CANDLE_TIMEFRAMES[timeframe]["durationMs"] == expected["durationMs"]
        assert CANDLE_TIMEFRAMES[timeframe]["retentionMs"] == expected["retentionMs"]
    assert CHANNEL_TO_TIMEFRAME == {
        "candle1m": "1m",
        "candle15m": "15m",
        "candle1H": "1H",
        "candle4H": "4H",
    }


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1H", "4H"])
def test_parse_candles_preserves_timestamp_and_confirmation_by_real_duration(timeframe):
    now = 1_800_000_000_000
    duration = EXPECTED_TIMEFRAMES[timeframe]["durationMs"]
    closed_at = now - duration
    current_at = now
    # One millisecond into the future is still an admissible live timestamp,
    # but a confirmed candle at that timestamp cannot have closed yet.
    impossible_confirmed_at = now + 1
    parsed = OKXPublicClient.parse_candles(
        [
            _row(closed_at, confirm="1", close="100"),
            _row(current_at, confirm="0", close="101"),
            _row(impossible_confirmed_at, confirm="1", close="102"),
        ],
        timeframe,
        now_ms=now,
    )

    assert [(row.timestamp, row.confirm, row.timeframe) for row in parsed] == [
        (closed_at, True, timeframe),
        (current_at, False, timeframe),
    ]


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1H", "4H"])
def test_duplicate_confirmation_upgrade_is_irreversible_for_every_timeframe(timeframe):
    now = int(time.time() * 1000)
    duration = EXPECTED_TIMEFRAMES[timeframe]["durationMs"]
    timestamp = now - 2 * duration
    live = _row(timestamp, confirm="0", close="106", high="110", low="95", volume="100")
    confirmed = _row(timestamp, confirm="1", close="105", high="108", low="96", volume="85")
    delayed_live = _row(timestamp, confirm="0", close="109", high="112", low="90", volume="120")
    parsed = OKXPublicClient.parse_candles([live, confirmed, delayed_live], timeframe, now_ms=now)

    assert len(parsed) == 1
    assert parsed[0].confirm is True
    assert parsed[0].close == 105
    assert parsed[0].volume == 85


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1H", "4H"])
def test_database_duplicate_upgrade_never_downgrades_confirmed(timeframe, tmp_path):
    db = Database(tmp_path / f"{timeframe}.db")
    timestamp = 1_700_000_000_000
    live = _candle(timestamp, timeframe, confirm=False)
    confirmed = live.model_copy(update={"confirm": True, "close": 100.5, "volume": 11})
    delayed = live.model_copy(update={"confirm": False, "close": 101.5, "volume": 20})
    db.upsert_candles("BTC-USDT-SWAP", [live, confirmed, delayed])
    assert db.candles("BTC-USDT-SWAP", timeframe) == [confirmed]


def test_unknown_timeframe_and_unknown_websocket_channel_are_rejected():
    with pytest.raises(ValueError, match="unsupported candle timeframe"):
        OKXPublicClient.parse_candles([], "5m")
    client = OKXPublicClient()
    assert client._valid_stream_message({
        "arg": {"channel": "candle5m"},
        "data": [_row(int(time.time() * 1000) - 300_000)],
    }) is False


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1H", "4H"])
def test_known_websocket_candle_channels_validate_with_their_own_duration(timeframe):
    duration = EXPECTED_TIMEFRAMES[timeframe]["durationMs"]
    client = OKXPublicClient()
    message = {
        "arg": {"channel": EXPECTED_TIMEFRAMES[timeframe]["channel"]},
        "data": [_row(int(time.time() * 1000) - 2 * duration)],
    }
    assert client._valid_stream_message(message) is True


@pytest.mark.asyncio
async def test_stream_subscribes_to_exactly_four_candle_channels(monkeypatch):
    client = OKXPublicClient()
    calls = []

    async def endpoint(url, args, name, on_message, stop):
        calls.append((url, args, name))

    monkeypatch.setattr(client, "_stream_endpoint", endpoint)
    await client.stream(lambda _: None, asyncio.Event())
    public_call = next(row for row in calls if row[2] == "public")
    candle_call = next(row for row in calls if row[2] == "candles")
    assert public_call[0] == client.ws_url
    assert public_call[1] == [
        {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
        {"channel": "funding-rate", "instId": "BTC-USDT-SWAP"},
        {"channel": "open-interest", "instId": "BTC-USDT-SWAP"},
        {"channel": "mark-price", "instId": "BTC-USDT-SWAP"},
    ]
    assert candle_call[0] == client.business_ws_url
    assert candle_call[1] == [
        {"channel": "candle1m", "instId": "BTC-USDT-SWAP"},
        {"channel": "candle15m", "instId": "BTC-USDT-SWAP"},
        {"channel": "candle1H", "instId": "BTC-USDT-SWAP"},
        {"channel": "candle4H", "instId": "BTC-USDT-SWAP"},
    ]


def test_retention_cutoff_can_never_delete_1h_or_4h_even_if_caller_passes_it(tmp_path):
    db = Database(tmp_path / "retention.db")
    cutoff = 1_800_000_000_000
    old = cutoff - 1
    recent = cutoff + 1
    db.upsert_candles(
        "BTC-USDT-SWAP",
        [
            _candle(timestamp, timeframe)
            for timeframe in EXPECTED_TIMEFRAMES
            for timestamp in (old, recent)
        ],
        retention_before=cutoff,
    )

    assert [row.timestamp for row in db.candles("BTC-USDT-SWAP", "1m")] == [recent]
    assert [row.timestamp for row in db.candles("BTC-USDT-SWAP", "15m")] == [recent]
    assert [row.timestamp for row in db.candles("BTC-USDT-SWAP", "1H")] == [old, recent]
    assert [row.timestamp for row in db.candles("BTC-USDT-SWAP", "4H")] == [old, recent]


def test_snapshot_exposes_independent_availability_staleness_timestamp_and_gap_for_four_series(tmp_path, monkeypatch):
    now = 1_800_000_000_000
    db = Database(tmp_path / "snapshot.db")
    rows = [
        _candle(now - 60_000, "1m"),
        _candle(now - 4 * 15 * 60_000, "15m"),
        _candle(now - 3_600_000, "1H"),
        _candle(now - 4 * 3_600_000, "4H"),
    ]
    db.upsert_candles(main.INSTRUMENT, rows)
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main.time, "time", lambda: now / 1000)
    monkeypatch.setattr(main, "_live_market_ready", lambda now_ms=None: (True, ""))
    monkeypatch.setitem(main.runtime, "price", 101.0)
    monkeypatch.setitem(main.runtime, "ticker_ts", now)
    monkeypatch.setitem(main.runtime, "mark_price", 100.5)
    monkeypatch.setitem(main.runtime, "mark_price_ts", now - 1_000)
    monkeypatch.setitem(main.runtime, "connection", "connected")
    monkeypatch.setitem(main.runtime, "candle_gaps", {"1m": True, "15m": False, "1H": False, "4H": False})

    body = main.snapshot().model_dump(by_alias=True, mode="json")
    assert set(body["candleStatus"]) == {"1m", "15m", "1H", "4H"}
    assert body["candleStatus"]["1m"] == {
        "available": True,
        "stale": False,
        "lastAt": now - 60_000,
        "gapDetected": True,
    }
    assert body["candleStatus"]["15m"]["available"] is True
    assert body["candleStatus"]["15m"]["stale"] is True
    assert body["candleStatus"]["1H"]["lastAt"] == now - 3_600_000
    assert body["candleStatus"]["4H"]["lastAt"] == now - 4 * 3_600_000
    assert body["markPrice"] == 100.5 and body["markPriceTime"] == now - 1_000
    assert [row["timestamp"] for row in body["candles1M"]] == [now - 60_000]
    assert [row["timestamp"] for row in body["candles15M"]] == [now - 4 * 15 * 60_000]


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1H", "4H"])
def test_confirmed_continuity_keeps_each_timeframe_gapped_until_missing_timestamp_is_persisted(timeframe, tmp_path, monkeypatch):
    step = EXPECTED_TIMEFRAMES[timeframe]["durationMs"]
    start = int(time.time() * 1000) - 20 * step
    local = Database(tmp_path / f"gap-{timeframe}.db")
    incomplete = [_candle(start, timeframe), _candle(start + step, timeframe), _candle(start + 3 * step, timeframe)]
    local.upsert_candles(main.INSTRUMENT, incomplete)
    monkeypatch.setattr(main, "db", local)
    monkeypatch.setitem(main.runtime, "candle_gaps", {tf: False for tf in EXPECTED_TIMEFRAMES})
    monkeypatch.setitem(main.runtime, "candle_gap_targets", {tf: None for tf in EXPECTED_TIMEFRAMES})

    assert main._refresh_candle_gap_status(timeframe, incomplete) is True
    assert main.runtime["candle_gap_targets"][timeframe] == (start + step, start + 3 * step)

    repaired = _candle(start + 2 * step, timeframe)
    local.upsert_candles(main.INSTRUMENT, [repaired])
    assert main._refresh_candle_gap_status(timeframe, [repaired]) is False
    assert main.runtime["candle_gap_targets"][timeframe] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("timeframe", ["1m", "15m", "1H", "4H"])
async def test_rest_repair_that_still_misses_one_candle_does_not_clear_gap(timeframe, tmp_path, monkeypatch):
    step = EXPECTED_TIMEFRAMES[timeframe]["durationMs"]
    start = int(time.time() * 1000) - 20 * step
    local = Database(tmp_path / f"reconcile-gap-{timeframe}.db")
    local.upsert_candles(main.INSTRUMENT, [_candle(start, timeframe)])
    monkeypatch.setattr(main, "db", local)
    monkeypatch.setitem(main.runtime, "candle_gaps", {tf: False for tf in EXPECTED_TIMEFRAMES})
    monkeypatch.setitem(main.runtime, "candle_gap_targets", {tf: None for tf in EXPECTED_TIMEFRAMES})
    phase = {"complete": False}

    async def public_rows(instrument, requested_timeframe, limit, history):
        if requested_timeframe != timeframe:
            return []
        if history:
            # REST repair covers the first missing row but still omits the next.
            return [_candle(start + step, timeframe), _candle(start + 3 * step, timeframe)]
        if phase["complete"]:
            return [_candle(start + 2 * step, timeframe), _candle(start + 3 * step, timeframe)]
        return [_candle(start + 3 * step, timeframe)]

    async def no_broadcast(message):
        return None

    async def no_signal():
        return False

    monkeypatch.setattr(main.client, "candles", public_rows)
    monkeypatch.setattr(main, "broadcast", no_broadcast)
    monkeypatch.setattr(main, "publish_current_signal", no_signal)

    assert await main.reconcile_market_once() is True
    assert main.runtime["candle_gaps"][timeframe] is True
    assert [row.timestamp for row in local.candles(main.INSTRUMENT, timeframe)] == [start, start + step, start + 3 * step]

    phase["complete"] = True
    assert await main.reconcile_market_once() is True
    assert main.runtime["candle_gaps"][timeframe] is False
    assert [row.timestamp for row in local.candles(main.INSTRUMENT, timeframe)] == [start + i * step for i in range(4)]


def test_legacy_strategy_reads_only_1h_and_4h(monkeypatch):
    calls = []

    class FakeDatabase:
        def candles(self, instrument, timeframe, limit, confirmed_only):
            calls.append((timeframe, limit, confirmed_only))
            return []

        def funding_rows(self, instrument, since):
            return []

        def get_settings(self):
            return type("Settings", (), {"custom_parameters": {}})()

    sentinel = object()
    monkeypatch.setattr(main, "db", FakeDatabase())
    monkeypatch.setattr(main, "analyze", lambda *args, **kwargs: sentinel)
    monkeypatch.setattr(main, "_apply_live_market_gate", lambda advice, now_ms=None: advice)

    assert main.current_advice() is sentinel
    assert calls == [("1H", 400, True), ("4H", 260, True)]
