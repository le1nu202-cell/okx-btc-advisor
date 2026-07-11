from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime,timezone
import numpy as np

from .models import AdviceAction,Candle
from .strategy import analyze


def run_backtest(c1:list[Candle],c4:list[Candle],funding:list[tuple[int,float]],strategy="combined",fee_bps=5,slippage_bps=5)->dict:
    if len(c1)<300 or len(c4)<100:return {"status":"insufficient_data","trades":0,"validationPass":False,"reason":"历史K线不足"}
    trades=[]; eq=1.0; curve=[eq]; fund_ts=np.array([x[0] for x in funding],dtype=np.int64) if funding else np.array([],dtype=np.int64)
    fund_map=dict(funding)
    for i in range(220,len(c1)-1):
        current=c1[i]; four=[x for x in c4 if x.timestamp<=current.timestamp]
        if len(four)<60:continue
        known_funding=[x for x in funding if x[0]<=current.timestamp]
        a=analyze(c1[max(0,i-300):i+1],four[-250:],known_funding,now_ms=current.timestamp+3600_000)
        if a.action not in (AdviceAction.LONG_CANDIDATE,AdviceAction.SHORT_CANDIDATE):continue
        if strategy!="combined" and a.strategy!=strategy:continue
        nxt=c1[i+1]; side=1 if a.action==AdviceAction.LONG_CANDIDATE else -1
        # Model entry/exit slippage exactly once in the round-trip cost below.
        entry=nxt.open; stop=a.stop_loss
        if stop is None:continue
        if (side>0 and stop>=entry) or (side<0 and stop<=entry):continue
        risk=abs(entry-stop); target=entry+side*2*risk; exit_price=nxt.close; reason="close"
        # Conservative: stop wins if both stop and target occur in one bar.
        stop_hit=nxt.low<=stop if side>0 else nxt.high>=stop; target_hit=nxt.high>=target if side>0 else nxt.low<=target
        if stop_hit:exit_price=stop; reason="stop"
        elif target_hit:exit_price=target; reason="target"
        gross=side*(exit_price-entry)/entry; costs=2*(fee_bps+slippage_bps)/10000
        applicable=[rate for ts,rate in funding if nxt.timestamp<ts<=nxt.timestamp+3600_000]
        funding_cost=side*sum(applicable); net=gross-costs-funding_cost; eq*=max(.01,1+net); curve.append(eq)
        trades.append({"entryTime":nxt.timestamp,"side":"long" if side>0 else "short","return":net,"reason":reason,"regime":a.regime.value})
    returns=np.array([t["return"] for t in trades]); wins=returns[returns>0].sum() if len(returns) else 0; losses=-returns[returns<0].sum() if len(returns) else 0
    sharpe=float(np.mean(returns)/np.std(returns)*math.sqrt(max(1,len(returns)))) if len(returns)>1 and np.std(returns)>0 else 0
    peak=np.maximum.accumulate(curve); dd=float(np.max(1-np.array(curve)/peak))
    by_year=defaultdict(lambda:{"trades":0,"netReturn":0.0})
    for t in trades:
        y=str(datetime.fromtimestamp(t["entryTime"]/1000,timezone.utc).year);by_year[y]["trades"]+=1;by_year[y]["netReturn"]+=t["return"]
    start,end=c1[0].timestamp,c1[-1].timestamp; expected=max(1,math.floor((end-start)/(8*3600_000)))
    funding_coverage=min(1.0,len(funding)/expected)
    pf=float(wins/losses) if losses else (999.0 if wins else 0)
    stress20=float(np.sum(returns)-len(returns)*.003) # +15bp each side relative to 5bp base
    # This first implementation is deliberately not certified: full rolling
    # walk-forward and a locked 12-month holdout must be run before certification.
    validation=False
    return {"status":"complete","strategy":strategy,"netReturn":eq-1,"maxDrawdown":dd,"sharpe":sharpe,"profitFactor":pf,"winRate":float(np.mean(returns>0)) if len(returns) else 0,"trades":len(trades),"byYear":dict(by_year),"fundingCoverage":funding_coverage,"fundingApproximation":funding_coverage<.95,"openInterestIncluded":False,"stress":{"5bps":float(np.sum(returns)),"10bps":float(np.sum(returns)-len(returns)*.001),"20bps":stress20},"pbo":None,"deflatedSharpe":None,"validationPass":validation,"validationLabel":"实验信号","holdingRule":"信号收盘后按下一根开盘进入，仅持有下一根1H；同根止盈止损先按止损；成本包含双边手续费和进场/出场总滑点近似","limitations":["资金费率缺口按0近似" if funding_coverage<.95 else "资金费率覆盖充分","历史OI不可得，未进入回测","尚未完成18月/3月滚动和最后12月锁定验证，因此强制 validationPass=false","现金、1x持有、EMA基准尚未实现","PBO/Deflated Sharpe需多个独立参数组合，当前不伪造"]}
