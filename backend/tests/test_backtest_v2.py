from types import SimpleNamespace

import pytest

from backend.backtest import HOUR, _annualized_ratios, _clean, _scenario, run_backtest
from backend.models import AdviceAction, Candle, MarketRegime


def candle(ts, confirm=True):
    return Candle(timestamp=ts,open=100,high=102,low=99,close=101,volume=10,timeframe="1H",confirm=confirm)


def test_backtest_cleaning_is_ordered_deduplicated_and_confirmed():
    rows=[candle(7_200_000),candle(0),candle(3_600_000,False),candle(0)]
    clean,gaps=_clean(rows,"1H")
    assert [x.timestamp for x in clean]==[0,7_200_000]
    assert gaps==1


def test_cost_scenarios_compound_and_get_worse_with_slippage():
    trades=[{"grossReturn":.10,"fundingCost":0},{"grossReturn":.10,"fundingCost":0}]
    no_cost=_scenario(trades,0,0)
    stressed=_scenario(trades,5,20)
    assert no_cost["netReturn"]==pytest.approx(.21)
    assert stressed["netReturn"]<no_cost["netReturn"]
    assert stressed["maxDrawdown"]==0


def test_scenario_drawdown_and_profit_factor():
    out=_scenario([{"grossReturn":.1,"fundingCost":0},{"grossReturn":-.2,"fundingCost":0}],0,0)
    assert out["maxDrawdown"]==pytest.approx(.2)
    assert out["profitFactor"]==pytest.approx(.5)


def test_bankruptcy_is_not_artificially_revived_and_sortino_counts_equal_losses():
    out=_scenario([{"grossReturn":-1.2,"fundingCost":0},{"grossReturn":.5,"fundingCost":0}],0,0)
    assert out["netReturn"]==-1 and out["maxDrawdown"]==1
    _,sortino=_annualized_ratios(__import__("numpy").asarray([0.0,-.1,-.1]))
    assert sortino<0


def test_evaluation_window_uses_next_open_has_no_future_data_and_stops_first(monkeypatch):
    one=[]
    for index in range(400):
        entry=index==300
        one.append(Candle(
            timestamp=index*HOUR,
            open=110 if entry else 100,
            high=131 if entry else 101,
            low=99 if entry else 99.5,
            close=100,
            volume=10,
            timeframe="1H",
            confirm=True,
        ))
    four=[Candle(timestamp=index*4*HOUR,open=100,high=101,low=99,close=100,volume=40,timeframe="4H",confirm=True) for index in range(100)]
    decisions=[]

    def fake_analyze(one_hour,four_hour,funding,now_ms):
        decisions.append((one_hour[-1].timestamp,now_ms,max((row.timestamp for row in one_hour),default=-1),
                          max((row.timestamp for row in four_hour),default=-1)))
        return SimpleNamespace(
            action=AdviceAction.LONG_CANDIDATE if one_hour[-1].timestamp==299*HOUR else AdviceAction.WAIT,
            strategy="trend",
            stop_loss=100,
            targets=[115,130],
            regime=MarketRegime.TREND,
        )

    monkeypatch.setattr("backend.backtest.analyze",fake_analyze)
    result=run_backtest(
        one,
        four,
        [],
        strategy="combined",
        fee_bps=0,
        slippage_bps=0,
        evaluation_start_ms=300*HOUR,
        evaluation_end_ms=302*HOUR,
        strict_validation=False,
    )

    assert result["trades"]==1
    # The entry bar touched both the 100 stop and 130 target. Conservative
    # ordering must use the stop, and the return must use its 110 next-open.
    assert result["netReturn"]==pytest.approx((100-110)/110)
    assert result["equityCurve"][-1]["timestamp"]<=302*HOUR
    assert decisions[0]==(299*HOUR,300*HOUR,299*HOUR,296*HOUR)


def _flat_history(length=400):
    one=[Candle(timestamp=i*HOUR,open=100,high=101,low=99,close=100,volume=10,timeframe="1H",confirm=True)
         for i in range(length)]
    four=[Candle(timestamp=i*4*HOUR,open=100,high=101,low=99,close=100,volume=40,timeframe="4H",confirm=True)
          for i in range(max(100,length//4))]
    return one,four


def _advice(action=AdviceAction.LONG_CANDIDATE,stop=90,target=120):
    return SimpleNamespace(action=action,strategy="trend",stop_loss=stop,targets=[110,target],regime=MarketRegime.TREND)


def test_exit_bar_close_can_generate_the_next_non_overlapping_entry(monkeypatch):
    one,four=_flat_history()
    for index in (300,301):
        one[index]=one[index].model_copy(update={"low":89})

    def fake(one_hour,*args,**kwargs):
        return _advice() if one_hour[-1].timestamp in (299*HOUR,300*HOUR) else _advice(AdviceAction.WAIT)

    monkeypatch.setattr("backend.backtest.analyze",fake)
    result=run_backtest(one,four,[],fee_bps=0,slippage_bps=0,
                        evaluation_start_ms=300*HOUR,evaluation_end_ms=302*HOUR,strict_validation=False)
    assert result["trades"]==2
    assert result["netReturn"]==pytest.approx(.9*.9-1)


def test_evaluation_end_forces_exit_without_reading_the_next_window(monkeypatch):
    one,four=_flat_history()
    one[301]=one[301].model_copy(update={"close":105,"high":106})
    one[302]=one[302].model_copy(update={"open":500,"high":501,"low":499,"close":500})

    def fake(one_hour,*args,**kwargs):
        return _advice(stop=50,target=200) if one_hour[-1].timestamp==300*HOUR else _advice(AdviceAction.WAIT)

    monkeypatch.setattr("backend.backtest.analyze",fake)
    result=run_backtest(one,four,[],fee_bps=0,slippage_bps=0,
                        evaluation_start_ms=301*HOUR,evaluation_end_ms=302*HOUR,strict_validation=False)
    assert result["trades"]==1
    assert result["netReturn"]==pytest.approx(.05)
    assert result["equityCurve"][-1]["timestamp"]==302*HOUR


def test_stop_gap_uses_worse_open_price(monkeypatch):
    one,four=_flat_history()
    one[300]=one[300].model_copy(update={"low":95})
    one[301]=one[301].model_copy(update={"open":80,"high":82,"low":79,"close":81})

    def fake(one_hour,*args,**kwargs):
        return _advice(stop=90,target=120) if one_hour[-1].timestamp==299*HOUR else _advice(AdviceAction.WAIT)

    monkeypatch.setattr("backend.backtest.analyze",fake)
    result=run_backtest(one,four,[],fee_bps=0,slippage_bps=0,
                        evaluation_start_ms=300*HOUR,evaluation_end_ms=302*HOUR,strict_validation=False)
    assert result["trades"]==1
    assert result["netReturn"]==pytest.approx(-.2)


def test_intrabar_exit_excludes_funding_settled_at_candle_close(monkeypatch):
    one,four=_flat_history()
    one[300]=one[300].model_copy(update={"low":95})
    one[301]=one[301].model_copy(update={"low":89})

    def fake(one_hour,*args,**kwargs):
        return _advice(stop=90,target=120) if one_hour[-1].timestamp==299*HOUR else _advice(AdviceAction.WAIT)

    monkeypatch.setattr("backend.backtest.analyze",fake)
    result=run_backtest(one,four,[(301*HOUR,.01),(302*HOUR,.01)],fee_bps=0,slippage_bps=0,
                        evaluation_start_ms=300*HOUR,evaluation_end_ms=303*HOUR,strict_validation=False)
    # The 301H settlement occurs at the exit bar open and is paid. The 302H
    # settlement is at its close, after the intrabar stop, and is not paid.
    assert result["netReturn"]==pytest.approx(-.11)


def test_max_drawdown_marks_open_positions_to_market_hourly(monkeypatch):
    one,four=_flat_history()
    one[300]=one[300].model_copy(update={"high":101,"low":79,"close":80})

    def fake(one_hour,*args,**kwargs):
        return _advice(stop=50,target=200) if one_hour[-1].timestamp==299*HOUR else _advice(AdviceAction.WAIT)

    monkeypatch.setattr("backend.backtest.analyze",fake)
    result=run_backtest(one,four,[],fee_bps=0,slippage_bps=0,
                        evaluation_start_ms=300*HOUR,evaluation_end_ms=302*HOUR,strict_validation=False)
    assert result["netReturn"]==pytest.approx(0)
    assert result["maxDrawdown"]==pytest.approx(.2)
    assert result["stress"]["20bps"]["maxDrawdown"]>=.2
