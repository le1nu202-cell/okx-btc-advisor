from __future__ import annotations

import math
import numpy as np

from .indicators import adx, atr, bollinger, ema, macd, money_flow_index, obv, rsi, sma, stochastic_rsi
from .models import AdviceAction, Candle, DataQuality, IndicatorContribution, MarketRegime, RiskEstimate, Settings, SignalAdvice


def _arrays(candles):
    return tuple(np.array([getattr(x,k) for x in candles],float) for k in ("open","high","low","close","volume"))


def _valid_candle(x:Candle)->bool:
    values=(x.open,x.high,x.low,x.close,x.volume)
    if not all(math.isfinite(float(v)) for v in values):return False
    if min(x.open,x.high,x.low,x.close)<=0 or x.volume<0:return False
    return x.low<=min(x.open,x.close)<=max(x.open,x.close)<=x.high


def _clean_confirmed(candles:list[Candle])->list[Candle]:
    by_time={x.timestamp:x for x in candles if x.confirm}
    return [by_time[k] for k in sorted(by_time)]


def _has_recent_gap(candles:list[Candle], step_ms:int, required:int)->bool:
    if len(candles)<required:return False
    recent=candles[-required:]
    return any(right.timestamp-left.timestamp!=step_ms for left,right in zip(recent,recent[1:]))


def classify_regime(candles_4h: list[Candle]) -> tuple[MarketRegime,dict]:
    if len(candles_4h)<60:return MarketRegime.TRANSITION,{"reason":"4H数据不足"}
    _,h,l,c,_=_arrays(candles_4h); a=atr(h,l,c); d=adx(h,l,c); e20,e50=ema(c,20),ema(c,50)
    if not all(math.isfinite(float(x[-1])) for x in (a,d,e20,e50)) or a[-1]<=0:
        return MarketRegime.TRANSITION,{"reason":"4H指标尚不可用","adx":None,"emaAtrGap":None,"atr":float(a[-1]) if math.isfinite(float(a[-1])) else None,"ema20":float(e20[-1]) if math.isfinite(float(e20[-1])) else None,"ema50":float(e50[-1]) if math.isfinite(float(e50[-1])) else None}
    gap=abs(e20[-1]-e50[-1])/a[-1] if a[-1] else 0
    vals={"adx":float(d[-1]),"emaAtrGap":float(gap),"atr":float(a[-1]),"ema20":float(e20[-1]),"ema50":float(e50[-1])}
    if d[-1]>=25 and gap>=0.5:return MarketRegime.TREND,vals
    if d[-1]<=20 and gap<=0.35:return MarketRegime.RANGE,vals
    return MarketRegime.TRANSITION,vals


def _quality(c1,c4,funding,oi,now_ms=None,decision_at=None):
    import time
    now_ms=now_ms or int(time.time()*1000); last=c1[-1].timestamp if c1 else None
    decision_at=decision_at or (last+3600_000 if last else now_ms)
    last_4h_close=c4[-1].timestamp+4*3600_000 if c4 else None
    one_hour_fresh=bool(last and 0<=now_ms-(last+3600_000)<=2*3600_000)
    four_hour_fresh=bool(last_4h_close and 0<=decision_at-last_4h_close<4*3600_000)
    fresh=one_hour_fresh and four_hour_fresh
    warnings=[]
    if not one_hour_fresh:warnings.append("1H行情已过期，停止生成新候选")
    if not four_hour_fresh:warnings.append("4H市场状态已过期，停止生成新候选")
    if funding is None:warnings.append("资金费率数据不足")
    if oi is None:warnings.append("持仓量数据不足（仅从应用启动后采样）")
    return DataQuality(fresh=fresh,last_candle_at=last,funding_available=funding is not None,open_interest_available=oi is not None,warnings=warnings)


def analyze(c1:list[Candle],c4:list[Candle],funding_rows:list[tuple[int,float]]|None=None,oi:dict|None=None,custom=False,now_ms=None,news_analysis:dict|None=None)->SignalAdvice:
    raw_funding_rows=funding_rows or []
    original_c1,original_c4=c1,c4
    import time
    try:analysis_now=int(now_ms) if now_ms is not None else int(time.time()*1000)
    except (TypeError,ValueError,OverflowError):analysis_now=int(time.time()*1000)
    future_c1=any(x.confirm and x.timestamp+3600_000>analysis_now for x in original_c1)
    c1=[x for x in _clean_confirmed(c1) if x.timestamp+3600_000<=analysis_now];c4=_clean_confirmed(c4)
    signal_open=c1[-1].timestamp if c1 else 0
    decision_at=signal_open+3600_000 if signal_open else 0
    # Validate only the point-in-time rows that are actually visible to this
    # decision. A malformed future row must not be able to change an earlier
    # historical result.
    c4=[x for x in c4 if x.timestamp+4*3600_000<=decision_at]
    invalid=any(not _valid_candle(x) for x in c1) or any(not _valid_candle(x) for x in c4)
    news_analysis=news_analysis or {"score":0,"status":"unavailable","items":[],"warnings":["消息面数据尚不可用"]}
    # A 1H decision may only see the 4H bar that has already closed.
    gap_1h=_has_recent_gap(c1,3600_000,220)
    gap_4h=_has_recent_gap(c4,4*3600_000,200)
    funding_rows=[]
    for row in raw_funding_rows:
        try:funding_time,funding_rate=int(row[0]),float(row[1])
        except (IndexError,TypeError,ValueError,OverflowError):continue
        if decision_at-90*86400_000<=funding_time<=decision_at and math.isfinite(funding_rate):
            funding_rows.append((funding_time,funding_rate))
    funding_rows=sorted(dict(funding_rows).items())
    funding=funding_rows[-1][1] if funding_rows else None
    funding_span=funding_rows[-1][0]-funding_rows[0][0] if len(funding_rows)>1 else 0
    funding_recent=bool(funding_rows and decision_at-funding_rows[-1][0]<=12*3600_000)
    funding_usable=len(funding_rows)>=216 and funding_span>=80*86400_000 and funding_recent
    usable_oi=oi if isinstance(oi,dict) else None
    if oi is not None and isinstance(oi,dict):
        try:
            oi_change=float(oi["changePercent"])
            oi_latest=int(oi["latestTime"]) if oi.get("latestTime") is not None else None
            oi_baseline=int(oi["baselineTime"]) if oi.get("baselineTime") is not None else None
            if not math.isfinite(oi_change) or (oi_latest is not None and oi_latest>decision_at):usable_oi=None
            if oi_latest is not None and oi_baseline is not None and oi_baseline>oi_latest:usable_oi=None
        except (KeyError,TypeError,ValueError,OverflowError):usable_oi=None
    quality=_quality(c1,c4,funding if funding_usable else None,usable_oi,analysis_now,decision_at)
    if funding is not None and not funding_usable:
        quality.warnings.append("90天资金费率样本不足或已过期（至少需要80天跨度、216个观测且最近12小时有记录），未修正置信度")
    if oi is not None and usable_oi is None:
        quality.warnings.append("持仓量上下文时间或数值无效，未展示24H确认标签")
    if future_c1:
        quality.warnings.append("已忽略决策时点之后才收盘的1H K线")
    if invalid:
        quality.warnings.append("K线包含非有限值、非法OHLC或负成交量，已阻止生成信号")
        ts=c1[-1].timestamp+3600_000 if c1 else 0
        return SignalAdvice(strategy="invalid_data",candle_close_at=ts,regime=MarketRegime.TRANSITION,action=AdviceAction.WAIT,direction_score=0,technical_score=0,news_score=0,news_analysis=news_analysis,confidence=0,invalidation="等待合法行情数据并重新计算",explanation="行情数据校验失败，未生成方向建议。",data_quality=quality,config_version="custom-v2-unvalidated" if custom else "research-v2-unvalidated")
    if len(c1)<220 or len(c4)<200:
        ts=c1[-1].timestamp+3600_000 if c1 else 0
        return SignalAdvice(strategy="insufficient",candle_close_at=ts,regime=MarketRegime.STALE if not quality.fresh else MarketRegime.TRANSITION,action=AdviceAction.WAIT,direction_score=0,technical_score=0,news_score=0,news_analysis=news_analysis,confidence=0,invalidation="等待足够的1H/4H已收盘K线",explanation="历史数据不足，暂不产生交易候选。",data_quality=quality,config_version="custom-v2-unvalidated" if custom else "research-v2-unvalidated")
    if gap_1h or gap_4h:
        missing=[]
        if gap_1h:missing.append("1H")
        if gap_4h:missing.append("4H")
        quality.fresh=False
        quality.warnings.append(f"最近策略窗口存在{'/'.join(missing)} K线缺口，已暂停生成新建议")
        ts=c1[-1].timestamp+3600_000
        return SignalAdvice(strategy="data_gap",candle_close_at=ts,regime=MarketRegime.STALE,action=AdviceAction.WAIT,direction_score=0,technical_score=0,news_score=0,news_analysis=news_analysis,confidence=0,invalidation="等待行情缺口回补完整",explanation="策略所需的最近K线序列不连续，安全起见停止生成新建议。",data_quality=quality,config_version="custom-v2-unvalidated" if custom else "research-v2-unvalidated")
    regime,rv=classify_regime(c4); o,h,l,c,v=_arrays(c1); a=atr(h,l,c); rr=rsi(c); ts=c1[-1].timestamp+3600_000
    if not quality.fresh:
        return SignalAdvice(strategy="stale",candle_close_at=ts,regime=MarketRegime.STALE,action=AdviceAction.WAIT,direction_score=0,technical_score=0,news_score=0,news_analysis=news_analysis,confidence=0,invalidation="等待行情恢复并回补缺口",explanation="行情过期，安全起见停止生成新建议。",data_quality=quality,config_version="research-v2-unvalidated")
    contributions=[]; score=0.0
    if regime==MarketRegime.TREND:
        e20,e50=ema(c,20),ema(c,50); e4_50,e4_200=ema(_arrays(c4)[3],50),ema(_arrays(c4)[3],200)
        ml,ms,mh=macd(c); oo=obv(c,v); vm=sma(v,20); sk,sd=stochastic_rsi(c); mf=money_flow_index(h,l,c,v)
        stoch_score=5 if sk[-2]<=sd[-2] and sk[-1]>sd[-1] and sk[-1]<30 else -5 if sk[-2]>=sd[-2] and sk[-1]<sd[-1] and sk[-1]>70 else 0
        mfi_score=5 if mf[-1]>=55 else -5 if mf[-1]<=45 else 0
        volume_score=(7 if c[-1]>=o[-1] else -7) if v[-1]>vm[-1]*1.2 else 0
        rules=[("1H均线",15 if e20[-1]>e50[-1] else -15,e20[-1]-e50[-1]),("4H趋势",15 if e4_50[-1]>e4_200[-1] else -15,e4_50[-1]-e4_200[-1]),("MACD",10 if mh[-1]>0 else -10,mh[-1]),("RSI",8 if rr[-1]>=55 else -8 if rr[-1]<=45 else 0,rr[-1]),("OBV",8 if oo[-1]>oo[-6] else -8,oo[-1]-oo[-6]),("量能确认",volume_score,v[-1]/vm[-1] if vm[-1] else 0),("20根结构突破",12 if c[-1]>max(h[-21:-1]) else -12 if c[-1]<min(l[-21:-1]) else 0,c[-1]),("Stoch RSI拐点",stoch_score,sk[-1]),("MFI资金流",mfi_score,mf[-1])]
        for n,s,val in rules: score+=s; contributions.append(IndicatorContribution(name=n,score=s,value=float(val),explanation=f"{n}贡献 {s:+.0f}"))
        strategy="trend"
    elif regime==MarketRegime.RANGE:
        upper,mid,lower=bollinger(c); sk,sd=stochastic_rsi(c); mf=money_flow_index(h,l,c,v)
        bands_valid=all(math.isfinite(float(value)) for value in (lower[-2],lower[-1],mid[-1],upper[-2],upper[-1])) and lower[-1]<mid[-1]<upper[-1]
        long=bands_valid and c[-2]<lower[-2] and lower[-1]<=c[-1]<mid[-1] and rr[-2]<35 and rr[-1]>=35
        short=bands_valid and c[-2]>upper[-2] and mid[-1]<c[-1]<=upper[-1] and rr[-2]>65 and rr[-1]<=65
        base=70 if long else -70 if short else 0
        stoch_score=10 if long and sk[-1]>sd[-1] else -10 if short and sk[-1]<sd[-1] else 0
        mfi_score=5 if long and mf[-1]>=50 else -5 if short and mf[-1]<=50 else 0
        score=base+stoch_score+mfi_score
        spread=upper[-1]-lower[-1];band_position=float((c[-1]-mid[-1])/spread) if math.isfinite(float(spread)) and spread else 0.0
        contributions=[IndicatorContribution(name="布林带回归",score=base*.7,value=band_position,explanation="价格重新进入区间" if base else "尚无边界回归确认"),IndicatorContribution(name="RSI反转",score=base*.3,value=float(rr[-1]),explanation="RSI与区间边界共同确认" if base else "等待RSI反转"),IndicatorContribution(name="Stoch RSI确认",score=stoch_score,value=float(sk[-1]),explanation="动量交叉确认" if stoch_score else "尚未确认"),IndicatorContribution(name="MFI资金流",score=mfi_score,value=float(mf[-1]),explanation="量价资金流确认" if mfi_score else "尚未确认")]
        strategy="range"
    else:
        return SignalAdvice(strategy="regime",candle_close_at=ts,regime=regime,action=AdviceAction.WAIT,direction_score=0,technical_score=0,news_score=0,news_analysis=news_analysis,confidence=20,contributions=[IndicatorContribution(name="4H ADX",score=0,value=rv.get("adx"),explanation="趋势与震荡阈值之间")],invalidation="等待4H市场状态明确",explanation="当前处于过渡状态，不强行选择方向；消息面仅供观察。",data_quality=quality,config_version="custom-v2-unvalidated" if custom else "research-v2-unvalidated")
    # Rule weights are designed to total at most 85 in either direction.  Use
    # the displayed contributions as the source of truth so explanations and
    # the published technical score can never diverge.
    technical_score=float(sum(x.score for x in contributions))
    if not -85<=technical_score<=85:raise AssertionError("technical contribution weights exceed 85")
    raw_news=0.0
    if news_analysis.get("status") in ("fresh","partial"):
        try: candidate=float(news_analysis.get("score",0) or 0)
        except (TypeError,ValueError): candidate=0.0
        if math.isfinite(candidate):raw_news=max(-15,min(15,candidate))
        coverage=news_analysis.get("sourceCoverage")
        if coverage is not None:
            try: coverage_value=float(coverage)
            except (TypeError,ValueError): coverage_value=0.0
            if not math.isfinite(coverage_value) or coverage_value<=0:raw_news=0.0
        as_of=news_analysis.get("asOf")
        if isinstance(as_of,(int,float)) and math.isfinite(float(as_of)):
            as_of_ms=float(as_of)*1000 if float(as_of)<1_000_000_000_000 else float(as_of)
            if as_of_ms>decision_at:
                raw_news=0.0
                quality.warnings.append("消息面时间晚于本次决策时点，已拒绝该新闻修正")
    applied_news=raw_news if abs(technical_score)>=30 else 0
    # This is also guaranteed by the 15-point cap and 30-point technical gate,
    # but keep the invariant explicit if either threshold is changed later.
    if technical_score>0 and technical_score+applied_news<0:applied_news=-technical_score
    if technical_score<0 and technical_score+applied_news>0:applied_news=-technical_score
    score=max(-100,min(100,technical_score+applied_news))
    action=AdviceAction.LONG_CANDIDATE if score>=60 else AdviceAction.WATCH_LONG if score>=30 else AdviceAction.SHORT_CANDIDATE if score<=-60 else AdviceAction.WATCH_SHORT if score<=-30 else AdviceAction.WAIT
    confidence=min(95,50+abs(technical_score)*.45)
    if applied_news and technical_score*applied_news<0:confidence=max(0,confidence-min(15,abs(applied_news)))
    elif applied_news:confidence=min(95,confidence+min(10,abs(applied_news)))
    if funding_usable:
        rates=np.array([x[1] for x in funding_rows]); pct=float(np.mean(rates<=funding))
        crowded=(score>0 and pct>=.9) or (score<0 and pct<=.1)
        if crowded: confidence=max(0,confidence-10); quality.warnings.append("极端资金费率与方向同向，置信度已下调10")
    if usable_oi is not None and regime==MarketRegime.TREND:
        change=float(usable_oi["changePercent"])
        if change>.005:oi_explanation=f"24H持仓量增加 {change*100:.2f}%，趋势有新增仓位支持"
        elif change<-.005:oi_explanation=f"24H持仓量减少 {abs(change)*100:.2f}%，趋势可能由平仓推动"
        else:oi_explanation=f"24H持仓量变化 {change*100:+.2f}%，新增仓位支持不明显"
        contributions.append(IndicatorContribution(name="24H持仓量确认",score=0,value=change,explanation=oi_explanation))
        quality.warnings.append(f"{oi_explanation}；仅作证据标签，不改变方向分")
    if action==AdviceAction.WAIT:confidence=min(confidence,25)
    entry=float(c[-1]); mult=2 if strategy=="trend" else 1.5
    if score>0:
        structural=float(min(l[-10:])); stop=min(structural,entry-mult*a[-1]); risk=entry-stop; targets=[entry+risk,entry+2*risk]
    elif score<0:
        structural=float(max(h[-10:])); stop=max(structural,entry+mult*a[-1]); risk=stop-entry; targets=[entry-risk,entry-2*risk]
    else: stop=None; targets=[]
    if strategy=="range" and score>0:
        targets=[float(mid[-1]),float(upper[-1])]
    elif strategy=="range" and score<0:
        targets=[float(mid[-1]),float(lower[-1])]
    targets_valid=not targets or (score>0 and all(target>entry for target in targets)) or (score<0 and all(target<entry for target in targets))
    if not targets_valid:
        quality.warnings.append("目标价方向无效，已阻止生成交易候选")
        return SignalAdvice(strategy="invalid_target",candle_close_at=ts,regime=regime,action=AdviceAction.WAIT,direction_score=0,technical_score=0,news_score=0,news_analysis=news_analysis,confidence=0,invalidation="等待重新形成方向正确的目标位",explanation="目标价不在预期收益方向，安全起见停止生成建议。",data_quality=quality,config_version="custom-v2-unvalidated" if custom else "research-v2-unvalidated")
    risk_reward=[((target-entry) if score>0 else (entry-target))/risk for target in targets] if targets and risk>0 else []
    expl=("趋势策略" if strategy=="trend" else "震荡反转策略")+f"技术分 {technical_score:+.0f}，消息修正 {applied_news:+.0f}，综合分 {score:+.0f}；"+("满足候选阈值。" if abs(score)>=60 else "尚未满足候选阈值。")
    return SignalAdvice(strategy=strategy,candle_close_at=ts,regime=regime,action=action,direction_score=score,technical_score=technical_score,news_score=applied_news,news_analysis=news_analysis,confidence=confidence,contributions=contributions,trigger_price=entry if action!=AdviceAction.WAIT else None,invalidation="价格触及参考止损或4H市场状态改变",stop_loss=stop,targets=targets,risk_reward=risk_reward,explanation=expl,data_quality=quality,config_version="custom-v2-unvalidated" if custom else "research-v2-unvalidated")


def estimate_risk(advice:SignalAdvice,settings:Settings)->RiskEstimate:
    warnings=["测算未包含实际手续费、滑点与资金费率；不构成投资建议。"]
    if not all(x is not None for x in (settings.equity,settings.risk_percent,settings.leverage)):
        warnings.insert(0,"请填写权益、每笔风险和杠杆后再计算仓位")
        return RiskEstimate(equity=settings.equity,risk_percent=settings.risk_percent,leverage=settings.leverage,entry_price=advice.trigger_price,stop_loss=advice.stop_loss,warnings=warnings)
    if advice.trigger_price is None or advice.stop_loss is None:
        warnings.insert(0,"当前没有有效触发价与止损价，暂不测算参考仓位")
        return RiskEstimate(equity=settings.equity,risk_percent=settings.risk_percent,leverage=settings.leverage,entry_price=advice.trigger_price,stop_loss=advice.stop_loss,warnings=warnings)
    distance=abs(advice.trigger_price-advice.stop_loss)
    base={"equity":settings.equity,"risk_percent":settings.risk_percent,"leverage":settings.leverage,"entry_price":advice.trigger_price,"stop_loss":advice.stop_loss}
    try:
        risk_cash=settings.equity*settings.risk_percent/100
        qty=risk_cash/distance if distance else 0
        notional=qty*advice.trigger_price
        max_notional=settings.equity*settings.leverage
    except OverflowError:
        risk_cash=qty=notional=max_notional=float("inf")
    if not all(math.isfinite(float(x)) for x in (distance,risk_cash,qty,notional,max_notional)):
        warnings.insert(0,"输入数值过大，无法安全测算参考仓位")
        return RiskEstimate(**base,stop_distance=distance if math.isfinite(distance) else None,warnings=warnings)
    if notional>max_notional: notional=max_notional; qty=notional/advice.trigger_price; warnings.insert(0,"仓位受计划杠杆上限约束")
    return RiskEstimate(**base,stop_distance=distance,reference_notional=notional,quantity_btc=qty,warnings=warnings)
