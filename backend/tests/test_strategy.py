import time
import numpy as np
import pytest

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


def test_open_interest_change_is_an_explainable_zero_score_trend_marker():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    a=analyze(c1,c4,oi={"changePercent":.025},now_ms=c1[-1].timestamp+3600_000)
    marker=next(item for item in a.contributions if item.name=="24H持仓量确认")
    assert marker.score==0 and marker.value==pytest.approx(.025)
    assert a.data_quality.open_interest_available is True


def test_signal_close_and_no_future_funding():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    future=(c1[-1].timestamp+3600_000+1000,.5)
    a=analyze(c1,c4,[future],now_ms=c1[-1].timestamp+3600_000)
    assert a.candle_close_at==c1[-1].timestamp+3600_000
    assert not a.data_quality.funding_available


def test_funding_percentile_requires_a_real_ninety_day_sample():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    decision=c1[-1].timestamp+3600_000
    short=[(decision-i*8*3600_000,.001) for i in range(20)]
    incomplete=analyze(c1,c4,short,now_ms=decision)
    assert incomplete.data_quality.funding_available is False
    assert any("90天资金费率样本不足" in warning for warning in incomplete.data_quality.warnings)
    complete=[(decision-i*9*3600_000,.001) for i in range(220)]
    assert analyze(c1,c4,complete,now_ms=decision).data_quality.funding_available is True
    stale=[(decision-13*3600_000-i*9*3600_000,.001) for i in range(220)]
    assert analyze(c1,c4,stale,now_ms=decision).data_quality.funding_available is False


def test_future_candle_and_oi_context_are_ignored():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    decision=c1[-1].timestamp+3600_000
    future=c1[-1].model_copy(update={"timestamp":c1[-1].timestamp+3600_000})
    advice=analyze([*c1,future],c4,oi={"changePercent":.02,"latestTime":decision+1},now_ms=decision)
    assert advice.candle_close_at==decision
    assert advice.data_quality.open_interest_available is False
    assert not any(item.name=="24H持仓量确认" for item in advice.contributions)
    assert any("决策时点之后" in warning for warning in advice.data_quality.warnings)


def test_stale_uses_candle_close():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000+2*3600_000+1)
    assert a.regime==MarketRegime.STALE


def test_stale_4h_regime_blocks_fresh_1h_signal():
    c1=candles(250,start=1_600_000_000_000+750*3600_000)
    c4=candles(249,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert a.action==AdviceAction.WAIT and a.regime==MarketRegime.STALE
    assert any("4H市场状态已过期" in warning for warning in a.data_quality.warnings)


@pytest.mark.parametrize("timeframe,index",[("1H",-30),("4H",-30)])
def test_recent_candle_gap_blocks_new_advice(timeframe,index):
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    if timeframe=="1H":c1.pop(index)
    else:c4.pop(index)
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert a.action==AdviceAction.WAIT and a.strategy=="data_gap"
    assert a.regime==MarketRegime.STALE and a.data_quality.fresh is False
    assert any(f"{timeframe} K线缺口" in warning for warning in a.data_quality.warnings)


def test_risk_bounds_and_calculation():
    a=SignalAdvice(strategy="trend",candle_close_at=1,regime=MarketRegime.TREND,action=AdviceAction.LONG_CANDIDATE,direction_score=60,confidence=70,trigger_price=100,stop_loss=95,invalidation="x",explanation="x",data_quality=DataQuality(fresh=True))
    r=estimate_risk(a,Settings(equity=10000,risk_percent=1,leverage=2))
    assert r.stop_distance==5 and r.quantity_btc==20 and r.reference_notional==2000


def test_risk_estimate_distinguishes_missing_settings_from_wait_signal():
    wait=SignalAdvice(strategy="regime",candle_close_at=1,regime=MarketRegime.TRANSITION,action=AdviceAction.WAIT,direction_score=0,confidence=20,invalidation="x",explanation="x",data_quality=DataQuality(fresh=True))
    configured=estimate_risk(wait,Settings(equity=1000,risk_percent=1,leverage=1))
    assert any("没有有效触发价" in warning for warning in configured.warnings)
    assert not any("请填写权益" in warning for warning in configured.warnings)
    missing=estimate_risk(wait,Settings())
    assert any("请填写权益" in warning for warning in missing.warnings)


def test_risk_estimate_degrades_safely_when_finite_inputs_overflow():
    a=SignalAdvice(strategy="trend",candle_close_at=1,regime=MarketRegime.TREND,action=AdviceAction.LONG_CANDIDATE,direction_score=60,confidence=70,trigger_price=100,stop_loss=95,invalidation="x",explanation="x",data_quality=DataQuality(fresh=True))
    r=estimate_risk(a,Settings(equity=1e308,risk_percent=2,leverage=2))
    assert r.reference_notional is None and r.quantity_btc is None
    assert any("数值过大" in warning for warning in r.warnings)


def test_news_adjustment_is_capped_and_separate():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000,news_analysis={"status":"fresh","score":99,"items":[]})
    assert -85<=a.technical_score<=85
    assert a.news_score==15
    assert a.direction_score==min(100,a.technical_score+a.news_score)
    assert a.config_version=="research-v2-unvalidated"


def test_unavailable_nonfinite_and_future_news_are_strictly_neutral():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    decision=c1[-1].timestamp+3600_000
    baseline=analyze(c1,c4,now_ms=decision)
    unavailable=analyze(c1,c4,now_ms=decision,news_analysis={"status":"unavailable","score":15})
    nonfinite=analyze(c1,c4,now_ms=decision,news_analysis={"status":"fresh","score":float("nan")})
    future=analyze(c1,c4,now_ms=decision,news_analysis={"status":"fresh","score":15,"asOf":decision+1})
    for advice in (unavailable,nonfinite,future):
        assert advice.news_score==0
        assert advice.direction_score==baseline.direction_score
    assert any("晚于本次决策" in warning for warning in future.data_quality.warnings)


def test_news_cannot_reverse_the_technical_direction():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    decision=c1[-1].timestamp+3600_000
    advice=analyze(c1,c4,now_ms=decision,news_analysis={"status":"fresh","score":-15,"asOf":decision})
    assert advice.technical_score>0
    assert advice.direction_score>=0


def test_zero_source_coverage_neutralizes_otherwise_fresh_score():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    decision=c1[-1].timestamp+3600_000
    advice=analyze(c1,c4,now_ms=decision,news_analysis={"status":"partial","score":15,"sourceCoverage":0,"asOf":decision})
    assert advice.news_score==0


def test_news_cannot_create_signal_without_technical_setup():
    c1=candles(219);c4=candles(199,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000,news_analysis={"status":"fresh","score":15,"items":[]})
    assert a.action==AdviceAction.WAIT and a.news_score==0


def test_invalid_candle_blocks_signal():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    c1[-1]=c1[-1].model_copy(update={"high":90.0})
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert a.action==AdviceAction.WAIT and a.strategy=="invalid_data" and a.confidence==0
    assert any("非法OHLC" in x for x in a.data_quality.warnings)


def test_negative_volume_and_nan_block_signal():
    for update in ({"volume":-1.0},{"close":float("nan")}):
        c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
        c1[-1]=c1[-1].model_copy(update=update)
        assert analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000).action==AdviceAction.WAIT


def test_invalid_future_4h_does_not_block_current_decision():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    clean=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    future=c4[-1].model_copy(update={"timestamp":c1[-1].timestamp+3600_000,"high":1.0})
    dirty=analyze(c1,[*c4,future],now_ms=c1[-1].timestamp+3600_000)
    assert dirty.action==clean.action and dirty.technical_score==clean.technical_score


def test_invalid_future_1h_does_not_change_current_decision():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    decision=c1[-1].timestamp+3600_000
    clean=analyze(c1,c4,now_ms=decision)
    future=c1[-1].model_copy(update={"timestamp":c1[-1].timestamp+3600_000,"high":1.0})
    dirty=analyze([*c1,future],c4,now_ms=decision)
    assert dirty.action==clean.action and dirty.technical_score==clean.technical_score
    assert any("决策时点之后" in warning for warning in dirty.data_quality.warnings)


def test_contributions_equal_published_technical_score():
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert sum(x.score for x in a.contributions)==a.technical_score


def test_wait_confidence_stays_low(monkeypatch):
    monkeypatch.setattr("backend.strategy.classify_regime",lambda _: (MarketRegime.RANGE,{"adx":10}))
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    a=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert a.action==AdviceAction.WAIT and a.confidence<=25


def test_range_targets_are_bollinger_midline_and_opposite_band(monkeypatch):
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H")
    entry=c1[-1].close
    monkeypatch.setattr("backend.strategy.classify_regime",lambda _: (MarketRegime.RANGE,{"adx":10}))
    def bands(values):
        upper=np.full(len(values),entry+20.0);mid=np.full(len(values),entry+10.0);lower=np.full(len(values),entry-1.0)
        lower[-2]=values[-2]+1.0
        return upper,mid,lower
    def rsi_values(values):
        result=np.full(len(values),50.0);result[-2:]=[30.0,40.0];return result
    monkeypatch.setattr("backend.strategy.bollinger",bands)
    monkeypatch.setattr("backend.strategy.rsi",rsi_values)
    monkeypatch.setattr("backend.strategy.stochastic_rsi",lambda values:(np.full(len(values),40.0),np.full(len(values),30.0)))
    monkeypatch.setattr("backend.strategy.money_flow_index",lambda *args:np.full(len(args[0]),55.0))
    monkeypatch.setattr("backend.strategy.atr",lambda *args:np.full(len(args[0]),2.0))
    advice=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert advice.strategy=="range" and advice.action==AdviceAction.LONG_CANDIDATE
    assert advice.targets==pytest.approx([entry+10,entry+20])
    expected_r=[(target-entry)/(entry-advice.stop_loss) for target in advice.targets]
    assert advice.risk_reward==pytest.approx(expected_r)


@pytest.mark.parametrize("side",["long","short"])
def test_range_reentry_that_crosses_midline_is_not_a_candidate(monkeypatch,side):
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H");entry=c1[-1].close
    monkeypatch.setattr("backend.strategy.classify_regime",lambda _: (MarketRegime.RANGE,{"adx":10}))
    def bands(values):
        upper=np.full(len(values),entry+20.0);mid=np.full(len(values),entry-10.0 if side=="long" else entry+10.0);lower=np.full(len(values),entry-20.0)
        if side=="long":lower[-2]=values[-2]+1.0
        else:upper[-2]=values[-2]-1.0
        return upper,mid,lower
    def rsi_values(values):
        result=np.full(len(values),50.0);result[-2:]=[30.0,40.0] if side=="long" else [70.0,60.0];return result
    monkeypatch.setattr("backend.strategy.bollinger",bands)
    monkeypatch.setattr("backend.strategy.rsi",rsi_values)
    monkeypatch.setattr("backend.strategy.stochastic_rsi",lambda values:(np.full(len(values),40.0),np.full(len(values),30.0)))
    monkeypatch.setattr("backend.strategy.money_flow_index",lambda *args:np.full(len(args[0]),50.0))
    advice=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert advice.action==AdviceAction.WAIT and advice.targets==[]


def test_invalid_bollinger_band_order_is_not_a_candidate(monkeypatch):
    c1=candles(250,start=1_600_000_000_000+750*3600_000);c4=candles(250,"4H");entry=c1[-1].close
    monkeypatch.setattr("backend.strategy.classify_regime",lambda _: (MarketRegime.RANGE,{"adx":10}))
    def broken_bands(values):
        upper=np.full(len(values),entry+20.0);mid=np.full(len(values),entry-30.0);lower=np.full(len(values),entry-20.0);lower[-2]=values[-2]+1.0
        return upper,mid,lower
    def rsi_values(values):result=np.full(len(values),50.0);result[-2:]=[30.0,40.0];return result
    monkeypatch.setattr("backend.strategy.bollinger",broken_bands)
    monkeypatch.setattr("backend.strategy.rsi",rsi_values)
    advice=analyze(c1,c4,now_ms=c1[-1].timestamp+3600_000)
    assert advice.action==AdviceAction.WAIT
