import pytest

from backend import main
from backend.okx import OKXError, OKXPublicClient


EXPECTED_PUBLIC_PATHS = {
    "/api/v5/market/history-candles",
    "/api/v5/market/candles",
    "/api/v5/market/ticker",
    "/api/v5/public/funding-rate-history",
    "/api/v5/public/open-interest",
    "/api/v5/public/market-data-history",
    "/api/v5/public/instruments",
    "/api/v5/public/position-tiers",
    "/api/v5/public/mark-price",
}


def _instrument_row(**changes):
    row = {
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
        "instFamily": "BTC-USDT",
        "ctType": "linear",
        "ctValCcy": "BTC",
        "ctVal": "0.01",
        "lotSz": "1",
        "tickSz": "0.1",
    }
    row.update(changes)
    return row


def _tier_row(**changes):
    row = {
        "instFamily": "BTC-USDT",
        "tier": "1",
        "minSz": "0",
        "maxSz": "1000",
        "mmr": "0.005",
        "imr": "0.01",
        "maxLever": "100",
    }
    row.update(changes)
    return row


@pytest.mark.asyncio
async def test_rest_allowlist_is_exact_and_rejects_private_or_absolute_paths_before_network():
    client = OKXPublicClient()
    assert set(client.PUBLIC_REST_PATHS) == EXPECTED_PUBLIC_PATHS
    for path in (
        "/api/v5/account/positions",
        "/api/v5/trade/order",
        "/api/v5/asset/balances",
        "https://evil.example/api/v5/public/instruments",
        "//evil.example/api/v5/public/instruments",
        "/api/v5/public/instruments?instType=SWAP",
        "/api/v5/public/../account/positions",
    ):
        with pytest.raises(OKXError, match="public read-only allowlist"):
            await client._get(path)


@pytest.mark.asyncio
async def test_public_risk_methods_use_fixed_paths_and_cross_tier_parameters(monkeypatch):
    client = OKXPublicClient()
    calls = []

    async def fake_get(path, params=None):
        calls.append((path, params))
        if path.endswith("/instruments"):
            return [_instrument_row()]
        if path.endswith("/position-tiers"):
            return [_tier_row()]
        if path.endswith("/mark-price"):
            return [{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "markPx": "100.5", "ts": "1700000000000"}]
        raise AssertionError(path)

    monkeypatch.setattr(client, "_get", fake_get)
    metadata = await client.instrument_metadata()
    tiers = await client.position_tiers()
    mark = await client.mark_price()

    assert metadata == {
        "instrument": "BTC-USDT-SWAP",
        "instrumentFamily": "BTC-USDT",
        "contractValueBtc": 0.01,
        "lotSizeContracts": 1.0,
        "tickSize": 0.1,
    }
    assert tiers == [{
        "tier": 1,
        "minContracts": 0.0,
        "maxContracts": 1000.0,
        "maintenanceMarginRate": 0.005,
        "initialMarginRate": 0.01,
        "maxLeverage": 100.0,
    }]
    assert mark == (100.5, 1_700_000_000_000)
    assert calls == [
        ("/api/v5/public/instruments", {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}),
        ("/api/v5/public/position-tiers", {"instType": "SWAP", "tdMode": "cross", "instFamily": "BTC-USDT"}),
        ("/api/v5/public/mark-price", {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}),
    ]
    with pytest.raises(ValueError, match="cross"):
        await client.position_tiers(td_mode="isolated")
    assert len(calls) == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"instId": "ETH-USDT-SWAP"},
        {"instType": "FUTURES"},
        {"instFamily": "ETH-USDT"},
        {"ctType": "inverse"},
        {"ctValCcy": "USDT"},
        {"ctVal": "0"},
        {"tickSz": "nan"},
    ],
)
def test_instrument_metadata_requires_exact_linear_btc_contract(changes):
    with pytest.raises(OKXError):
        OKXPublicClient.parse_instrument_metadata(_instrument_row(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"instFamily": "ETH-USDT"},
        {"tier": "0"},
        {"minSz": "10", "maxSz": "10"},
        {"mmr": "-0.1"},
        {"imr": "0.001", "mmr": "0.005"},
        {"maxLever": "0"},
    ],
)
def test_public_position_tiers_reject_wrong_family_or_invalid_ranges(changes):
    with pytest.raises(OKXError):
        OKXPublicClient.parse_position_tiers([_tier_row(**changes)])


def test_public_risk_cache_is_fresh_for_six_hours_and_never_used_after_seven_days(monkeypatch):
    updated = 1_800_000_000_000
    payload = {
        "contractValueBtc": 0.01,
        "tiers": [_tier_row()],
        "updatedAt": updated,
    }

    class CacheDatabase:
        def get_public_market_cache(self, key):
            assert key == "okx-public-risk:BTC-USDT-SWAP:cross"
            return dict(payload)

    monkeypatch.setattr(main, "db", CacheDatabase())
    at_six_hours = main._public_risk_context(updated + 6 * 3_600_000)
    after_six_hours = main._public_risk_context(updated + 6 * 3_600_000 + 1)
    at_seven_days = main._public_risk_context(updated + 7 * 86_400_000)
    after_seven_days = main._public_risk_context(updated + 7 * 86_400_000 + 1)

    assert at_six_hours is not None and at_six_hours["stale"] is False
    assert after_six_hours is not None and after_six_hours["stale"] is True
    assert at_seven_days is not None and at_seven_days["stale"] is True
    assert after_seven_days is None
