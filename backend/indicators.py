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
