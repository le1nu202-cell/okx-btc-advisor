from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .backtest import run_backtest
from .db import Database
from .models import BacktestRequest, BacktestStatus, MarketSnapshot, Settings
from .okx import OKXPublicClient
from .strategy import analyze, estimate_risk

INSTRUMENT="BTC-USDT-SWAP"
ROOT=Path(__file__).resolve().parents[1]
db=Database(os.getenv("OKX_ADVISOR_DB",str(Path(__file__).with_name("data")/"advisor.db")))
client=OKXPublicClient(base_url=os.getenv("OKX_BASE_URL","https://www.okx.com"),ws_url=os.getenv("OKX_WS_URL","wss://ws.okx.com:8443/ws/v5/public"))
runtime={"price":None,"ticker_ts":None,"connection":"starting","stop":None,"task":None,"bootstrap_task":None}
subscribers:set[WebSocket]=set()


def current_advice():
    c1=db.candles(INSTRUMENT,"1H",400,True); c4=db.candles(INSTRUMENT,"4H",260,True)
    funding=db.funding_rows(INSTRUMENT,c1[-1].timestamp-90*86400_000 if c1 else 0); oi=db.latest_oi(INSTRUMENT)
    return analyze(c1,c4,funding,oi,custom=bool(db.get_settings().custom_parameters))


async def broadcast(payload:dict):
    dead=[]
    for ws in list(subscribers):
        try:await ws.send_json(payload)
        except Exception:dead.append(ws)
    for ws in dead:subscribers.discard(ws)


async def on_okx(msg:dict):
    if msg.get("event")=="reconnected":
        # Fill any gap accumulated while the public stream was disconnected.
        for tf in ("1H","4H"):
            try:db.upsert_candles(INSTRUMENT,await client.candles(INSTRUMENT,tf,100,history=False))
            except Exception:pass
        return
    channel=msg.get("arg",{}).get("channel",""); data=msg.get("data",[])
    runtime["connection"]="connected"
    if channel=="tickers" and data:
        runtime["price"]=float(data[0]["last"]);runtime["ticker_ts"]=int(data[0]["ts"])
    elif channel.startswith("candle"):
        tf="1H" if channel=="candle1H" else "4H"; rows=client.parse_candles(data,tf);db.upsert_candles(INSTRUMENT,rows)
        if rows and rows[-1].confirm:
            advice=current_advice()
            if advice.action.value!="WAIT":
                inserted=db.save_signal(advice)
                if inserted:await broadcast({"type":"signal","advice":advice.model_dump(by_alias=True,mode="json")})
    elif channel=="funding-rate" and data:
        x=data[0];db.upsert_funding(INSTRUMENT,[(int(x["fundingTime"]),float(x["fundingRate"]))])
    elif channel=="open-interest" and data:
        x=data[0];db.upsert_oi(INSTRUMENT,int(x["ts"]),float(x.get("oiCcy") or x["oi"]))
    await broadcast({"type":"market","price":runtime["price"],"tickerTime":runtime["ticker_ts"],"connectionStatus":runtime["connection"]})


async def bootstrap():
    try:
        for tf in ("1H","4H"):
            rows=await client.candles(INSTRUMENT,tf,300,history=True);db.upsert_candles(INSTRUMENT,rows)
        db.upsert_funding(INSTRUMENT,await client.funding_history(INSTRUMENT,100))
        oi=await client.open_interest(INSTRUMENT)
        if oi:db.upsert_oi(INSTRUMENT,*oi)
        tick=await client.ticker(INSTRUMENT)
        if tick:runtime["price"]=float(tick["last"]);runtime["ticker_ts"]=int(tick["ts"])
    except Exception as e:runtime["connection"]=f"degraded: {type(e).__name__}"
    runtime["stop"]=asyncio.Event(); runtime["task"]=asyncio.create_task(client.stream(on_okx,runtime["stop"]))


@asynccontextmanager
async def lifespan(app:FastAPI):
    if os.getenv("OKX_DISABLE_NETWORK")!="1":runtime["bootstrap_task"]=asyncio.create_task(bootstrap())
    yield
    if runtime["stop"]:runtime["stop"].set()
    if runtime["task"]:runtime["task"].cancel()
    if runtime["bootstrap_task"]:runtime["bootstrap_task"].cancel()


app=FastAPI(title="OKX BTC Advisor",version="0.1.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["http://127.0.0.1:5173","http://localhost:5173"],allow_methods=["GET","PUT","POST","DELETE"],allow_headers=["*"])


@app.get("/api/health")
def health():return {"status":"ok","connectionStatus":runtime["connection"]}


@app.get("/api/market/snapshot",response_model=MarketSnapshot,response_model_by_alias=True)
def snapshot():
    c1=db.candles(INSTRUMENT,"1H",200);c4=db.candles(INSTRUMENT,"4H",200);f=db.latest_funding(INSTRUMENT);oi=db.latest_oi(INSTRUMENT)
    now=int(time.time()*1000); last_close=c1[-1].timestamp+3600_000 if c1 else 0
    stale=not last_close or now-last_close>2*3600_000 or (runtime["ticker_ts"] is not None and now-runtime["ticker_ts"]>30_000)
    # A future scheduled fundingTime is not an observation timestamp.
    market_ts=max([x for x in (runtime["ticker_ts"],last_close,oi[0] if oi else None) if x is not None],default=0)
    updated=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(market_ts/1000)) if market_ts else ""
    return MarketSnapshot(instrument=INSTRUMENT,price=runtime["price"] or (c1[-1].close if c1 else None),updated_at=updated,stale=stale,candles_1h=c1,candles_4h=c4,funding_rate=f[1] if f else None,funding_time=f[0] if f else None,open_interest=oi[1] if oi else None,open_interest_time=oi[0] if oi else None,connection_status=runtime["connection"])


@app.get("/api/advice/current")
def advice_current():
    a=current_advice(); return {"advice":a.model_dump(by_alias=True,mode="json"),"riskEstimate":estimate_risk(a,db.get_settings()).model_dump(by_alias=True,mode="json")}


@app.get("/api/advice/history")
def advice_history(limit:int=Query(50,ge=1,le=500)):return {"items":db.signal_history(limit)}


@app.get("/api/settings",response_model=Settings,response_model_by_alias=True)
def settings_get():return db.get_settings()


@app.put("/api/settings",response_model=Settings,response_model_by_alias=True)
def settings_put(value:Settings):db.put_settings(value);return value


@app.delete("/api/local-data")
def clear_data():db.clear_local_data();return {"cleared":True}


async def execute_backtest(job_id:str,req:BacktestRequest):
    try:
        now=int(time.time()*1000);since=now-req.years*365*86400_000
        db.save_backtest(job_id,"running",.02,"正在分页下载1H历史K线")
        async def p1(oldest,page):db.save_backtest(job_id,"running",min(.35,.02+page*.005),f"1H回填第{page}页")
        c1=await client.backfill_candles(INSTRUMENT,"1H",since,p1);db.upsert_candles(INSTRUMENT,c1)
        db.save_backtest(job_id,"running",.4,"正在分页下载4H历史K线")
        c4=await client.backfill_candles(INSTRUMENT,"4H",since);db.upsert_candles(INSTRUMENT,c4)
        db.save_backtest(job_id,"running",.62,"正在下载官方资金费率历史")
        funding=[]
        # Official endpoint accepts at most 20 months: split into ~18-month chunks.
        step=18*30*86400_000;begin=since
        while begin<now:
            end=min(now,begin+step)
            try:funding.extend(await client.bulk_funding_history(begin,end))
            except Exception:pass
            begin=end+1
        if not funding:
            funding=await client.funding_history(INSTRUMENT,100)
        db.upsert_funding(INSTRUMENT,funding)
        db.save_backtest(job_id,"running",.82,"正在运行保守回测")
        result=await asyncio.to_thread(run_backtest,c1,c4,sorted(dict(funding).items()),req.strategy,req.fee_bps,req.slippage_bps)
        db.save_backtest(job_id,"complete",1,"回测完成",result)
    except Exception as e:db.save_backtest(job_id,"failed",1,f"{type(e).__name__}: {e}")


@app.post("/api/backtests",response_model=BacktestStatus,response_model_by_alias=True,status_code=202)
async def backtests(req:BacktestRequest,background:BackgroundTasks):
    id=str(uuid.uuid4());db.save_backtest(id,"queued",0,"等待执行");background.add_task(execute_backtest,id,req);return BacktestStatus(id=id,status="queued",message="等待执行")


@app.get("/api/backtests/{id}")
def backtest_status(id:str):
    row=db.get_backtest(id)
    if not row:raise HTTPException(404,"回测任务不存在")
    return {"id":id,"status":row["status"],"progress":row["progress"],"message":row["message"],"result":json.loads(row["payload"]) if row["payload"] else None}


@app.websocket("/ws/live")
async def live(ws:WebSocket):
    await ws.accept();subscribers.add(ws)
    try:
        await ws.send_json({"type":"ready","connectionStatus":runtime["connection"]})
        while True:await ws.receive_text()
    except WebSocketDisconnect:subscribers.discard(ws)


DIST=ROOT/"frontend"/"dist"
if DIST.exists():
    assets=DIST/"assets"
    if assets.exists():app.mount("/assets",StaticFiles(directory=assets),name="assets")

    @app.get("/{path:path}",include_in_schema=False)
    async def spa(path:str):
        requested=(DIST/path).resolve()
        if DIST.resolve() in requested.parents and requested.is_file():return FileResponse(requested)
        return FileResponse(DIST/"index.html")
