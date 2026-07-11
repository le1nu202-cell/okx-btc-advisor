from __future__ import annotations

import numpy as np


def _a(x): return np.asarray(x, dtype=float)


def ema(values, period: int) -> np.ndarray:
    x=_a(values); out=np.full(len(x),np.nan)
    if len(x)<period:return out
    out[period-1]=np.mean(x[:period]); alpha=2/(period+1)
    for i in range(period,len(x)): out[i]=alpha*x[i]+(1-alpha)*out[i-1]
    return out


def sma(values, period:int)->np.ndarray:
    x=_a(values); out=np.full(len(x),np.nan)
    if len(x)>=period: out[period-1:]=np.convolve(x,np.ones(period)/period,"valid")
    return out


def true_range(high,low,close)->np.ndarray:
    h,l,c=map(_a,(high,low,close)); prev=np.r_[np.nan,c[:-1]]
    return np.maximum(h-l,np.maximum(np.abs(h-prev),np.abs(l-prev)))


def wilder(values,period:int)->np.ndarray:
    x=_a(values); out=np.full(len(x),np.nan)
    if len(x)<period:return out
    out[period-1]=np.nanmean(x[:period])
    for i in range(period,len(x)): out[i]=(out[i-1]*(period-1)+x[i])/period
    return out


def atr(high,low,close,period=14): return wilder(true_range(high,low,close),period)


def rsi(values,period=14)->np.ndarray:
    x=_a(values); d=np.diff(x,prepend=np.nan)
    gain=np.where(d>0,d,0); loss=np.where(d<0,-d,0)
    ag=wilder(gain[1:],period); al=wilder(loss[1:],period); out=np.full(len(x),np.nan)
    with np.errstate(divide="ignore",invalid="ignore"):
        rs=ag/al; vals=100-(100/(1+rs)); vals=np.where((al==0)&(ag>0),100,vals); vals=np.where((al==0)&(ag==0),0,vals)
    out[1:]=vals; return out


def macd(values,fast=12,slow=26,signal=9):
    f,s=ema(values,fast),ema(values,slow); line=f-s
    valid=line[~np.isnan(line)]; sig=np.full(len(line),np.nan)
    if len(valid)>=signal:
        e=ema(valid,signal); sig[np.where(~np.isnan(line))[0]]=e
    return line,sig,line-sig


def obv(close,volume)->np.ndarray:
    c,v=map(_a,(close,volume)); out=np.zeros(len(c))
    for i in range(1,len(c)): out[i]=out[i-1]+(v[i] if c[i]>c[i-1] else -v[i] if c[i]<c[i-1] else 0)
    return out


def bollinger(values,period=20,stddev=2):
    x=_a(values); mid=sma(x,period); sd=np.full(len(x),np.nan)
    for i in range(period-1,len(x)): sd[i]=np.std(x[i-period+1:i+1],ddof=0)
    return mid+stddev*sd,mid,mid-stddev*sd


def adx(high,low,close,period=14)->np.ndarray:
    h,l,c=map(_a,(high,low,close)); up=np.diff(h,prepend=np.nan); down=-np.diff(l,prepend=np.nan)
    plus=np.where((up>down)&(up>0),up,0); minus=np.where((down>up)&(down>0),down,0)
    a=atr(h,l,c,period)
    with np.errstate(divide="ignore",invalid="ignore"):
        p=100*wilder(plus[1:],period)/a[1:]; m=100*wilder(minus[1:],period)/a[1:]; dx=100*np.abs(p-m)/(p+m)
    out=np.full(len(c),np.nan); out[1:]=wilder(dx,period); return out


def stochastic_rsi(values, rsi_period=14, stoch_period=14, smooth_k=3, smooth_d=3):
    """Return Stochastic RSI %K and %D in the 0..100 range."""
    rr=rsi(values,rsi_period); raw=np.full(len(rr),np.nan)
    for i in range(stoch_period-1,len(rr)):
        window=rr[i-stoch_period+1:i+1]
        if np.isnan(window).any():continue
        lo,hi=float(np.min(window)),float(np.max(window))
        raw[i]=50.0 if hi==lo else 100.0*(rr[i]-lo)/(hi-lo)
    valid=np.where(~np.isnan(raw))[0]; k=np.full(len(rr),np.nan); d=np.full(len(rr),np.nan)
    if len(valid)>=smooth_k:
        kv=sma(raw[valid],smooth_k);k[valid]=kv
        kval=np.where(~np.isnan(k))[0]
        if len(kval)>=smooth_d:d[kval]=sma(k[kval],smooth_d)
    return k,d


def money_flow_index(high,low,close,volume,period=14)->np.ndarray:
    """Volume-weighted momentum oscillator in the 0..100 range."""
    h,l,c,v=map(_a,(high,low,close,volume));typical=(h+l+c)/3
    flow=typical*v;delta=np.diff(typical,prepend=np.nan)
    positive=np.where(delta>0,flow,0.0);negative=np.where(delta<0,flow,0.0)
    out=np.full(len(c),np.nan)
    for i in range(period,len(c)):
        pos=float(np.sum(positive[i-period+1:i+1]));neg=float(np.sum(negative[i-period+1:i+1]))
        out[i]=50.0 if pos==0 and neg==0 else 100.0 if neg==0 else 100.0-(100.0/(1.0+pos/neg))
    return out


def williams_r(high,low,close,period=14)->np.ndarray:
    """Williams %R oscillator in the -100..0 range."""
    h,l,c=map(_a,(high,low,close));out=np.full(len(c),np.nan)
    for i in range(period-1,len(c)):
        hh=float(np.max(h[i-period+1:i+1]));ll=float(np.min(l[i-period+1:i+1]))
        out[i]=-50.0 if hh==ll else -100.0*(hh-c[i])/(hh-ll)
    return out
