from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from .indicators import ema
from .models import AdviceAction, Candle
from .strategy import analyze

HOUR = 3_600_000


def _valid(c: Candle, timeframe: str) -> bool:
    values=(c.open,c.high,c.low,c.close,c.volume)
    return bool(c.confirm and c.timeframe==timeframe and all(math.isfinite(float(x)) for x in values)
                and min(c.open,c.high,c.low,c.close)>0 and c.volume>=0
                and c.low<=min(c.open,c.close)<=max(c.open,c.close)<=c.high)


def _clean(rows:list[Candle], timeframe:str) -> tuple[list[Candle],int]:
    by_ts={c.timestamp:c for c in rows if _valid(c,timeframe)}
    clean=[by_ts[k] for k in sorted(by_ts)]
    step=HOUR if timeframe=="1H" else 4*HOUR
    gaps=sum(1 for a,b in zip(clean,clean[1:]) if b.timestamp-a.timestamp!=step)
    return clean,gaps


def _compound(values) -> float:
    result=1.0
    for value in values:result*=max(.01,1+float(value))
    return result-1


def _drawdown(values) -> float:
    curve=np.cumprod(1+np.clip(np.asarray(values,dtype=float),-.99,None)) if len(values) else np.array([1.0])
    curve=np.r_[1.0,curve];peak=np.maximum.accumulate(curve)
    return float(np.max(1-curve/peak))


def _scenario(trades:list[dict],fee_bps:float,slippage_bps:float) -> dict:
    returns=[]
    for trade in trades:
        net=trade["grossReturn"]-2*(fee_bps+slippage_bps)/10_000-trade["fundingCost"]
        returns.append(net)
    wins=sum(x for x in returns if x>0);losses=-sum(x for x in returns if x<0)
    return {"netReturn":_compound(returns),"maxDrawdown":_drawdown(returns),
            "profitFactor":float(wins/losses) if losses else (999.0 if wins else 0.0)}


def _ema_benchmark(candles:list[Candle],fee_bps:float,slippage_bps:float) -> float:
    close=np.asarray([c.close for c in candles],dtype=float);fast,slow=ema(close,20),ema(close,50)
    returns=[];previous=0
    for i in range(1,len(close)):
        position=0 if not np.isfinite(slow[i-1]) else (1 if fast[i-1]>slow[i-1] else -1)
        turnover=abs(position-previous);cost=turnover*(fee_bps+slippage_bps)/10_000
        returns.append(position*(close[i]/close[i-1]-1)-cost);previous=position
    return _compound(returns)


def run_backtest(c1:list[Candle],c4:list[Candle],funding:list[tuple[int,float]],strategy="combined",fee_bps=5,slippage_bps=5,max_hold_bars=24)->dict:
    c1,gaps_1h=_clean(c1,"1H");c4,gaps_4h=_clean(c4,"4H")
    if len(c1)<300 or len(c4)<100:
        return {"status":"insufficient_data","trades":0,"validationPass":False,"reason":"清洗后历史K线不足","dataQuality":{"gaps1H":gaps_1h,"gaps4H":gaps_4h}}
    funding=sorted({int(ts):float(rate) for ts,rate in funding if math.isfinite(float(rate))}.items())
    c4_ts=[x.timestamp for x in c4];trades=[];hourly=np.zeros(len(c1));i=220;held_bars=0
    while i<len(c1)-1:
        current=c1[i];nxt=c1[i+1]
        if nxt.timestamp-current.timestamp!=HOUR:i+=1;continue
        k=bisect_right(c4_ts,current.timestamp);four=c4[max(0,k-260):k]
        known_funding=[x for x in funding if x[0]<=current.timestamp+HOUR]
        advice=analyze(c1[max(0,i-300):i+1],four,known_funding,now_ms=current.timestamp+HOUR)
        if advice.action not in (AdviceAction.LONG_CANDIDATE,AdviceAction.SHORT_CANDIDATE) or (strategy!="combined" and advice.strategy!=strategy):
            i+=1;continue
        side=1 if advice.action==AdviceAction.LONG_CANDIDATE else -1;entry=nxt.open;stop=advice.stop_loss
        target=advice.targets[1] if len(advice.targets)>1 else None
        if stop is None or target is None or (side>0 and (stop>=entry or target<=entry)) or (side<0 and (stop<=entry or target>=entry)):
            i+=1;continue
        exit_idx=min(len(c1)-1,i+max_hold_bars);exit_price=c1[exit_idx].close;reason="maxHold"
        previous=nxt
        for j in range(i+1,exit_idx+1):
            bar=c1[j]
            if j>i+1 and bar.timestamp-previous.timestamp!=HOUR:
                exit_idx=j-1;exit_price=previous.close;reason="dataGap";break
            stop_hit=bar.low<=stop if side>0 else bar.high>=stop
            target_hit=bar.high>=target if side>0 else bar.low<=target
            if stop_hit:exit_idx=j;exit_price=stop;reason="stop";break
            if target_hit:exit_idx=j;exit_price=target;reason="target";break
            previous=bar
        exit_close_ts=c1[exit_idx].timestamp+HOUR
        funding_cost=side*sum(rate for ts,rate in funding if nxt.timestamp<ts<=exit_close_ts)
        gross=side*(exit_price-entry)/entry
        net=gross-2*(fee_bps+slippage_bps)/10_000-funding_cost
        hourly[exit_idx]+=net;held_bars+=max(1,exit_idx-i)
        trades.append({"entryTime":nxt.timestamp,"exitTime":exit_close_ts,"exitIndex":exit_idx,"side":"long" if side>0 else "short","grossReturn":gross,"fundingCost":funding_cost,"return":net,"reason":reason,"regime":advice.regime.value,"stop":stop,"target":target,"barsHeld":max(1,exit_idx-i)})
        i=exit_idx+1

    returns=np.asarray([t["return"] for t in trades],dtype=float);base=_scenario(trades,fee_bps,slippage_bps)
    hourly_mean=float(np.mean(hourly));hourly_std=float(np.std(hourly,ddof=1)) if len(hourly)>1 else 0
    sharpe=hourly_mean/hourly_std*math.sqrt(365*24) if hourly_std>0 else 0.0
    downside=hourly[hourly<0];down_std=float(np.std(downside,ddof=1)) if len(downside)>1 else 0
    sortino=hourly_mean/down_std*math.sqrt(365*24) if down_std>0 else 0.0
    years=max((c1[-1].timestamp-c1[0].timestamp)/(365*24*HOUR),1/365)
    annualized=(1+base["netReturn"])**(1/years)-1 if base["netReturn"]>-1 else -1
    calmar=annualized/base["maxDrawdown"] if base["maxDrawdown"]>0 else 0.0
    by_year=defaultdict(list)
    for trade in trades:by_year[str(datetime.fromtimestamp(trade["entryTime"]/1000,timezone.utc).year)].append(trade["return"])
    by_year={year:{"trades":len(vals),"netReturn":_compound(vals)} for year,vals in by_year.items()}
    max_losing=losing=0
    for value in returns:
        losing=losing+1 if value<0 else 0;max_losing=max(max_losing,losing)
    expected=max(1,math.floor((c1[-1].timestamp-c1[0].timestamp)/(8*HOUR)));funding_coverage=min(1.0,len(funding)/expected)
    stresses={f"{bps}bps":_scenario(trades,fee_bps,bps) for bps in (5,10,20)}
    buy_hold=c1[-1].close/c1[0].open-1-2*(fee_bps+slippage_bps)/10_000
    return {"status":"complete","strategy":strategy,"netReturn":base["netReturn"],"maxDrawdown":base["maxDrawdown"],"sharpe":sharpe,"sortino":sortino,"calmar":calmar,"profitFactor":base["profitFactor"],"winRate":float(np.mean(returns>0)) if len(returns) else 0,"trades":len(trades),"exposure":held_bars/max(1,len(c1)),"maxConsecutiveLosses":max_losing,"averageBarsHeld":float(np.mean([t["barsHeld"] for t in trades])) if trades else 0,"byYear":by_year,"fundingCoverage":funding_coverage,"fundingApproximation":funding_coverage<.95,"openInterestIncluded":False,"newsIncluded":False,"stress":stresses,"benchmarks":{"cash":0.0,"buyHold1x":buy_hold,"ema20_50":_ema_benchmark(c1,fee_bps,slippage_bps)},"dataQuality":{"gaps1H":gaps_1h,"gaps4H":gaps_4h},"pbo":None,"deflatedSharpe":None,"validationPass":False,"validationLabel":"实验信号","holdingRule":f"信号收盘后按下一根开盘进入；使用信号时绝对止损/2R目标，最多持有{max_hold_bars}根1H；同根止盈止损先按止损；不允许重叠持仓","limitations":["资金费率缺口按0近似" if funding_coverage<.95 else "资金费率覆盖充分","历史OI与point-in-time新闻不可得，未进入回测","尚未完成18月/3月滚动和最后12月锁定验证，因此强制 validationPass=false","PBO/Deflated Sharpe需多个独立参数组合，当前不伪造"]}
