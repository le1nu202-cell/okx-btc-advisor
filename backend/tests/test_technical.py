import json
from datetime import datetime, timezone

from backend.models import Candle
from backend.technical import GROUP_CAPS, analyze_technical


def candles(n=260, tf="1H", trend=1.0, start=None, volume=100.0):
    step = 3_600_000 if tf == "1H" else 14_400_000
    start = start or int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return [Candle(timestamp=start+i*step, open=100+i*trend, high=101+i*trend, low=99+i*trend,
                   close=100.5+i*trend, volume=volume+i%11, timeframe=tf, confirm=True) for i in range(n)]


def test_output_is_json_friendly_and_groups_are_capped():
    result = analyze_technical(candles(), candles(tf="4H"))
    json.dumps(result, allow_nan=False)
    assert result["ready"]
    assert set(result["group_scores"]) == set(GROUP_CAPS)
    assert all(abs(result["group_scores"][key]) <= cap for key, cap in GROUP_CAPS.items())
    assert -100 <= result["technical_score"] <= 100
    assert result["rating_1h"]["label"] in {"强多", "偏多", "中性", "偏空", "强空"}


def test_uptrend_produces_positive_confluence_and_valid_levels():
    result = analyze_technical(candles(trend=1.0), candles(tf="4H", trend=2.0))
    assert result["technical_score"] > 0
    assert result["group_scores"]["trend"] > 0
    assert result["levels"]["donchian_support"] < result["levels"]["donchian_resistance"]
    profile = result["levels"]["volume_profile"]
    assert profile["val"] <= profile["poc"] <= profile["vah"]
    assert result["volume_price"]["daily_vwap"] is not None
    assert result["volume_price"]["weekly_vwap"] is not None


def test_downtrend_is_negative_and_momentum_is_finite():
    result = analyze_technical(candles(trend=-0.2), candles(tf="4H", trend=-0.2))
    assert result["technical_score"] < 0
    assert result["momentum"]["stoch_rsi"] is not None
    assert -100 <= result["momentum"]["williams_r"] <= 0
    assert 0 <= result["volume_price"]["mfi"] <= 100


def test_insufficient_data_blocks_entries():
    result = analyze_technical(candles(80), candles(80, tf="4H"))
    assert not result["ready"]
    assert result["risk_overlay"]["block_new_entries"]
    assert result["risk_overlay"]["position_scale"] == 0


def test_unconfirmed_duplicate_and_out_of_order_are_cleaned():
    one = candles()
    bogus = one[-1].model_copy(update={"close": 999_999, "confirm": False})
    shuffled = [bogus, *reversed(one), one[-1]]
    clean = analyze_technical(one, candles(tf="4H"))
    dirty = analyze_technical(shuffled, candles(tf="4H"))
    assert dirty["technical_score"] == clean["technical_score"]
    assert dirty["as_of"] == clean["as_of"]


def test_anchor_vwap_uses_current_utc_day_and_week():
    start = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    one = candles(260, start=start)
    result = analyze_technical(one, candles(260, tf="4H", start=start))
    last_day = datetime.fromtimestamp(one[-1].timestamp / 1000, timezone.utc).date()
    current_day = [c for c in one if datetime.fromtimestamp(c.timestamp / 1000, timezone.utc).date() == last_day]
    expected = sum(((c.high+c.low+c.close)/3)*c.volume for c in current_day) / sum(c.volume for c in current_day)
    assert abs(result["volume_price"]["daily_vwap"] - expected) < 1e-7
    assert result["volume_price"]["weekly_vwap"] <= result["volume_price"]["daily_vwap"]
