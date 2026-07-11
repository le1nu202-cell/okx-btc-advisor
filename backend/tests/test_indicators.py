import numpy as np

from backend.indicators import adx, atr, bollinger, ema, money_flow_index, obv, rsi, stochastic_rsi, williams_r


def test_ema_fixed_vector():
    got=ema([1,2,3,4,5],3)
    assert np.isnan(got[0]) and np.isnan(got[1])
    assert np.allclose(got[2:],[2,3,4])


def test_rsi_monotonic_is_100():
    got=rsi(np.arange(1,40),14)
    assert got[-1] == 100


def test_atr_constant_range():
    close=np.arange(100,140,dtype=float); high=close+1;low=close-1
    got=atr(high,low,close,14)
    assert np.isfinite(got[-1]) and abs(got[-1]-2)<1e-9


def test_bollinger_and_obv():
    upper,mid,lower=bollinger(np.ones(30)*10)
    assert upper[-1]==mid[-1]==lower[-1]==10
    assert np.array_equal(obv([1,2,1,1],[5,6,7,8]),[0,6,-1,-1])


def test_adx_trend_is_high():
    close=np.arange(1,80,dtype=float); got=adx(close+1,close-1,close)
    assert got[-1]>90


def test_advanced_oscillators_are_bounded():
    close=100+np.sin(np.arange(120)/5)*8+np.arange(120)*.05
    high=close+1;low=close-1;volume=1000+np.arange(120)*3
    k,d=stochastic_rsi(close)
    mfi=money_flow_index(high,low,close,volume)
    wr=williams_r(high,low,close)
    assert 0<=k[-1]<=100 and 0<=d[-1]<=100
    assert 0<=mfi[-1]<=100
    assert -100<=wr[-1]<=0


def test_money_flow_flat_zero_volume_is_neutral():
    values=np.ones(40)*100;volume=np.zeros(40)
    assert money_flow_index(values+1,values-1,values,volume)[-1]==50
