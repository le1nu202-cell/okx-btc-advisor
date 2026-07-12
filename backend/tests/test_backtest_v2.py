import pytest

from backend.backtest import _clean, _scenario
from backend.models import Candle


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
