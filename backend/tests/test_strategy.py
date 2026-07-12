import time

from backend.models import AdviceAction,Candle,MarketRegime,Settings,SignalAdvice,DataQuality
from backend.strategy import analyze, classify_regime, estimate_risk


def candles(n,tf="1H",trend=1.0,start=1_600_000_000_000):
    step=3600_000 if tf=="1H" else 4*3600_000
    return [Candle(timestamp=start+i*step,open=100+i*trend,high=101+i*trend,low=99+i*trend,close=100.5+i*trend,volume=100+i%7,timeframe=tf,confirm=True) for i in range(n)]


def test_warmup_requires_ema200():
    c1=candles(219);c4=candles(199,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert a.action==AdviceAction.WAIT and a.strategy=="insufficient"


def test_regime_threshold_trending():
    regime,values=classify_regime(candles(250,"4H"))
    assert regime==MarketRegime.TREND and values["adx"]>=25


def test_signal_close_and_no_future_funding():
    c1=candles(250,start=1_600_000_000_000+900*3600_000);c4=candles(250,"4H")
    future=(c1[-1].timestamp+3600_000+1000,.5)
    a=analyze(c1,c4,[future],now_ms=c1[-1].timestamp+3600_000)
    assert a.candle_close_at==c1[-1].timestamp+3600_000
    assert not a.data_quality.funding_available


def test_stale_uses_candle_close():
    c1=candles(250,start=1_600_000_000_000+900*3600_000);c4=candles(250,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000+2*3600_000+1)
    assert a.regime==MarketRegime.STALE


def test_risk_bounds_and_calculation():
    a=SignalAdvice(strategy="trend",candle_close_at=1,regime=MarketRegime.TREND,action=AdviceAction.LONG_CANDIDATE,direction_score=60,confidence=70,trigger_price=100,stop_loss=95,invalidation="x",explanation="x",data_quality=DataQuality(fresh=True))
    r=estimate_risk(a,Settings(equity=10000,risk_percent=1,leverage=2))
    assert r.stop_distance==5 and r.quantity_btc==20 and r.reference_notional==2000


def test_news_adjustment_is_capped_and_separate():
    c1=candles(250,start=1_600_000_000_000+900*3600_000);c4=candles(250,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000,news_analysis={"status":"fresh","score":99,"items":[]})
    assert -85<=a.technical_score<=85
    assert a.news_score==15
    assert a.direction_score==min(100,a.technical_score+a.news_score)
    assert a.config_version=="research-v2-unvalidated"


def test_news_cannot_create_signal_without_technical_setup():
    c1=candles(219);c4=candles(199,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000,news_analysis={"status":"fresh","score":15,"items":[]})
    assert a.action==AdviceAction.WAIT and a.news_score==0


def test_invalid_candle_blocks_signal():
    c1=candles(250,start=1_600_000_000_000+900*3600_000);c4=candles(250,"4H")
    c1[-1]=c1[-1].model_copy(update={"high":90.0})
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert a.action==AdviceAction.WAIT and a.strategy=="invalid_data" and a.confidence==0
    assert any("非法OHLC" in x for x in a.data_quality.warnings)


def test_negative_volume_and_nan_block_signal():
    for update in ({"volume":-1.0},{"close":float("nan")}):
        c1=candles(250,start=1_600_000_000_000+900*3600_000);c4=candles(250,"4H")
        c1[-1]=c1[-1].model_copy(update=update)
        assert analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000).action==AdviceAction.WAIT


def test_invalid_future_4h_does_not_block_current_decision():
    c1=candles(250,start=1_600_000_000_000+900*3600_000);c4=candles(250,"4H")
    clean=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    future=c4[-1].model_copy(update={"timestamp":c1[-1].timestamp+3600_000,"high":1.0})
    dirty=analyze(c1,[*c4,future],now_ms=c1[-1].timestamp+3600_000)
    assert dirty.action==clean.action and dirty.technical_score==clean.technical_score


def test_contributions_equal_published_technical_score():
    c1=candles(250,start=1_600_000_000_000+900*3600_000);c4=candles(250,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert sum(x.score for x in a.contributions)==a.technical_score


def test_wait_confidence_stays_low(monkeypatch):
    monkeypatch.setattr("backend.strategy.classify_regime",lambda _: (MarketRegime.RANGE,{"adx":10}))
    c1=candles(250,start=1_600_000_000_000+900*3600_000);c4=candles(250,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert a.action==AdviceAction.WAIT and a.confidence<=25
