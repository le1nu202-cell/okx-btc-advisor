from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .backtest import run_backtest
from .db import Database
from .models import AdviceAction, BacktestRequest, BacktestStatus, Candle, MarketRegime, MarketSnapshot, Settings, SignalAdvice
from .news import NewsAggregator, aggregate_news
from .liquidation import DEFAULT_MARK_PRICE_MAX_AGE_MS, actual_liquidation_estimate, planned_liquidation_scenarios
from .market_analysis import MODEL_VERSION as MARKET_ANALYSIS_MODEL_VERSION
from .market_analysis import analysis_changed_significantly, build_market_analysis, compact_analysis_snapshot
from .market_validation import build_validation_report
from .okx import ANALYSIS_TIMEFRAMES, CANDLE_TIMEFRAMES, CHANNEL_TO_TIMEFRAME, CHART_TIMEFRAMES, OKXPublicClient
from .strategy import analyze, estimate_risk
from .technical import analyze_technical
from .trade_history import BulkDeleteTradeLogsRequest, encode_trade_log_csv, encode_trade_log_json
from .workbench import (
    TERMINAL_STATES,
    ReplayCreateRequest,
    ReplayPlanRequest,
    RiskCalculation,
    TradeActionRequest,
    TradePlanDraft,
    TradeState,
    apply_action,
    build_add_check,
    build_trade_log,
    calculate_risk,
    candle_mark_to_market,
    make_plan_record,
    new_id,
    normalize_live_execution_state,
    now_ms,
    price_trigger,
    recompute_execution,
    replay_candle_transition,
    replay_start_index,
    trade_statistics,
    visible_replay_candles,
)

INSTRUMENT="BTC-USDT-SWAP"
APP_ID="okx-btc-advisor"
APP_VERSION="0.6.0"
MAX_WEBSOCKET_CONNECTIONS=32
NEWS_POLL_SECONDS=300
NEWS_SOURCE_STALE_MS=15*60_000
PUBLIC_RISK_CACHE_KEY="okx-public-risk:BTC-USDT-SWAP:cross"
PUBLIC_RISK_REFRESH_MS=6*3600_000
PUBLIC_RISK_MAX_STALE_MS=7*86400_000
MARK_PRICE_STALE_MS=DEFAULT_MARK_PRICE_MAX_AGE_MS
LIQUIDATION_REFRESH_MIN_INTERVAL_MS=1_000
MARKET_ANALYSIS_INPUT_LIMIT=400
MARKET_ANALYSIS_VALIDATION_CACHE_MS=15*60_000
MARKET_ANALYSIS_VALIDATION_WINDOW=960
MARKET_ANALYSIS_VALIDATION_STRIDE=4
ROOT=Path(__file__).resolve().parents[1]
db=Database(os.getenv("OKX_ADVISOR_DB",str(Path(__file__).with_name("data")/"advisor.db")))
client=OKXPublicClient(base_url=os.getenv("OKX_BASE_URL","https://www.okx.com"),ws_url=os.getenv("OKX_WS_URL","wss://ws.okx.com:8443/ws/v5/public"),business_ws_url=os.getenv("OKX_BUSINESS_WS_URL","wss://ws.okx.com:8443/ws/v5/business"))
def _empty_news(message="新闻源正在连接"):
    return {"items":[],"analysis":{"asOf":None,"windowHours":48,"score":0,"articleCount":0,"status":"unavailable","sourceCoverage":0,"sourceStatus":[],"rawImpact":0,"coverageAdjustedImpact":0,"reason":"新闻数据不可用，严格按中性处理","warnings":[message]}}


runtime={"price":None,"ticker_ts":None,"mark_price":None,"mark_price_ts":None,"connection":"starting","stream_status":{"public":"starting","candles":"starting"},"candle_gaps":{timeframe:False for timeframe in CHART_TIMEFRAMES},"candle_gap_targets":{timeframe:None for timeframe in CHART_TIMEFRAMES},"stop":None,"task":None,"bootstrap_task":None,"resync_task":None,"reconcile_task":None,"news_task":None,"intraday_backfill_task":None,"risk_parameter_task":None,"backtest_tasks":set(),"news":_empty_news(),"news_source_status":[],"news_checked":False,"news_last_success":None,"plan_check_ts":0,"liquidation_refresh_ts":0,"market_analysis":None,"market_analysis_signature":None,"market_analysis_validation":None,"market_analysis_validation_key":None,"market_analysis_validation_at":0}
subscribers:set[WebSocket]=set()
pending_websockets=0
news_aggregator=NewsAggregator()
backtest_lock=asyncio.Lock()
trade_plan_lock=threading.RLock()
market_analysis_lock=threading.RLock()
market_validation_lock=threading.Lock()
backtest_executor:ProcessPoolExecutor|None=None


def _public_risk_context(now:int|None=None) -> dict|None:
    current=int(now or time.time()*1000)
    try:cached=db.get_public_market_cache(PUBLIC_RISK_CACHE_KEY)
    except (AttributeError,TypeError,ValueError):return None
    if not cached:return None
    updated=int(cached.get("updatedAt") or cached.get("observedAt") or 0)
    age=current-updated
    if updated<=0 or age< -60_000 or age>PUBLIC_RISK_MAX_STALE_MS:return None
    if not isinstance(cached.get("tiers"),list) or not cached.get("contractValueBtc"):return None
    return {**cached,"stale":age>PUBLIC_RISK_REFRESH_MS}


def _risk_with_liquidation(plan:TradePlanDraft,risk:RiskCalculation|dict,*,evaluated_at:int|None=None) -> dict:
    evaluation_time=int(evaluated_at if evaluated_at is not None else now_ms())
    payload=risk.model_dump(by_alias=True,mode="json") if isinstance(risk,RiskCalculation) else dict(risk)
    payload["liquidationScenarios"]=planned_liquidation_scenarios(
        plan.model_dump(by_alias=True,mode="json"),payload,
        mark_price=runtime.get("mark_price"),mark_price_time=runtime.get("mark_price_ts"),
        public_context=_public_risk_context(evaluation_time),evaluated_at=evaluation_time,
    )
    return payload


def _enrich_record_liquidation(record:dict,*,evaluated_at:int|None=None) -> dict:
    evaluation_time=int(evaluated_at if evaluated_at is not None else now_ms())
    updated=dict(record)
    plan=TradePlanDraft.model_validate(updated["plan"])
    risk=RiskCalculation.model_validate(updated["risk"])
    updated["risk"]=_risk_with_liquidation(plan,risk,evaluated_at=evaluation_time)
    execution_risk=dict(updated.get("executionRisk") or {})
    execution_risk["liquidationEstimate"]=actual_liquidation_estimate(
        updated,mark_price=runtime.get("mark_price"),mark_price_time=runtime.get("mark_price_ts"),
        public_context=_public_risk_context(evaluation_time),evaluated_at=evaluation_time,
    )
    updated["executionRisk"]=execution_risk
    return updated


def _backtest_process_pool()->ProcessPoolExecutor:
    global backtest_executor
    if backtest_executor is None:backtest_executor=ProcessPoolExecutor(max_workers=1)
    return backtest_executor


async def run_backtest_isolated(c1,c4,funding,strategy,fee_bps,slippage_bps):
    loop=asyncio.get_running_loop()
    return await loop.run_in_executor(_backtest_process_pool(),run_backtest,c1,c4,funding,strategy,fee_bps,slippage_bps)


def _epoch_ms(value):
    if value is None:return None
    if isinstance(value,(int,float)):return int(value if value>10_000_000_000 else value*1000)
    return int(datetime.fromisoformat(str(value).replace("Z","+00:00")).timestamp()*1000)


def _history_since_ms(now_ms:int,years:int)->int:
    """Calendar-year lookback plus a small warm-up/alignment buffer."""
    current=datetime.fromtimestamp(now_ms/1000,timezone.utc)
    try:target=current.replace(year=current.year-years)
    except ValueError:target=current.replace(year=current.year-years,month=2,day=28)
    return int((target-timedelta(days=7)).timestamp()*1000)


def _backtest_horizon_coverage(c1:list,c4:list,requested_start:int,requested_end:int)->dict:
    first_1h=c1[0].timestamp if c1 else None;last_1h=c1[-1].timestamp+3600_000 if c1 else None
    first_4h=c4[0].timestamp if c4 else None;last_4h=c4[-1].timestamp+4*3600_000 if c4 else None
    duration=max(1,requested_end-requested_start)
    covered_start=max(value for value in (first_1h,first_4h,requested_start) if value is not None) if c1 and c4 else requested_end
    covered_end=min(value for value in (last_1h,last_4h,requested_end) if value is not None) if c1 and c4 else requested_start
    fraction=max(0,min(1,(covered_end-covered_start)/duration))
    complete=bool(
        first_1h is not None and first_1h<=requested_start+3600_000 and last_1h is not None and last_1h>=requested_end-2*3600_000
        and first_4h is not None and first_4h<=requested_start+4*3600_000 and last_4h is not None and last_4h>=requested_end-4*3600_000
        and fraction>=.995
    )
    return {"requestedStart":requested_start,"requestedEnd":requested_end,"actualStart1H":first_1h,"actualEnd1H":last_1h,"actualStart4H":first_4h,"actualEnd4H":last_4h,"rows1H":len(c1),"rows4H":len(c4),"durationCoverage":fraction,"complete":complete}


def _public_news(result:dict)->dict:
    status="fresh" if result.get("status") in ("complete","fresh") else result.get("status","unavailable")
    categories={"security":"安全事件","exchangeRisk":"交易所风险","operations":"交易运行","fundFlow":"资金流","regulation":"监管政策","macro":"宏观流动性","adoption":"机构采用","opinion":"观点预测","general":"综合市场"}
    items=[]
    for row in result.get("clusters",[]):
        direction=int(row.get("direction",0));importance=int(row.get("importance",1))
        effective=float(row.get("effectiveImpact",0) or 0)
        items.append({"id":row.get("clusterId"),"title":row.get("title",""),"url":row.get("url",""),"source":row.get("source",""),"sources":row.get("sources",[]),"publishedAt":_epoch_ms(row.get("publishedAt")),"latestPublishedAt":_epoch_ms(row.get("latestPublishedAt")),"observedAt":_epoch_ms(row.get("observedAt")),"summary":f"{row.get('sourceCount',1)} 个独立来源；事件影响 {effective:+.3f}","category":categories.get(row.get("category"),row.get("category","综合市场")),"importance":importance,"importanceScore":row.get("importanceScore",importance*20),"importanceLabel":"重大" if importance>=5 else "高" if importance>=4 else "中" if importance>=3 else "低","sentiment":direction,"sentimentLabel":"利多" if direction>0 else "利空" if direction<0 else "中性","directionConfidence":row.get("directionConfidence"),"relevance":row.get("relevance",0),"reason":row.get("reason",""),"effectiveImpact":effective,"sourceCount":row.get("sourceCount"),"ageHours":row.get("ageHours"),"halfLifeHours":row.get("halfLifeHours"),"timeDecay":row.get("timeDecay"),"weightBreakdown":row.get("weightBreakdown",{}),"weightFormula":row.get("weightFormula","")})
    raw_statuses=result.get("sourceStatus",[])
    sources=[{"source":str(x.get("source","unknown")),"ok":bool(x.get("ok")),"itemCount":max(0,int(x.get("itemCount",0) or 0)),"observedAt":_epoch_ms(x.get("observedAt")),"error":str(x.get("error"))[:200] if x.get("error") else None} for x in raw_statuses if isinstance(x,dict)]
    warnings=[f"{x['source']}: {x['error']}" for x in sources if not x["ok"] and x["error"]]
    public_status=status if status in ("fresh","partial") else "unavailable"
    raw_score=result.get("newsScore",0)
    score=float(raw_score) if isinstance(raw_score,(int,float)) and math.isfinite(float(raw_score)) else 0
    score=0 if public_status=="unavailable" else max(-15,min(15,score))
    drivers=sorted(items,key=lambda x:(abs(x["effectiveImpact"]),x["importanceScore"] or 0),reverse=True)
    return {"items":items,"analysis":{"asOf":_epoch_ms(result.get("asOf")),"windowHours":result.get("windowHours",48),"score":score,"articleCount":result.get("clusterCount",len(items)),"topDriverIds":[x["id"] for x in drivers[:5] if x["effectiveImpact"]],"status":public_status,"sourceCoverage":result.get("sourceCoverage",0 if public_status=="unavailable" else 1),"sourceStatus":sources,"rawImpact":result.get("rawImpact",0),"coverageAdjustedImpact":result.get("coverageAdjustedImpact",0),"reason":result.get("reason",""),"warnings":warnings}}


def _news_sources_usable(now_ms:int|None=None)->tuple[bool,str]:
    if not runtime.get("news_checked"):
        return False,"新闻源尚未完成首次检查，新闻分暂不参与"
    statuses=runtime.get("news_source_status") or []
    if not any(row.get("ok") for row in statuses):
        return False,"新闻源当前全部不可用，新闻分已归零"
    last=runtime.get("news_last_success")
    if not isinstance(last,(int,float)) or not math.isfinite(float(last)):
        return False,"新闻来源成功时间缺失，新闻分已归零"
    age=max(0,(int(time.time()*1000) if now_ms is None else now_ms)-int(last))
    if age>NEWS_SOURCE_STALE_MS:
        return False,f"新闻来源状态已过期 {age//60_000} 分钟，新闻分已归零"
    return True,""


def _neutral_news_with_source_state(message:str)->dict:
    public=_empty_news(message)
    sources=[]
    for row in runtime.get("news_source_status") or []:
        if not isinstance(row,dict):continue
        sources.append({"source":str(row.get("source","unknown")),"ok":False,"itemCount":max(0,int(row.get("itemCount",0) or 0)),"observedAt":_epoch_ms(row.get("observedAt")),"error":str(row.get("error") or message)[:200]})
    public["analysis"]["sourceStatus"]=sources
    return public


def news_for_decision(decision_at:int,now_ms:int|None=None)->dict:
    # Source health is part of the point-in-time input. Reading the latest live
    # status here would let the same closed candle change after a later poll.
    statuses=db.news_source_status(decision_at,NEWS_SOURCE_STALE_MS)
    if not statuses:return _empty_news("决策时点没有可验证的新闻来源状态，新闻分已归零")
    rows=db.news_items(100,decision_at,decision_at-48*3600_000)
    result=aggregate_news(rows,decision_at,statuses)
    return _public_news(result)


def current_advice():
    now=int(time.time()*1000)
    c1=[row for row in db.candles(INSTRUMENT,"1H",400,True) if row.timestamp+3600_000<=now]
    c4=[row for row in db.candles(INSTRUMENT,"4H",260,True) if row.timestamp+4*3600_000<=now]
    decision_at=c1[-1].timestamp+3600_000 if c1 else None
    funding=db.funding_rows(INSTRUMENT,c1[-1].timestamp-90*86400_000 if c1 else 0)
    oi=db.open_interest_context(INSTRUMENT,as_of_ms=decision_at) if decision_at else None
    news=news_for_decision(c1[-1].timestamp+3600_000) if c1 else runtime["news"]
    advice=analyze(c1,c4,funding,oi,custom=bool(db.get_settings().custom_parameters),news_analysis={**news["analysis"],"items":news["items"][:5]})
    return _apply_live_market_gate(advice,now)


def _live_market_ready(now_ms:int|None=None)->tuple[bool,str]:
    now_ms=int(time.time()*1000) if now_ms is None else int(now_ms)
    ticker_ts=runtime.get("ticker_ts")
    if not isinstance(ticker_ts,(int,float)) or not math.isfinite(float(ticker_ts)):return False,"实时ticker尚未可用"
    if now_ms-int(ticker_ts)>30_000:return False,"实时ticker已超过30秒未更新"
    if runtime.get("connection")!="connected":return False,"必要的OKX公共行情通道未全部连接"
    return True,""


def _apply_live_market_gate(advice:SignalAdvice,now_ms:int|None=None)->SignalAdvice:
    healthy,reason=_live_market_ready(now_ms)
    if healthy or advice.action==AdviceAction.WAIT:return advice
    quality=advice.data_quality.model_copy(deep=True);quality.fresh=False
    if reason not in quality.warnings:quality.warnings.append(reason)
    return advice.model_copy(update={"strategy":"live_market_stale","regime":MarketRegime.STALE,"action":AdviceAction.WAIT,"direction_score":0,"technical_score":0,"news_score":0,"confidence":0,"contributions":[],"trigger_price":None,"stop_loss":None,"targets":[],"risk_reward":[],"invalidation":"等待实时ticker和公共行情通道恢复","explanation":"实时市场状态不完整，已阻止保存、广播和提醒新的交易候选。","data_quality":quality})


def _technical_summary_is_fresh(raw:dict,now_ms:int|None=None)->bool:
    as_of=raw.get("as_of");four_as_of=raw.get("four_hour_as_of")
    if not raw.get("ready") or not all(isinstance(value,(int,float)) and math.isfinite(float(value)) for value in (as_of,four_as_of)):return False
    age=(int(time.time()*1000) if now_ms is None else int(now_ms))-(int(as_of)+3600_000)
    four_age=(int(as_of)+3600_000)-(int(four_as_of)+4*3600_000)
    return 0<=age<=2*3600_000 and 0<=four_age<4*3600_000


def technical_summary():
    raw=analyze_technical(db.candles(INSTRUMENT,"1H",400,True),db.candles(INSTRUMENT,"4H",260,True))
    ratings=(raw.get("rating_1h",{}).get("label","—"),raw.get("rating_4h",{}).get("label","—"))
    labels={"trend":"趋势与多周期","structure":"市场结构","volume_price":"量价与资金流","momentum":"动量共振","volatility":"波动阶段"}
    groups=[]
    for key,label in labels.items():
        score=raw.get("group_scores",{}).get(key,0);cap=raw.get("group_caps",{}).get(key,0)
        groups.append({"key":key,"label":label,"score":score,"cap":cap,"rating1h":ratings[0],"rating4h":ratings[1],"summary":f"分组贡献 {score:+.1f} / {cap:.0f}","metrics":[{"label":"贡献分","value":f"{score:+.1f}","tone":"positive" if score>0 else "negative" if score<0 else "neutral"}]})
    levels=raw.get("levels",{});vp=levels.get("volume_profile",{});flow=raw.get("volume_price",{});momentum=raw.get("momentum",{});vol=raw.get("volatility",{})
    group_metrics={
        "trend":[{"label":"1H / 4H评级","value":f"{ratings[0]} / {ratings[1]}","tone":"neutral"}],
        "structure":[{"label":"支撑 / 阻力","value":f"{levels.get('donchian_support','—')} / {levels.get('donchian_resistance','—')}","tone":"neutral"}],
        "volume_price":[{"label":"日 / 周VWAP","value":f"{flow.get('daily_vwap','—')} / {flow.get('weekly_vwap','—')}","tone":"neutral"},{"label":"MFI","value":str(flow.get('mfi','—')),"tone":"neutral"}],
        "momentum":[{"label":"RSI / Stoch / %R","value":f"{momentum.get('rsi','—')} / {momentum.get('stoch_rsi','—')} / {momentum.get('williams_r','—')}","tone":"neutral"}],
        "volatility":[{"label":"ATR / BB分位","value":f"{vol.get('atr_percentile','—')}% / {vol.get('bollinger_width_percentile','—')}%","tone":"neutral"}],
    }
    for group in groups:group["metrics"].extend(group_metrics.get(group["key"],[]))
    technical_fresh=_technical_summary_is_fresh(raw)
    warnings=[*raw.get("warnings",[]),*raw.get("conflicts",[]),*raw.get("risk_overlay",{}).get("reasons",[])]
    if raw.get("ready") and not technical_fresh:warnings.append("辅助技术摘要所用1H或4H行情已过期")
    phase={"COMPRESSION":"波动压缩","NORMAL":"正常波动","EXPANSION":"波动扩张","EXTREME":"极端波动","UNKNOWN":"数据不足"}.get(vol.get("phase"),str(vol.get("phase","数据不足")))
    risk=raw.get("risk_overlay",{})
    return {"asOf":raw.get("as_of",0)+3600_000 if raw.get("as_of") else None,"status":"fresh" if technical_fresh else "unavailable","algorithmId":"aux-confluence-v2","technicalScore":raw.get("technical_score",0),"groups":groups,"vwap":flow.get("daily_vwap"),"weeklyVwap":flow.get("weekly_vwap"),"mfi":flow.get("mfi"),"volumeProfile":vp,"supportResistance":{"supports":[levels["donchian_support"]] if levels.get("donchian_support") is not None else [],"resistances":[levels["donchian_resistance"]] if levels.get("donchian_resistance") is not None else []},"momentum":f"RSI {momentum.get('rsi','—')} · Stoch RSI {momentum.get('stoch_rsi','—')} · Williams %R {momentum.get('williams_r','—')}","volatilityPhase":phase,"atrPercentile":vol.get("atr_percentile"),"bollingerWidthPercentile":vol.get("bollinger_width_percentile"),"volatility":vol,"positionScale":risk.get("position_scale"),"riskReasons":risk.get("reasons",[]),"riskOverlay":risk,"warnings":warnings}


def _confirmed_candle_fingerprint(timeframe:str) -> tuple|None:
    rows=db.candles(INSTRUMENT,timeframe,1,confirmed_only=True)
    if not rows:return None
    row=rows[-1]
    return (row.timestamp,row.open,row.high,row.low,row.close,row.volume,row.volume_ccy)


def _market_analysis_signature(now_ms:int|None=None) -> tuple:
    current=int(time.time()*1000) if now_ms is None else int(now_ms)
    fingerprints=[];lagging=[]
    for timeframe in CHART_TIMEFRAMES:
        fingerprint=_confirmed_candle_fingerprint(timeframe)
        fingerprints.append(fingerprint)
        interval=int(CANDLE_TIMEFRAMES[timeframe]["durationMs"])
        expected_latest=(current//interval)*interval-interval
        # A slightly early exchange-confirmed row is still ineligible until
        # its real close boundary.  ``!=`` makes that future-to-eligible clock
        # transition invalidate the cache as well as a genuinely missing row.
        lagging.append(fingerprint is None or int(fingerprint[0])!=expected_latest)
    gaps=tuple(bool(runtime.get("candle_gaps",{}).get(timeframe)) for timeframe in CHART_TIMEFRAMES)
    return (*fingerprints,*lagging,str(runtime.get("connection") or "starting"),*gaps)


def _market_validation_signature() -> tuple:
    """Invalidate on every confirmed-corpus revision relevant to a decision.

    One-minute candles after the latest completed 15m decision cannot change
    an already reconstructed snapshot, so they are excluded until the next
    15m close. Older 1m corrections/backfills and any 15m/1H/4H corpus change
    do invalidate the report.
    """
    latest_fifteen=_confirmed_candle_fingerprint("15m")
    one_minute_through=(int(latest_fifteen[0])+int(CANDLE_TIMEFRAMES["15m"]["durationMs"])-int(CANDLE_TIMEFRAMES["1m"]["durationMs"])) if latest_fifteen else -1
    fingerprints=[db.candle_series_signature(INSTRUMENT,"1m",confirmed_only=True,through_ts=one_minute_through)]
    for timeframe in ("15m","1H","4H"):
        fingerprints.append(db.candle_series_signature(INSTRUMENT,timeframe,confirmed_only=True))
    return tuple(fingerprints)


def current_market_analysis(*,force:bool=False) -> dict:
    """Build one deterministic v0.6 view from point-in-time closed candles."""
    with market_analysis_lock:
        signature=_market_analysis_signature()
        cached=runtime.get("market_analysis")
        if not force and cached is not None and signature==runtime.get("market_analysis_signature"):
            return cached
        decision_at=int(time.time()*1000)
        candles={
            timeframe:db.candles(INSTRUMENT,timeframe,MARKET_ANALYSIS_INPUT_LIMIT)
            for timeframe in CHART_TIMEFRAMES
        }
        analysis=build_market_analysis(
            candles,
            decision_at=decision_at,
            connection_status=str(runtime.get("connection") or "starting"),
            gap_status={timeframe:bool(runtime.get("candle_gaps",{}).get(timeframe)) for timeframe in CHART_TIMEFRAMES},
            include_series=True,
        )
        runtime["market_analysis"]=analysis
        runtime["market_analysis_signature"]=signature
        return analysis


def _persist_market_analysis(analysis:dict,trigger:str) -> bool:
    if not analysis.get("asOf") or analysis.get("compositeScore") is None:return False
    compact=compact_analysis_snapshot(analysis)
    compact["snapshotTrigger"]=trigger
    return db.save_market_analysis_snapshot(compact,"LIVE_OBSERVED")


async def publish_market_analysis(*,trigger:str,persist:bool=False,force:bool=True) -> dict:
    previous=runtime.get("market_analysis")
    analysis=current_market_analysis(force=force)
    quality=str((analysis.get("dataQuality") or {}).get("status") or "UNAVAILABLE")
    significant=bool(previous and analysis_changed_significantly(previous,analysis))
    persisted=False
    if (persist or significant) and quality not in {"UNAVAILABLE","STALE"}:
        try:persisted=_persist_market_analysis(analysis,trigger)
        except (TypeError,ValueError,ArithmeticError):pass
    await broadcast({"type":"marketAnalysis","analysis":analysis,"trigger":trigger,"persisted":persisted})
    return analysis


async def broadcast(payload:dict):
    dead=[]
    for ws in list(subscribers):
        try:await ws.send_json(payload)
        except Exception:dead.append(ws)
    for ws in dead:subscribers.discard(ws)


def _save_terminal_trade_log(record:dict,source:str="live") -> dict|None:
    state=TradeState(record["state"])
    if state not in {TradeState.TAKE_PROFIT,TradeState.STOPPED}:return None
    if record.get("logId"):
        existing=[row for row in db.trade_logs(source,500) if row.get("id")==record["logId"]]
        return existing[0] if existing else None
    # A live plan has exactly one terminal log. A deterministic key protects
    # the single-process application from duplicate writes even if a client
    # retries the same confirmation while the first response is in flight.
    record["logId"]=f"live:{record['id']}" if source=="live" else new_id()
    if source=="replay":regime=str(record.get("regime4H") or "UNKNOWN")
    else:
        try:regime=current_advice().regime.value
        except Exception:regime="UNKNOWN"
    if source=="live":
        enriched=_enrich_record_liquidation(record)
    else:
        enriched=recompute_execution(record)
        replay_risk=dict(enriched.get("executionRisk") or {})
        replay_risk["liquidationEstimate"]=actual_liquidation_estimate(
            enriched,mark_price=None,mark_price_time=None,public_context=_public_risk_context(),
        )
        enriched["executionRisk"]=replay_risk
    log=build_trade_log(enriched,source,regime)
    # build_trade_log intentionally rebuilds the frozen execution accounting;
    # restore the independent liquidation post-processing projection.
    log["executionRisk"]=enriched["executionRisk"]
    log["id"]=record["logId"]
    db.save_trade_plan(record,active=True) if source=="live" else None
    db.save_trade_log(log,source)
    return log


async def evaluate_active_plan_price(price:float|None) -> None:
    if price is None:return
    now=int(time.time()*1000)
    if now-int(runtime.get("plan_check_ts") or 0)<1000:return
    runtime["plan_check_ts"]=now
    alert=None;plan_update=None
    try:
        with trade_plan_lock:
            stored_record=db.get_active_trade_plan()
            if not stored_record:return
            record=normalize_live_execution_state(stored_record)
            state=TradeState(record["state"])
            if state in TERMINAL_STATES:return
            fresh,_=_live_market_ready(now)
            before_state=record["state"]
            updated,event,message=price_trigger(record,float(price),fresh)
            execution=recompute_execution(updated).get("execution",{})
            if fresh and state not in {TradeState.PLANNED,TradeState.IDLE} and execution.get("mfeMaeSupported") and execution.get("openedQuantityBtc",0)>0:
                mark=Candle(timestamp=now,open=float(price),high=float(price),low=float(price),close=float(price),volume=0,timeframe="live",confirm=False)
                favorable,adverse=candle_mark_to_market(updated,mark)
                updated["mfeUsdt"]=max(float(updated.get("mfeUsdt") or 0),favorable)
                updated["maeUsdt"]=max(float(updated.get("maeUsdt") or 0),adverse)
            updated=_enrich_record_liquidation(recompute_execution(updated),evaluated_at=now)
            changed=record!=stored_record or updated!=record
            if changed:db.save_trade_plan(updated,active=True)
            if event:
                db.save_trade_plan_event(updated["id"],before_state,updated["state"],event,float(price),{"message":message})
                alert={"type":"tradePlanAlert","event":event,"message":message,"plan":updated,"log":None}
            elif changed:
                plan_update={"type":"tradePlanUpdate","reason":"TICKER_REFRESH","plan":updated}
    except (TypeError,ValueError,ArithmeticError):
        # A damaged local plan must never break public market ingestion.
        return
    if alert:await broadcast(alert)
    elif plan_update:await broadcast(plan_update)


async def refresh_active_plan_liquidation(*,reason:str="MARK_PRICE",evaluated_at:int|None=None,force:bool=False) -> dict|None:
    """Persist and publish backend-authoritative mark-relative risk fields.

    OKX mark-price can update several times per second.  The latest value is
    always retained in runtime, while this derived plan projection is limited
    to one save/broadcast per second.
    """
    evaluation_time=int(evaluated_at if evaluated_at is not None else now_ms())
    last=int(runtime.get("liquidation_refresh_ts") or 0)
    if not force and evaluation_time-last<LIQUIDATION_REFRESH_MIN_INTERVAL_MS:return None
    runtime["liquidation_refresh_ts"]=evaluation_time
    try:
        with trade_plan_lock:
            stored=db.get_active_trade_plan()
            if not stored:return None
            normalized=normalize_live_execution_state(stored)
            updated=_enrich_record_liquidation(recompute_execution(normalized),evaluated_at=evaluation_time)
            if updated!=stored:db.save_trade_plan(updated,active=True)
    except (TypeError,ValueError,ArithmeticError):
        return None
    await broadcast({"type":"tradePlanUpdate","reason":reason,"plan":updated})
    return updated


async def publish_current_signal():
    advice=current_advice()
    if advice.action.value=="WAIT":return False
    inserted=db.save_signal(advice)
    if inserted:await broadcast({"type":"signal","advice":advice.model_dump(by_alias=True,mode="json")})
    return inserted


def update_stream_status(stream_name:str|None,status:str)->str:
    if not stream_name:
        runtime["connection"]=status
        return status
    statuses=runtime.setdefault("stream_status",{"public":"starting","candles":"starting"})
    statuses[stream_name]=status
    values=set(statuses.values())
    if values=={"connected"}:overall="connected"
    elif "connected" in values:overall="degraded"
    elif "reconnecting" in values:overall="reconnecting"
    else:overall="starting"
    runtime["connection"]=overall
    return overall


def _remember_candle_gap(timeframe:str,start:int,end:int) -> None:
    """Keep the exact missing interval until persisted confirmed rows cover it."""
    if timeframe not in CANDLE_TIMEFRAMES or end<=start:return
    targets=runtime.setdefault("candle_gap_targets",{tf:None for tf in CHART_TIMEFRAMES})
    current=targets.get(timeframe)
    if current:
        start=min(int(start),int(current[0]));end=max(int(end),int(current[1]))
    targets[timeframe]=(int(start),int(end))
    runtime.setdefault("candle_gaps",{})[timeframe]=True


def _internal_candle_gaps(rows:list[Candle],timeframe:str) -> list[tuple[int,int]]:
    step=int(CANDLE_TIMEFRAMES[timeframe]["durationMs"])
    timestamps=sorted({int(row.timestamp) for row in rows if row.confirm and row.timeframe==timeframe})
    return [(left,right) for left,right in zip(timestamps,timestamps[1:]) if right-left!=step]


def _refresh_candle_gap_status(timeframe:str,received:list[Candle]|None=None) -> bool:
    """Validate merged, recently closed candles instead of trusting REST success.

    A detected boundary is retained separately from the boolean UI status. A
    later 300-row response therefore cannot hide a still-uncovered older
    boundary merely by pushing its left endpoint out of the recent query.
    """
    received=list(received or [])
    for start,end in _internal_candle_gaps(received,timeframe):
        _remember_candle_gap(timeframe,start,end)

    # Include one more row than the largest REST page so the merge boundary is
    # visible when a response contains a full 300 rows.
    persisted=db.candles(INSTRUMENT,timeframe,301)
    merged={row.timestamp:row for row in persisted if row.confirm and row.timeframe==timeframe}
    for row in received:
        if row.confirm and row.timeframe==timeframe:merged[row.timestamp]=row
    recent=[merged[timestamp] for timestamp in sorted(merged)]
    for start,end in _internal_candle_gaps(recent,timeframe):
        _remember_candle_gap(timeframe,start,end)

    targets=runtime.setdefault("candle_gap_targets",{tf:None for tf in CHART_TIMEFRAMES})
    target=targets.get(timeframe)
    if target:
        start,end=int(target[0]),int(target[1])
        segment={row.timestamp:row for row in db.candles_since(INSTRUMENT,timeframe,start,confirmed_only=True) if row.timestamp<=end}
        for row in received:
            if row.confirm and row.timeframe==timeframe and start<=row.timestamp<=end:segment[row.timestamp]=row
        ordered=[segment[timestamp] for timestamp in sorted(segment)]
        covered=(
            bool(ordered) and ordered[0].timestamp==start and ordered[-1].timestamp==end
            and not _internal_candle_gaps(ordered,timeframe)
        )
        if not covered:
            runtime["candle_gaps"][timeframe]=True
            return True
        targets[timeframe]=None

    # One successful row is not evidence that a previously reported gap was
    # repaired. Two or more contiguous closed rows can clear a target-less
    # transient error; an empty/unclosed response leaves the prior status alone.
    if len(recent)>=2 and any(row.confirm and row.timeframe==timeframe for row in received):
        runtime["candle_gaps"][timeframe]=False
    return bool(runtime["candle_gaps"].get(timeframe))


async def on_okx(msg:dict):
    stream_name=msg.get("stream") or msg.get("_stream")
    if msg.get("event")=="reconnecting":
        update_stream_status(stream_name,"reconnecting")
        await broadcast({"type":"market","price":runtime["price"],"tickerTime":runtime["ticker_ts"],"connectionStatus":runtime["connection"]})
        try:await publish_market_analysis(trigger="CONNECTION_DEGRADED",persist=False)
        except Exception:pass
        return
    if msg.get("event")=="message_error":
        return
    if msg.get("event")=="reconnected":
        update_stream_status(stream_name,"connected")
        # Fill any gap accumulated while the public stream was disconnected.
        if stream_name in (None,"candles"):
            await resync_market_history()
            try:await publish_current_signal()
            except Exception:pass
            try:await publish_market_analysis(trigger="CONNECTION_RESTORED",persist=False)
            except Exception:pass
        return
    channel=msg.get("arg",{}).get("channel",""); data=msg.get("data",[]);valid=False;mark_updated=False
    if channel=="tickers" and data:
        parsed=client.parse_ticker(data[0])
        if parsed:
            valid=True
            if runtime["ticker_ts"] is None or parsed[1]>=runtime["ticker_ts"]:runtime["price"],runtime["ticker_ts"]=parsed
    elif channel=="mark-price" and data:
        parsed=client.parse_mark_price(data[0])
        if parsed:
            valid=True
            if runtime["mark_price_ts"] is None or parsed[1]>=runtime["mark_price_ts"]:
                runtime["mark_price"],runtime["mark_price_ts"]=parsed;mark_updated=True
    elif channel in CHANNEL_TO_TIMEFRAME:
        tf=CHANNEL_TO_TIMEFRAME[channel]; rows=client.parse_candles(data,tf)
        published_rows=list(rows)
        previous=[row for row in db.candles(INSTRUMENT,tf,5) if row.confirm]
        received_confirmed=[row for row in rows if row.confirm]
        newest_before=max((row.timestamp for row in previous),default=None)
        newest_received=max((row.timestamp for row in received_confirmed),default=None)
        confirmed_advanced=newest_received is not None and (newest_before is None or newest_received>newest_before)
        step=int(CANDLE_TIMEFRAMES[tf]["durationMs"])
        if previous and received_confirmed and min(row.timestamp for row in received_confirmed)>previous[-1].timestamp+step:
            _remember_candle_gap(tf,previous[-1].timestamp,min(row.timestamp for row in received_confirmed))
            try:
                repaired=await client.backfill_candles(INSTRUMENT,tf,previous[-1].timestamp)
                retention=CANDLE_TIMEFRAMES[tf].get("retentionMs")
                db.upsert_candles(INSTRUMENT,repaired,int(time.time()*1000)-int(retention) if retention else None)
                published_rows=[*repaired,*rows]
            except Exception:pass
        retention=CANDLE_TIMEFRAMES[tf].get("retentionMs")
        db.upsert_candles(INSTRUMENT,rows,int(time.time()*1000)-int(retention) if retention else None)
        _refresh_candle_gap_status(tf,published_rows)
        if published_rows:
            await broadcast({
                "type":"candle",
                "timeframe":tf,
                "candles":[row.model_dump(by_alias=True,mode="json") for row in published_rows],
                "gapDetected":bool(runtime["candle_gaps"].get(tf)),
            })
        if any(row.confirm for row in rows):
            if tf in ANALYSIS_TIMEFRAMES:await publish_current_signal()
            try:await publish_market_analysis(trigger=f"{tf}_CLOSE",persist=confirmed_advanced and tf in {"15m","1H","4H"})
            except Exception:pass
        valid=bool(rows)
    elif channel=="funding-rate" and data:
        rows=client.parse_funding_rows(data,max_future_ms=48*3600_000);db.upsert_funding(INSTRUMENT,rows);valid=bool(rows)
    elif channel=="open-interest" and data:
        parsed=client.parse_open_interest(data[0])
        if parsed:db.upsert_oi(INSTRUMENT,*parsed);valid=True
    if valid:update_stream_status(stream_name,"connected")
    await broadcast({"type":"market","price":runtime["price"],"tickerTime":runtime["ticker_ts"],"markPrice":runtime["mark_price"],"markPriceTime":runtime["mark_price_ts"],"connectionStatus":runtime["connection"]})
    if mark_updated:
        await refresh_active_plan_liquidation(reason="MARK_PRICE")
    if channel=="tickers" and valid:
        await evaluate_active_plan_price(runtime["price"])


async def resync_market_history()->bool:
    """Restore the complete live-analysis warm-up set without starting another WS."""
    errors=[]
    for tf in CHART_TIMEFRAMES:
        try:
            rows=await client.candles(INSTRUMENT,tf,300,history=True)
            retention=CANDLE_TIMEFRAMES[tf].get("retentionMs")
            db.upsert_candles(INSTRUMENT,rows,int(time.time()*1000)-int(retention) if retention else None)
            if not any(row.confirm for row in rows):runtime["candle_gaps"][tf]=True
            gap_detected=_refresh_candle_gap_status(tf,rows)
            if rows:
                await broadcast({
                    "type":"candle","timeframe":tf,
                    "candles":[row.model_dump(by_alias=True,mode="json") for row in rows],
                    "gapDetected":gap_detected,
                })
        except Exception as exc:
            runtime["candle_gaps"][tf]=True
            errors.append(f"{tf}:{type(exc).__name__}")
    try:db.upsert_funding(INSTRUMENT,await client.backfill_funding_history(INSTRUMENT,int(time.time()*1000)-91*86400_000))
    except Exception as exc:errors.append(f"funding:{type(exc).__name__}")
    try:
        oi=await client.open_interest(INSTRUMENT)
        if oi:db.upsert_oi(INSTRUMENT,*oi)
    except Exception as exc:errors.append(f"oi:{type(exc).__name__}")
    try:
        mark=await client.mark_price(INSTRUMENT)
        if mark:
            runtime["mark_price"],runtime["mark_price_ts"]=mark
            await refresh_active_plan_liquidation(reason="MARK_PRICE_RESYNC",force=True)
    except Exception as exc:errors.append(f"mark:{type(exc).__name__}")
    try:
        tick=await client.ticker(INSTRUMENT)
        if tick:runtime["price"],runtime["ticker_ts"]=tick
    except Exception as exc:errors.append(f"ticker:{type(exc).__name__}")
    if errors:runtime["connection"]="degraded: resync"
    return not errors


async def bootstrap():
    await resync_market_history()
    runtime["stop"]=asyncio.Event(); runtime["task"]=asyncio.create_task(client.stream(on_okx,runtime["stop"]))
    runtime["intraday_backfill_task"]=asyncio.create_task(backfill_intraday_history())


async def backfill_intraday_history()->bool:
    """Fill chart retention windows after WS startup so live data is not delayed."""
    complete=True
    now=int(time.time()*1000)
    for tf in ("1m","15m"):
        retention=int(CANDLE_TIMEFRAMES[tf]["retentionMs"])
        cutoff=now-retention
        try:
            rows=await client.backfill_candles(INSTRUMENT,tf,cutoff)
            db.upsert_candles(INSTRUMENT,rows,cutoff)
            if not any(row.confirm for row in rows):runtime["candle_gaps"][tf]=True
            if _refresh_candle_gap_status(tf,rows):complete=False
        except Exception:
            runtime["candle_gaps"][tf]=True;complete=False
    return complete


async def reconcile_market_once()->bool:
    """Repair silent per-channel WS stalls using the public current-candle REST view."""
    refreshed=False;analysis_refreshed=False;analysis_persist=False
    for tf in CHART_TIMEFRAMES:
        try:
            previous=[row for row in db.candles(INSTRUMENT,tf,5) if row.confirm]
            rows=await client.candles(INSTRUMENT,tf,100,history=False)
            received_confirmed=[row for row in rows if row.confirm]
            step=int(CANDLE_TIMEFRAMES[tf]["durationMs"])
            if previous and received_confirmed and min(row.timestamp for row in received_confirmed)>previous[-1].timestamp+step:
                _remember_candle_gap(tf,previous[-1].timestamp,min(row.timestamp for row in received_confirmed))
                rows=await client.candles(INSTRUMENT,tf,300,history=True)
            retention=CANDLE_TIMEFRAMES[tf].get("retentionMs")
            if rows:
                newest_before=max((row.timestamp for row in previous),default=None)
                newest_received=max((row.timestamp for row in received_confirmed),default=None)
                confirmed_advanced=newest_received is not None and (newest_before is None or newest_received>newest_before)
                db.upsert_candles(INSTRUMENT,rows,int(time.time()*1000)-int(retention) if retention else None)
                gap_detected=_refresh_candle_gap_status(tf,rows);refreshed=True
                await broadcast({
                    "type":"candle","timeframe":tf,
                    "candles":[row.model_dump(by_alias=True,mode="json") for row in rows],
                    "gapDetected":gap_detected,
                })
                if tf in ANALYSIS_TIMEFRAMES:analysis_refreshed=True
                if confirmed_advanced and tf in {"15m","1H","4H"}:analysis_persist=True
        except Exception:runtime["candle_gaps"][tf]=True
    if analysis_refreshed:
        try:await publish_current_signal()
        except Exception:pass
    if refreshed:
        try:await publish_market_analysis(trigger="REST_RECONCILE",persist=analysis_persist)
        except Exception:pass
    return refreshed


async def market_reconcile_loop():
    while True:
        await asyncio.sleep(300)
        await reconcile_market_once()


async def refresh_public_risk_parameters()->bool:
    """Atomically cache only validated public cross-margin contract data."""
    try:
        metadata,tiers=await asyncio.gather(
            client.instrument_metadata(INSTRUMENT),
            client.position_tiers("BTC-USDT","cross"),
        )
        updated=int(time.time()*1000)
        db.put_public_market_cache(PUBLIC_RISK_CACHE_KEY,{**metadata,"tiers":tiers,"updatedAt":updated},updated)
        return True
    except Exception:
        return False


async def public_risk_parameter_loop():
    while True:
        if await refresh_public_risk_parameters():
            await broadcast({"type":"riskParameters","updatedAt":now_ms()})
        await asyncio.sleep(PUBLIC_RISK_REFRESH_MS/1000)


async def news_loop():
    while True:
        try:
            raw=await news_aggregator.fetch();runtime["news_source_status"]=raw.get("sourceStatus",[]);runtime["news_checked"]=True
            if any(row.get("ok") for row in runtime["news_source_status"]):runtime["news_last_success"]=int(time.time()*1000)
            db.save_news_source_status(runtime["news_source_status"])
            db.upsert_news(raw.get("rawItems",[]))
            # The dedicated panel shows the latest observable news window. Strategy advice
            # separately calls news_for_decision(candle_close) so later headlines can never
            # leak into an already-closed signal.
            runtime["news"]=_public_news(raw)
            public=runtime["news"]
            await broadcast({"type":"news","analysis":public["analysis"]})
        except Exception as exc:
            message=f"新闻聚合暂不可用：{type(exc).__name__}"
            runtime["news_checked"]=True
            runtime["news_source_status"]=[{"source":"aggregator","ok":False,"error":message}]
            runtime["news"]=_empty_news(message)
            await broadcast({"type":"news","analysis":runtime["news"]["analysis"]})
        await asyncio.sleep(NEWS_POLL_SECONDS)


@asynccontextmanager
async def lifespan(app:FastAPI):
    global backtest_executor
    db.interrupt_incomplete_backtests()
    if os.getenv("OKX_DISABLE_NETWORK")!="1":
        runtime["bootstrap_task"]=asyncio.create_task(bootstrap())
        runtime["reconcile_task"]=asyncio.create_task(market_reconcile_loop())
        runtime["risk_parameter_task"]=asyncio.create_task(public_risk_parameter_loop())
        runtime["news_task"]=asyncio.create_task(news_loop())
    yield
    if runtime["stop"]:runtime["stop"].set()
    tasks=[x for x in (runtime["task"],runtime["bootstrap_task"],runtime["resync_task"],runtime["reconcile_task"],runtime["intraday_backfill_task"],runtime["risk_parameter_task"],runtime["news_task"],*runtime["backtest_tasks"]) if x]
    for task in tasks:task.cancel()
    if tasks:await asyncio.gather(*tasks,return_exceptions=True)
    if backtest_executor is not None:
        backtest_executor.shutdown(wait=False,cancel_futures=True);backtest_executor=None


def _local_hostname(value:str|None,*,allow_testserver:bool=False)->bool:
    if not value:return False
    try:
        parsed=urlsplit(value if "://" in value else f"//{value}")
        host=(parsed.hostname or "").lower()
        parsed.port
    except ValueError:return False
    if parsed.username is not None or parsed.password is not None:return False
    if parsed.path not in ("","/") or parsed.query or parsed.fragment:return False
    if host=="testserver":return allow_testserver
    if host=="localhost":return True
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return False


def _origin_allowed(value:str|None,request_host:str|None)->bool:
    if not value:return False
    try:
        parsed=urlsplit(value)
        host_parts=urlsplit(f"//{request_host or ''}")
        origin_port=parsed.port or (443 if parsed.scheme=="https" else 80)
        request_port=host_parts.port or (443 if parsed.scheme=="https" else 80)
    except ValueError:return False
    if (
        parsed.scheme not in {"http","https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("","/")
        or parsed.query
        or parsed.fragment
        or not _local_hostname(value)
    ):return False
    normalized=f"{parsed.scheme}://{(parsed.hostname or '').lower()}:{origin_port}"
    if normalized in {"http://127.0.0.1:5173","http://localhost:5173"}:return True
    return (
        (parsed.hostname or "").lower()==(host_parts.hostname or "").lower()
        and origin_port==request_port
        and host_parts.username is None
        and host_parts.password is None
    )


class LoopbackHostMiddleware:
    """Reject DNS-rebinding Host values while retaining IPv4/IPv6 loopback support."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope["type"] not in {"http","websocket"}:
            await self.app(scope,receive,send);return
        host=next((value.decode("latin-1") for key,value in scope.get("headers",[]) if key.lower()==b"host"),"")
        allow_testserver=os.getenv("OKX_TEST_MODE")=="1"
        if _local_hostname(host,allow_testserver=allow_testserver):
            await self.app(scope,receive,send);return
        if scope["type"]=="websocket":
            await send({"type":"websocket.close","code":1008,"reason":"Invalid host"})
        else:
            await PlainTextResponse("Invalid host header",status_code=400)(scope,receive,send)


app=FastAPI(title="OKX BTC Advisor",version=APP_VERSION,lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["http://127.0.0.1:5173","http://localhost:5173"],allow_methods=["GET","PUT","POST","DELETE"],allow_headers=["*"])
app.add_middleware(LoopbackHostMiddleware)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request,exc:RequestValidationError):
    """Keep rejected non-finite JSON numbers out of the error response itself."""
    def safe(value):
        if isinstance(value,float) and not math.isfinite(value):return "非有限数"
        if isinstance(value,dict):return {str(key):safe(item) for key,item in value.items()}
        if isinstance(value,(list,tuple)):return [safe(item) for item in value]
        return value
    return JSONResponse(status_code=422,content={"detail":safe(jsonable_encoder(exc.errors()))})


@app.middleware("http")
async def security_headers(request,call_next):
    response=await call_next(request)
    response.headers["Content-Security-Policy"]=(
        "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self' ws://127.0.0.1:* ws://localhost:* ws://[::1]:*"
    )
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"]="no-store"
    elif request.url.path.startswith("/assets/") and response.status_code==200:
        response.headers["Cache-Control"]="public, max-age=31536000, immutable"
    else:
        # The HTML shell must be revalidated so a desktop shortcut cannot keep
        # loading an obsolete bundle after an upgrade.
        response.headers["Cache-Control"]="no-cache"
    return response


@app.get("/api/health")
def health():return {"status":"ok","appId":APP_ID,"version":APP_VERSION,"instrument":INSTRUMENT,"connectionStatus":runtime["connection"]}


@app.get("/api/market/snapshot",response_model=MarketSnapshot,response_model_by_alias=True)
def snapshot():
    c1m=db.candles(INSTRUMENT,"1m",720);c15m=db.candles(INSTRUMENT,"15m",672)
    c1=db.candles(INSTRUMENT,"1H",200);c4=db.candles(INSTRUMENT,"4H",200);f=db.latest_funding(INSTRUMENT);oi=db.latest_oi(INSTRUMENT)
    now=int(time.time()*1000)
    latest_confirmed=next((candle for candle in reversed(c1) if candle.confirm),None)
    last_close=latest_confirmed.timestamp+3600_000 if latest_confirmed else 0
    live_ready,_=_live_market_ready(now)
    stale=not last_close or now-last_close>2*3600_000 or not live_ready
    # A future scheduled fundingTime is not an observation timestamp.
    # Price freshness must not be masked by a newer auxiliary OI observation;
    # open interest already has its own explicit timestamp in the response.
    market_ts=max([x for x in (runtime["ticker_ts"],last_close) if x is not None],default=0)
    updated=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(market_ts/1000)) if market_ts else ""
    series={"1m":c1m,"15m":c15m,"1H":c1,"4H":c4}
    candle_status={}
    for timeframe,rows in series.items():
        duration=int(CANDLE_TIMEFRAMES[timeframe]["durationMs"])
        last_at=rows[-1].timestamp if rows else None
        last_confirmed=next((row for row in reversed(rows) if row.confirm),None)
        last_confirmed_at=last_confirmed.timestamp if last_confirmed else None
        confirmed_stale=not last_confirmed_at or now-(last_confirmed_at+duration)>2*duration
        candle_status[timeframe]={
            "available":bool(rows),
            "stale":not last_at or now-(last_at+duration)>2*duration,
            "lastAt":last_at,
            "lastConfirmedAt":last_confirmed_at,
            "confirmedStale":confirmed_stale,
            "gapDetected":bool(runtime.get("candle_gaps",{}).get(timeframe)),
        }
    return MarketSnapshot(
        instrument=INSTRUMENT,price=runtime["price"] or (c1[-1].close if c1 else None),updated_at=updated,
        stale=stale,candles_1m=c1m,candles_15m=c15m,candles_1h=c1,candles_4h=c4,
        mark_price=runtime.get("mark_price"),mark_price_time=runtime.get("mark_price_ts"),candle_status=candle_status,
        funding_rate=f[1] if f else None,funding_time=f[0] if f else None,
        open_interest=oi[1] if oi else None,open_interest_time=oi[0] if oi else None,
        connection_status=runtime["connection"],
    )


@app.get("/api/advice/current")
def advice_current():
    a=current_advice(); return {"advice":a.model_dump(by_alias=True,mode="json"),"riskEstimate":estimate_risk(a,db.get_settings()).model_dump(by_alias=True,mode="json")}


@app.get("/api/advice/history")
def advice_history(limit:int=Query(50,ge=1,le=500)):return {"items":db.signal_history(limit)}


@app.get("/api/technical/summary")
def technical_get():return technical_summary()


@app.get("/api/market-analysis/current")
def market_analysis_get():
    return current_market_analysis()


@app.get("/api/market-analysis/history")
def market_analysis_history(limit:int=Query(100,ge=1,le=500)):
    items=db.market_analysis_history(INSTRUMENT,MARKET_ANALYSIS_MODEL_VERSION,limit,"LIVE_OBSERVED")
    return {"items":items,"retentionDays":180,"modelVersion":MARKET_ANALYSIS_MODEL_VERSION}


@app.get("/api/market-analysis/validation")
def market_analysis_validation():
    now=int(time.time()*1000)
    key=_market_validation_signature()
    cached=runtime.get("market_analysis_validation")
    cached_at=int(runtime.get("market_analysis_validation_at") or 0)
    if cached is not None and key==runtime.get("market_analysis_validation_key") and now-cached_at<MARKET_ANALYSIS_VALIDATION_CACHE_MS:
        return cached
    # FastAPI executes this synchronous endpoint in a worker thread.  The
    # report is CPU-heavy, so concurrent tabs share one reconstruction instead
    # of starting duplicate 20+ second builds for the same public candles.
    with market_validation_lock:
        now=int(time.time()*1000)
        key=_market_validation_signature()
        cached=runtime.get("market_analysis_validation")
        cached_at=int(runtime.get("market_analysis_validation_at") or 0)
        if cached is not None and key==runtime.get("market_analysis_validation_key") and now-cached_at<MARKET_ANALYSIS_VALIDATION_CACHE_MS:
            return cached
        report=build_validation_report(
            {timeframe:db.candles_since(INSTRUMENT,timeframe,0) for timeframe in CHART_TIMEFRAMES},
            generated_at=now,
            max_samples=MARKET_ANALYSIS_VALIDATION_WINDOW,
            decision_stride=MARKET_ANALYSIS_VALIDATION_STRIDE,
        )
        runtime["market_analysis_validation"]=report
        runtime["market_analysis_validation_key"]=key
        runtime["market_analysis_validation_at"]=now
        return report


@app.get("/api/news")
def news_get(limit:int=Query(20,ge=1,le=100)):
    usable,message=_news_sources_usable()
    public=runtime["news"] if usable else _neutral_news_with_source_state(message)
    return {"items":public["items"][:limit],"analysis":public["analysis"]}


@app.get("/api/settings",response_model=Settings,response_model_by_alias=True)
def settings_get():return db.get_settings()


@app.put("/api/settings",response_model=Settings,response_model_by_alias=True)
def settings_put(value:Settings):db.put_settings(value);return value


@app.post("/api/workbench/calculate")
def workbench_calculate(value:TradePlanDraft):
    return _risk_with_liquidation(value,calculate_risk(value))


@app.get("/api/trade-plans/current")
def trade_plan_current():
    with trade_plan_lock:
        value=db.get_active_trade_plan()
        if value:
            value=normalize_live_execution_state(value)
            value=recompute_execution(value)
            value=_enrich_record_liquidation(value)
            db.save_trade_plan(value,active=True)
            return value
    return {"id":None,"state":TradeState.IDLE.value,"plan":None,"risk":None,"events":[]}


@app.post("/api/trade-plans",status_code=201)
def trade_plan_create(value:TradePlanDraft):
    with trade_plan_lock:
        existing=db.get_active_trade_plan()
        if existing and TradeState(existing["state"]) not in TERMINAL_STATES:
            raise HTTPException(409,"已有未结束的交易计划，请先完成或取消")
        try:record=_enrich_record_liquidation(make_plan_record(value))
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        db.save_trade_plan(record,active=True)
        db.save_trade_plan_event(record["id"],TradeState.IDLE.value,TradeState.PLANNED.value,"CREATE_PLAN",None,{"risk":record["risk"]})
        return record


@app.put("/api/trade-plans/{plan_id}")
def trade_plan_update(plan_id:str,value:TradePlanDraft):
    with trade_plan_lock:
        record=db.get_trade_plan(plan_id)
        if not record:raise HTTPException(404,"交易计划不存在")
        if TradeState(record["state"])!=TradeState.PLANNED:
            raise HTTPException(409,"只有尚未确认初始开仓的计划可以修改价格和仓位")
        risk=calculate_risk(value)
        if not risk.valid:raise HTTPException(422,"；".join(risk.errors))
        updated={**record,"plan":value.model_dump(by_alias=True,mode="json"),"risk":_risk_with_liquidation(value,risk),"updatedAt":now_ms()}
        updated.pop("activeReminder",None)
        updated=_enrich_record_liquidation(recompute_execution(updated))
        db.save_trade_plan(updated,active=True)
        db.save_trade_plan_event(plan_id,record["state"],record["state"],"UPDATE_PLAN",None,{"risk":updated["risk"]})
        return updated


@app.post("/api/trade-plans/{plan_id}/actions")
def trade_plan_action(plan_id:str,value:TradeActionRequest):
    with trade_plan_lock:
        record=db.get_trade_plan(plan_id)
        if not record:raise HTTPException(404,"交易计划不存在")
        record=normalize_live_execution_state(record)
        before=record["state"]
        try:updated,target=apply_action(record,value)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        updated=_enrich_record_liquidation(updated)
        db.save_trade_plan(updated,active=True)
        db.save_trade_plan_event(plan_id,before,target,value.action.value,value.price,{"note":value.note,"quantityBtc":value.quantity_btc})
        log=_save_terminal_trade_log(updated,"live")
        return {"plan":updated,"log":log}


@app.get("/api/trade-plans/{plan_id}/events")
def trade_plan_event_history(plan_id:str,limit:int=Query(200,ge=1,le=1000)):
    if not db.get_trade_plan(plan_id):raise HTTPException(404,"交易计划不存在")
    return {"items":db.trade_plan_events(plan_id,limit)}


@app.get("/api/workbench/add-check")
def workbench_add_check():
    record=db.get_active_trade_plan()
    if not record:raise HTTPException(404,"当前没有交易计划")
    plan=TradePlanDraft.model_validate(record["plan"]);risk=RiskCalculation.model_validate(record["risk"])
    c1=db.candles(INSTRUMENT,"1H",220,confirmed_only=True);c4=db.candles(INSTRUMENT,"4H",200,confirmed_only=True)
    funding=db.latest_funding(INSTRUMENT);oi=db.open_interest_context(INSTRUMENT)
    fresh,_=_live_market_ready()
    try:regime=current_advice().regime
    except Exception:regime=MarketRegime.STALE
    try:technical=technical_summary()
    except Exception:technical=None
    return build_add_check(plan,risk,c1,c4,runtime["price"],funding[1] if funding else None,oi,technical,regime,fresh)


@app.get("/api/trade-logs")
def trade_log_history(source:str=Query("live",pattern="^(live|replay)$"),limit:int=Query(200,ge=1,le=1000)):
    return {"items":db.trade_logs(source,limit)}


@app.get("/api/trade-logs/statistics")
def trade_log_statistics(source:str=Query("live",pattern="^(live|replay)$")):
    return trade_statistics(db.trade_logs(source,None))


@app.get("/api/trade-logs/export")
def trade_log_export(
    source:str=Query(...,pattern="^(live|replay)$"),
    format:str=Query(...,pattern="^(csv|json)$"),
):
    rows=db.trade_logs(source,None);exported=now_ms()
    if format=="csv":
        content=encode_trade_log_csv(rows);media_type="text/csv; charset=utf-8";extension="csv"
    else:
        content=encode_trade_log_json(source,rows,exported);media_type="application/json";extension="json"
    return Response(
        content=content,media_type=media_type,
        headers={"Content-Disposition":f'attachment; filename="trade-logs-{source}-{exported}.{extension}"'},
    )


@app.delete("/api/trade-logs/{log_id}")
def trade_log_delete(log_id:str,source:str=Query(...,pattern="^(live|replay)$")):
    if not log_id or len(log_id)>200:raise HTTPException(422,"invalid trade log id")
    deleted=db.delete_trade_log(source,log_id)
    return {"deleted":deleted,"deletedCount":1 if deleted else 0,"source":source,"id":log_id}


@app.post("/api/trade-logs/bulk-delete")
def trade_log_bulk_delete(value:BulkDeleteTradeLogsRequest):
    deleted=db.bulk_delete_trade_logs(value.source,value.ids)
    return {"deletedCount":len(deleted),"source":value.source}


@app.delete("/api/trade-logs")
def trade_log_clear(
    source:str=Query(...,pattern="^(live|replay)$"),
    confirmation:str=Query(...),
):
    if confirmation!="DELETE":raise HTTPException(422,"confirmation must equal DELETE")
    return {"deletedCount":db.clear_trade_logs(source),"source":source}


def _replay_data(record:dict)->dict:
    one=db.candles_since(INSTRUMENT,"1H",0,confirmed_only=True)
    four=db.candles_since(INSTRUMENT,"4H",0,confirmed_only=True)
    index=next((i for i,candle in enumerate(one) if candle.timestamp==record["cursorTs"]),None)
    if index is None:raise HTTPException(409,"Replay 所需历史 K 线已不存在")
    visible_one,visible_four=visible_replay_candles(one,four,index)
    public={**record,"candles1H":[row.model_dump(by_alias=True,mode="json") for row in visible_one],"candles4H":[row.model_dump(by_alias=True,mode="json") for row in visible_four]}
    # The server keeps the next index private. No future candles, indicators or news are included.
    public.pop("cursorIndex",None)
    return public


@app.post("/api/replay/sessions",status_code=201)
def replay_create(value:ReplayCreateRequest):
    one=db.candles_since(INSTRUMENT,"1H",0,confirmed_only=True)
    four=db.candles_since(INSTRUMENT,"4H",0,confirmed_only=True)
    try:index=replay_start_index(one,value.mode,value.start_at)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    now=now_ms();record={
        "id":new_id(),"status":"SELECTED","state":TradeState.IDLE.value,
        "mode":value.mode,"cursorTs":one[index].timestamp,"cursorIndex":index,
        "plan":None,"risk":None,"addCount":0,"actualFills":{},"events":[],
        "mfeUsdt":0.0,"maeUsdt":0.0,"regime4H":"UNKNOWN","createdAt":now,"updatedAt":now,"result":None,
    }
    db.save_replay_session(record)
    return _replay_data(record)


@app.put("/api/replay/sessions/{session_id}/plan")
def replay_set_plan(session_id:str,value:ReplayPlanRequest):
    record=db.get_replay_session(session_id)
    if not record:raise HTTPException(404,"Replay 会话不存在")
    risk=calculate_risk(value.plan)
    if not risk.valid:raise HTTPException(422,"；".join(risk.errors))
    updated={
        **record,"status":"RUNNING","state":TradeState.INITIAL_OPEN.value,
        "plan":value.plan.model_dump(by_alias=True,mode="json"),
        "risk":risk.model_dump(by_alias=True,mode="json"),
        "actualFills":{"initial":{"price":risk.initial_fill_price,"quantityBtc":risk.initial_quantity_btc}},"updatedAt":now_ms(),
    }
    updated=recompute_execution(updated)
    db.save_replay_session(updated)
    return _replay_data(updated)


@app.post("/api/replay/sessions/{session_id}/actions")
def replay_action(session_id:str,value:TradeActionRequest):
    record=db.get_replay_session(session_id)
    if not record:raise HTTPException(404,"Replay 会话不存在")
    if not record.get("plan"):raise HTTPException(409,"请先在隐藏未来数据的图表上设置交易计划")
    try:updated,_=apply_action(record,value)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    if TradeState(updated["state"]) in TERMINAL_STATES:updated["status"]="COMPLETE"
    updated["events"]=[*list(updated.get("events") or []),{"type":value.action.value,"price":value.price,"at":updated["cursorTs"]}]
    log=_save_terminal_trade_log(updated,"replay")
    if log:updated["result"]=log
    db.save_replay_session(updated)
    return _replay_data(updated)


@app.post("/api/replay/sessions/{session_id}/step")
def replay_step(session_id:str,count:int=Query(1,ge=1,le=24)):
    record=db.get_replay_session(session_id)
    if not record:raise HTTPException(404,"Replay 会话不存在")
    if record.get("status")!="RUNNING" or not record.get("plan"):
        raise HTTPException(409,"Replay 尚未设置计划或已经结束")
    one=db.candles_since(INSTRUMENT,"1H",0,confirmed_only=True)
    index=next((i for i,candle in enumerate(one) if candle.timestamp==record["cursorTs"]),None)
    if index is None:raise HTTPException(409,"Replay 所需历史 K 线已不存在")
    updated=record
    for _ in range(count):
        index+=1
        if index>=len(one):
            updated["status"]="COMPLETE";break
        updated={**updated,"cursorTs":one[index].timestamp,"cursorIndex":index,"updatedAt":now_ms()}
        updated,event=replay_candle_transition(updated,one[index])
        if event:
            updated["events"]=[*list(updated.get("events") or []),{"type":event,"price":None,"at":one[index].timestamp}]
        if TradeState(updated["state"]) in TERMINAL_STATES:
            updated["status"]="COMPLETE"
            log=_save_terminal_trade_log(updated,"replay")
            if log:updated["result"]=log
            break
    db.save_replay_session(updated)
    return _replay_data(updated)


@app.get("/api/replay/sessions/{session_id}")
def replay_get(session_id:str):
    record=db.get_replay_session(session_id)
    if not record:raise HTTPException(404,"Replay 会话不存在")
    return _replay_data(record)


@app.delete("/api/local-data")
async def clear_data():
    if backtest_lock.locked():
        raise HTTPException(409,"回测运行期间不能清除本地数据")
    db.clear_local_data()
    runtime["price"],runtime["ticker_ts"]=None,None
    runtime["mark_price"],runtime["mark_price_ts"]=None,None
    runtime["liquidation_refresh_ts"]=0
    runtime["market_analysis"],runtime["market_analysis_signature"]=None,None
    runtime["market_analysis_validation"],runtime["market_analysis_validation_key"]=None,None
    runtime["market_analysis_validation_at"]=0
    runtime["candle_gaps"]={timeframe:False for timeframe in CHART_TIMEFRAMES}
    runtime["candle_gap_targets"]={timeframe:None for timeframe in CHART_TIMEFRAMES}
    runtime["news"],runtime["news_source_status"]=_empty_news("本地数据已清除，等待重新同步"),[]
    runtime["news_checked"],runtime["news_last_success"]=False,None
    resyncing=False
    if os.getenv("OKX_DISABLE_NETWORK")!="1":
        existing=runtime.get("resync_task")
        if not existing or existing.done():
            runtime["resync_task"]=asyncio.create_task(resync_market_history())
        resyncing=True
    return {"cleared":True,"resyncing":resyncing}


async def execute_backtest(job_id:str,req:BacktestRequest):
    try:
        now=int(time.time()*1000);since=_history_since_ms(now,req.years)
        db.save_backtest(job_id,"running",.02,"正在分页下载1H历史K线")
        async def p1(oldest,page):db.save_backtest(job_id,"running",min(.35,.02+page*.005),f"1H回填第{page}页")
        downloaded_1h=await client.backfill_candles(INSTRUMENT,"1H",since,p1);db.upsert_candles(INSTRUMENT,downloaded_1h)
        c1=[row for row in db.candles_since(INSTRUMENT,"1H",since,confirmed_only=True) if row.timestamp+3600_000<=now]
        db.save_backtest(job_id,"running",.4,"正在分页下载4H历史K线")
        downloaded_4h=await client.backfill_candles(INSTRUMENT,"4H",since);db.upsert_candles(INSTRUMENT,downloaded_4h)
        c4=[row for row in db.candles_since(INSTRUMENT,"4H",since,confirmed_only=True) if row.timestamp+4*3600_000<=now]
        coverage=_backtest_horizon_coverage(c1,c4,since+7*86400_000,now)
        if not coverage["complete"]:
            result={"status":"insufficient_data","strategy":req.strategy,"reason":f"请求的{req.years}年历史覆盖不足（当前共同覆盖 {coverage['durationCoverage']*100:.2f}%）","historyCoverage":coverage,"validationPass":False,"validationLabel":"实验信号（历史覆盖不足）"}
            db.save_backtest(job_id,"complete",1,"回测结束：请求历史覆盖不足",result)
            return
        db.save_backtest(job_id,"running",.62,"正在下载官方资金费率历史")
        funding=[]
        # Official endpoint accepts at most 20 months: split into ~18-month chunks.
        step=18*30*86400_000;begin=since
        while begin<now:
            end=min(now,begin+step)
            try:
                chunk=await client.bulk_funding_history(begin,end)
                funding.extend(chunk);db.upsert_funding(INSTRUMENT,chunk)
            except Exception:pass
            begin=end+1
        if not funding:
            funding=await client.funding_history(INSTRUMENT,100)
        db.upsert_funding(INSTRUMENT,funding)
        # Persisted public history is the source of truth. This preserves valid
        # prior pages when an OKX request is temporarily partial or rate-limited.
        funding=db.funding_rows(INSTRUMENT,since)
        db.save_backtest(job_id,"running",.82,"正在运行保守回测")
        result=await run_backtest_isolated(c1,c4,sorted(dict(funding).items()),req.strategy,req.fee_bps,req.slippage_bps)
        result["historyCoverage"]=coverage
        message="回测完成" if result.get("status")=="complete" else f"回测结束：{result.get('reason','数据不足或结果不可用')}"
        db.save_backtest(job_id,"complete",1,message,result)
    except asyncio.CancelledError:
        row=db.get_backtest(job_id)
        db.save_backtest(job_id,"interrupted",float(row["progress"]) if row else 0,"服务关闭，回测已中断")
        raise
    except Exception as e:db.save_backtest(job_id,"failed",1,f"{type(e).__name__}: {e}")
    finally:
        if backtest_lock.locked():backtest_lock.release()


@app.post("/api/backtests",response_model=BacktestStatus,response_model_by_alias=True,status_code=202)
async def backtests(req:BacktestRequest):
    if backtest_lock.locked():
        raise HTTPException(409,"已有回测正在运行，请等待其完成")
    await backtest_lock.acquire()
    try:
        id=str(uuid.uuid4());db.create_backtest(id,req.model_dump(by_alias=True),"等待执行")
        task=asyncio.create_task(execute_backtest(id,req),name=f"backtest-{id}")
    except Exception:
        backtest_lock.release()
        raise
    runtime["backtest_tasks"].add(task)
    task.add_done_callback(runtime["backtest_tasks"].discard)
    return BacktestStatus(id=id,status="queued",message="等待执行")


def _backtest_response(row:dict)->dict:
    def decoded(value):
        if not value:return None
        try:return json.loads(value,parse_constant=lambda constant:(_ for _ in ()).throw(ValueError(f"non-finite JSON constant: {constant}")))
        except (TypeError,json.JSONDecodeError):return None
        except ValueError:return None
    result=decoded(row.get("payload"));request=decoded(row.get("request_payload"))
    message=row["message"]
    if row.get("payload") and result is None:message=f"{message}（结果记录损坏，已安全忽略）"
    if row.get("request_payload") and request is None:message=f"{message}（请求记录损坏）"
    return {
        "id":row["id"],"status":row["status"],"progress":row["progress"],"message":message,
        "result":result,"request":request,
        "createdAt":row.get("created_at"),"updatedAt":row.get("updated_at"),
        "startedAt":row.get("started_at"),"finishedAt":row.get("finished_at"),
    }


@app.get("/api/backtests")
def backtest_history(limit:int=Query(20,ge=1,le=200),status:str|None=Query(None,pattern="^(queued|running|complete|failed|interrupted)$")):
    return {"items":[_backtest_response(row) for row in db.backtest_history(limit,status)]}


@app.get("/api/backtests/{id}")
def backtest_status(id:str):
    row=db.get_backtest(id)
    if not row:raise HTTPException(404,"回测任务不存在")
    return _backtest_response(row)


@app.delete("/api/backtests/{id}")
def backtest_delete(id:str):
    row=db.get_backtest(id)
    if not row:raise HTTPException(404,"回测任务不存在")
    if row["status"] in ("queued","running"):
        raise HTTPException(409,"不能删除正在排队或运行的回测")
    if not db.delete_backtest(id):raise HTTPException(409,"回测状态已变化，请重试")
    return {"deleted":True,"id":id}


@app.websocket("/ws/live")
async def live(ws:WebSocket):
    global pending_websockets
    origin=ws.headers.get("origin")
    if origin is not None and not _origin_allowed(origin,ws.headers.get("host")):
        await ws.close(code=1008,reason="Origin is not allowed");return
    if len(subscribers)+pending_websockets>=MAX_WEBSOCKET_CONNECTIONS:
        await ws.close(code=1013,reason="Too many local connections");return
    pending_websockets+=1
    try:
        await ws.accept();subscribers.add(ws)
        await ws.send_json({"type":"ready","connectionStatus":runtime["connection"]})
        while True:
            message=await ws.receive()
            if message.get("type")=="websocket.disconnect":break
            text=message.get("text")
            if text is None:
                await ws.close(code=1003,reason="Text frames only");break
            if len(text.encode("utf-8"))>1024:
                await ws.close(code=1009,reason="Message too large");break
    except WebSocketDisconnect:pass
    finally:
        pending_websockets=max(0,pending_websockets-1)
        subscribers.discard(ws)


DIST=ROOT/"frontend"/"dist"
if DIST.exists():
    assets=DIST/"assets"
    if assets.exists():app.mount("/assets",StaticFiles(directory=assets),name="assets")

    @app.get("/{path:path}",include_in_schema=False)
    async def spa(path:str):
        if path in {"api","ws"} or path.startswith(("api/","ws/")):
            raise HTTPException(404,"本地接口不存在")
        requested=(DIST/path).resolve()
        if DIST.resolve() in requested.parents and requested.is_file():return FileResponse(requested)
        return FileResponse(DIST/"index.html")
