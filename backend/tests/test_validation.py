from datetime import datetime,timezone

import numpy as np

from backend.models import Candle
from backend.validation import _timeline_quality, deflated_sharpe_ratio, probability_of_backtest_overfitting, run_strict_validation


def ts(year,month,day=1):
    return int(datetime(year,month,day,tzinfo=timezone.utc).timestamp()*1000)


def candle(timestamp,timeframe):
    return Candle(timestamp=timestamp,open=100,high=102,low=99,close=101,volume=10,timeframe=timeframe,confirm=True)


def moments(values):
    values=np.asarray(values,dtype=float)
    return {"count":len(values),**{f"sumPower{power}":float(np.sum(values**power)) for power in range(1,5)}}


def test_dsr_is_available_only_with_enough_observations_and_trials():
    sample=np.tile([.001,-.0004,.0008,-.0002],100)
    unavailable=deflated_sharpe_ratio(moments(sample),[.1])
    available=deflated_sharpe_ratio(moments(sample),[.01,.02,-.01])
    assert unavailable["status"]=="unavailable" and unavailable["value"] is None
    assert available["status"]=="available" and 0<=available["value"]<=1


def test_dsr_multiple_testing_hurdle_includes_trial_mean():
    sample=np.tile([.001,-.0004,.0008,-.0002],100)
    centred=deflated_sharpe_ratio(moments(sample),[0,0,0])
    shifted=deflated_sharpe_ratio(moments(sample),[.5,.5,.5])
    assert shifted["deflatedBenchmarkSharpe"]>centred["deflatedBenchmarkSharpe"]
    assert shifted["value"]<centred["value"]


def test_pbo_requires_multiple_configs_and_eight_periods():
    assert probability_of_backtest_overfitting([[1],[2]] )["status"]=="unavailable"
    matrix=[[.1+i*.01,.08-i*.005,-.02] for i in range(8)]
    result=probability_of_backtest_overfitting(matrix)
    assert result["status"]=="available" and 0<=result["value"]<=1
    assert result["splits"]==70 and result["exhaustive"] is True
    too_large=probability_of_backtest_overfitting([[.1,.2] for _ in range(22)])
    assert too_large["status"]=="unavailable" and "安全上限" in too_large["reason"]


def test_timeline_quality_reports_long_contiguous_gaps():
    c1=[candle(i*3_600_000,"1H") for i in list(range(10))+list(range(20,40))]
    c4=[candle(i*4*3_600_000,"4H") for i in range(10)]
    quality=_timeline_quality(c1,c4,0,40*3_600_000)
    assert quality["maximumGapHours1H"]==10
    assert quality["coverage1H"]==.75


def test_strict_validation_uses_calendar_windows_and_locked_holdout():
    start,end=ts(2022,1),ts(2026,1)
    c1=[candle(timestamp,"1H") for timestamp in range(start,end,3_600_000)]
    c4=[candle(timestamp,"4H") for timestamp in range(start,end,4*3_600_000)]
    calls=[]
    def runner(one,four,funding,strategy,fee,slippage,**kwargs):
        calls.append(kwargs)
        positive=.02;count=90*24
        data_moments={"count":count,"sumPower1":.2,"sumPower2":.01,"sumPower3":0.0,"sumPower4":.0001}
        scenario=lambda net:{"netReturn":net,"grossProfit":2.0,"grossLoss":1.0}
        return {"status":"complete","netReturn":positive,"maxDrawdown":.05,"sharpe":1.0,"profitFactor":2.0,"grossProfit":2.0,"grossLoss":1.0,
                "winRate":.55,"trades":25,"exposure":.1,"fundingCoverage":1.0,"returnMoments":data_moments,
                "stress":{"5bps":scenario(.02),"10bps":scenario(.015),"20bps":scenario(.005)},"dataQuality":{"gaps1H":0,"gaps4H":0}}
    result=run_strict_validation(c1,c4,[],"combined",5,5,runner)
    assert result["status"]=="complete" and len(result["windows"])==6
    assert result["windows"][0]["trainStart"]==ts(2022,1)
    assert result["windows"][0]["trainEnd"]==ts(2023,7)
    assert result["windows"][0]["oosEnd"]==ts(2023,10)
    assert result["windows"][-1]["oosEnd"]==result["holdout"]["start"]==ts(2025,1)
    assert result["holdout"]["locked"] and result["holdout"]["end"]==end
    assert all(call["strict_validation"] is False for call in calls)
    assert result["aggregateOos"]["trades"]==150
    assert result["thresholdPass"] and not result["validationPass"]
    assert result["pbo"]["status"]==result["deflatedSharpe"]["status"]=="unavailable"


def test_strict_validation_reports_insufficient_history_honestly():
    start,end=ts(2024,1),ts(2026,1)
    result=run_strict_validation([candle(start,"1H"),candle(end-3_600_000,"1H")],
                                 [candle(start,"4H"),candle(end-4*3_600_000,"4H")],[],"combined",5,5,lambda *a,**k:{})
    assert result["status"]=="unavailable" and result["validationPass"] is False
    assert result["pbo"]["value"] is None and result["deflatedSharpe"]["value"] is None


def test_windows_anchor_backward_when_1h_and_4h_starts_are_offset():
    one_start=ts(2023,7,12)+17*3_600_000
    four_start=ts(2023,7,12)+20*3_600_000
    end=ts(2026,7,12)+17*3_600_000
    calls=[]

    def runner(*args,**kwargs):
        calls.append(kwargs)
        scenario={"netReturn":.01}
        return {"status":"complete","netReturn":.01,"sharpe":1,"profitFactor":2,"grossProfit":2,"grossLoss":1,
                "trades":60,"fundingCoverage":1,"returnMoments":{"count":2000,"sumPower1":.2,"sumPower2":.01,
                "sumPower3":0,"sumPower4":.0001},"stress":{"5bps":scenario,"10bps":scenario,"20bps":scenario}}

    result=run_strict_validation([candle(one_start,"1H"),candle(end-3_600_000,"1H")],
                                 [candle(four_start,"4H"),candle(end-3_600_000,"4H")],[],"combined",5,5,runner)
    assert len(result["windows"])==2
    assert result["windows"][-1]["oosEnd"]==result["holdout"]["start"]
    assert result["scheme"]["anchor"]=="locked_holdout_backward"


def test_incomplete_oos_window_and_missing_funding_cannot_pass_thresholds():
    start,end=ts(2022,1),ts(2026,1)
    c1=[candle(timestamp,"1H") for timestamp in range(start,end,3_600_000)]
    c4=[candle(timestamp,"4H") for timestamp in range(start,end,4*3_600_000)]
    call=0

    def runner(*args,**kwargs):
        nonlocal call
        call+=1
        if call==2:
            return {"status":"insufficient_data"}
        scenario={"netReturn":.1}
        return {"status":"complete","netReturn":.1,"sharpe":2,"profitFactor":3,"grossProfit":3,"grossLoss":1,
                "trades":100,"fundingCoverage":.5,"returnMoments":{"count":2000,"sumPower1":.2,"sumPower2":.01,
                "sumPower3":0,"sumPower4":.0001},"stress":{"5bps":scenario,"10bps":scenario,"20bps":scenario}}

    result=run_strict_validation(c1,c4,[],"combined",5,5,runner)
    assert result["aggregateOos"]["allWindowsComplete"] is False
    assert result["criteria"]["fundingCoverageAtLeast95Pct"] is False
    assert result["thresholdPass"] is False and result["validationPass"] is False
