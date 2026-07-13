from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
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
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .backtest import run_backtest
from .db import Database
from .models import AdviceAction, BacktestRequest, BacktestStatus, MarketRegime, MarketSnapshot, Settings, SignalAdvice
from .news import NewsAggregator, aggregate_news
from .okx import OKXPublicClient
from .strategy import analyze, estimate_risk
from .technical import analyze_technical

INSTRUMENT="BTC-USDT-SWAP"
APP_ID="okx-btc-advisor"
APP_VERSION="0.3.0"
MAX_WEBSOCKET_CONNECTIONS=32
NEWS_POLL_SECONDS=300
NEWS_SOURCE_STALE_MS=15*60_000
ROOT=Path(__file__).resolve().parents[1]
db=Database(os.getenv("OKX_ADVISOR_DB",str(Path(__file__).with_name("data")/"advisor.db")))
client=OKXPublicClient(base_url=os.getenv("OKX_BASE_URL","https://www.okx.com"),ws_url=os.getenv("OKX_WS_URL","wss://ws.okx.com:8443/ws/v5/public"),business_ws_url=os.getenv("OKX_BUSINESS_WS_URL","wss://ws.okx.com:8443/ws/v5/business"))
def _empty_news(message="新闻源正在连接"):
    return {"items":[],"analysis":{"asOf":None,"windowHours":48,"score":0,"articleCount":0,"status":"unavailable","sourceCoverage":0,"sourceStatus":[],"rawImpact":0,"coverageAdjustedImpact":0,"reason":"新闻数据不可用，严格按中性处理","warnings":[message]}}


runtime={"price":None,"ticker_ts":None,"connection":"starting","stream_status":{"public":"starting","candles":"starting"},"stop":None,"task":None,"bootstrap_task":None,"resync_task":None,"reconcile_task":None,"news_task":None,"backtest_tasks":set(),"news":_empty_news(),"news_source_status":[],"news_checked":False,"news_last_success":None}
subscribers:set[WebSocket]=set()
pending_websockets=0
news_aggregator=NewsAggregator()
backtest_lock=asyncio.Lock()
backtest_executor:ProcessPoolExecutor|None=None


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


async def broadcast(payload:dict):
    dead=[]
    for ws in list(subscribers):
        try:await ws.send_json(payload)
        except Exception:dead.append(ws)
    for ws in dead:subscribers.discard(ws)


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


async def on_okx(msg:dict):
    stream_name=msg.get("stream") or msg.get("_stream")
    if msg.get("event")=="reconnecting":
        update_stream_status(stream_name,"reconnecting")
        await broadcast({"type":"market","price":runtime["price"],"tickerTime":runtime["ticker_ts"],"connectionStatus":runtime["connection"]})
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
        return
    channel=msg.get("arg",{}).get("channel",""); data=msg.get("data",[]);valid=False
    if channel=="tickers" and data:
        parsed=client.parse_ticker(data[0])
        if parsed:
            valid=True
            if runtime["ticker_ts"] is None or parsed[1]>=runtime["ticker_ts"]:runtime["price"],runtime["ticker_ts"]=parsed
    elif channel.startswith("candle"):
        tf="1H" if channel=="candle1H" else "4H"; rows=client.parse_candles(data,tf)
        previous=db.candles(INSTRUMENT,tf,1)
        step=3600_000 if tf=="1H" else 4*3600_000
        if previous and rows and min(row.timestamp for row in rows)>previous[-1].timestamp+step:
            try:db.upsert_candles(INSTRUMENT,await client.candles(INSTRUMENT,tf,300,history=True))
            except Exception:pass
        db.upsert_candles(INSTRUMENT,rows)
        if rows and rows[-1].confirm:
            await publish_current_signal()
        valid=bool(rows)
    elif channel=="funding-rate" and data:
        rows=client.parse_funding_rows(data,max_future_ms=48*3600_000);db.upsert_funding(INSTRUMENT,rows);valid=bool(rows)
    elif channel=="open-interest" and data:
        parsed=client.parse_open_interest(data[0])
        if parsed:db.upsert_oi(INSTRUMENT,*parsed);valid=True
    if valid:update_stream_status(stream_name,"connected")
    await broadcast({"type":"market","price":runtime["price"],"tickerTime":runtime["ticker_ts"],"connectionStatus":runtime["connection"]})


async def resync_market_history()->bool:
    """Restore the complete live-analysis warm-up set without starting another WS."""
    errors=[]
    for tf in ("1H","4H"):
        try:
            rows=await client.candles(INSTRUMENT,tf,300,history=True);db.upsert_candles(INSTRUMENT,rows)
        except Exception as exc:errors.append(f"{tf}:{type(exc).__name__}")
    try:db.upsert_funding(INSTRUMENT,await client.backfill_funding_history(INSTRUMENT,int(time.time()*1000)-91*86400_000))
    except Exception as exc:errors.append(f"funding:{type(exc).__name__}")
    try:
        oi=await client.open_interest(INSTRUMENT)
        if oi:db.upsert_oi(INSTRUMENT,*oi)
    except Exception as exc:errors.append(f"oi:{type(exc).__name__}")
    try:
        tick=await client.ticker(INSTRUMENT)
        if tick:runtime["price"],runtime["ticker_ts"]=tick
    except Exception as exc:errors.append(f"ticker:{type(exc).__name__}")
    if errors:runtime["connection"]="degraded: resync"
    return not errors


async def bootstrap():
    await resync_market_history()
    runtime["stop"]=asyncio.Event(); runtime["task"]=asyncio.create_task(client.stream(on_okx,runtime["stop"]))


async def reconcile_market_once()->bool:
    """Repair silent per-channel WS stalls using the public current-candle REST view."""
    refreshed=False
    for tf in ("1H","4H"):
        try:
            previous=db.candles(INSTRUMENT,tf,1)
            rows=await client.candles(INSTRUMENT,tf,100,history=False)
            step=3600_000 if tf=="1H" else 4*3600_000
            if previous and rows and min(row.timestamp for row in rows)>previous[-1].timestamp+step:
                rows=await client.candles(INSTRUMENT,tf,300,history=True)
            if rows:db.upsert_candles(INSTRUMENT,rows);refreshed=True
        except Exception:pass
    if refreshed:
        try:await publish_current_signal()
        except Exception:pass
    return refreshed


async def market_reconcile_loop():
    while True:
        await asyncio.sleep(300)
        await reconcile_market_once()


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
        runtime["news_task"]=asyncio.create_task(news_loop())
    yield
    if runtime["stop"]:runtime["stop"].set()
    tasks=[x for x in (runtime["task"],runtime["bootstrap_task"],runtime["resync_task"],runtime["reconcile_task"],runtime["news_task"],*runtime["backtest_tasks"]) if x]
    for task in tasks:task.cancel()
    if tasks:await asyncio.gather(*tasks,return_exceptions=True)
    if backtest_executor is not None:
        backtest_executor.shutdown(wait=False,cancel_futures=True);backtest_executor=None


def _local_hostname(value:str|None)->bool:
    if not value:return False
    try:host=(urlsplit(value if "://" in value else f"//{value}").hostname or "").lower()
    except ValueError:return False
    if host in {"localhost","testserver"}:return True
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return False


def _local_origin(value:str|None)->bool:
    if not value:return False
    try:parsed=urlsplit(value)
    except ValueError:return False
    return parsed.scheme in {"http","https"} and parsed.username is None and _local_hostname(value)


class LoopbackHostMiddleware:
    """Reject DNS-rebinding Host values while retaining IPv4/IPv6 loopback support."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope["type"] not in {"http","websocket"}:
            await self.app(scope,receive,send);return
        host=next((value.decode("latin-1") for key,value in scope.get("headers",[]) if key.lower()==b"host"),"")
        if _local_hostname(host):
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
    return MarketSnapshot(instrument=INSTRUMENT,price=runtime["price"] or (c1[-1].close if c1 else None),updated_at=updated,stale=stale,candles_1h=c1,candles_4h=c4,funding_rate=f[1] if f else None,funding_time=f[0] if f else None,open_interest=oi[1] if oi else None,open_interest_time=oi[0] if oi else None,connection_status=runtime["connection"])


@app.get("/api/advice/current")
def advice_current():
    a=current_advice(); return {"advice":a.model_dump(by_alias=True,mode="json"),"riskEstimate":estimate_risk(a,db.get_settings()).model_dump(by_alias=True,mode="json")}


@app.get("/api/advice/history")
def advice_history(limit:int=Query(50,ge=1,le=500)):return {"items":db.signal_history(limit)}


@app.get("/api/technical/summary")
def technical_get():return technical_summary()


@app.get("/api/news")
def news_get(limit:int=Query(20,ge=1,le=100)):
    usable,message=_news_sources_usable()
    public=runtime["news"] if usable else _neutral_news_with_source_state(message)
    return {"items":public["items"][:limit],"analysis":public["analysis"]}


@app.get("/api/settings",response_model=Settings,response_model_by_alias=True)
def settings_get():return db.get_settings()


@app.put("/api/settings",response_model=Settings,response_model_by_alias=True)
def settings_put(value:Settings):db.put_settings(value);return value


@app.delete("/api/local-data")
async def clear_data():
    if backtest_lock.locked():
        raise HTTPException(409,"回测运行期间不能清除本地数据")
    db.clear_local_data()
    runtime["price"],runtime["ticker_ts"]=None,None
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
    if origin is not None and not _local_origin(origin):
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
