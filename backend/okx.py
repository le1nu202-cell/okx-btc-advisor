from __future__ import annotations

import asyncio
import csv
import io
import json
import time
import zipfile
from collections.abc import Awaitable, Callable

import httpx
import websockets

from .models import Candle


class OKXError(RuntimeError): pass


class OKXPublicClient:
    def __init__(self, base_url="https://www.okx.com", ws_url="wss://ws.okx.com:8443/ws/v5/public", timeout=20):
        self.base_url=base_url.rstrip("/"); self.ws_url=ws_url; self.timeout=timeout

    async def _get(self,path,params=None):
        last=None
        async with httpx.AsyncClient(base_url=self.base_url,timeout=self.timeout,follow_redirects=True) as client:
            for attempt in range(3):
                try:
                    r=await client.get(path,params=params); r.raise_for_status(); body=r.json()
                    if body.get("code")!="0": raise OKXError(body.get("msg") or str(body))
                    return body.get("data",[])
                except (httpx.HTTPError,OKXError) as exc:
                    last=exc
                    if attempt<2:await asyncio.sleep(.5*(2**attempt))
        raise OKXError(str(last))

    @staticmethod
    def parse_candles(rows,timeframe):
        found={}
        for r in rows:
            if len(r)<9:continue
            c=Candle(timestamp=int(r[0]),open=float(r[1]),high=float(r[2]),low=float(r[3]),close=float(r[4]),volume=float(r[5]),volume_ccy=float(r[6]) if r[6] else None,confirm=str(r[8])=="1",timeframe=timeframe)
            found[c.timestamp]=c
        return [found[k] for k in sorted(found)]

    async def candles(self,instrument="BTC-USDT-SWAP",bar="1H",limit=300,after=None,before=None,history=True):
        p={"instId":instrument,"bar":bar,"limit":min(300,limit)}
        if after is not None:p["after"]=str(after)
        if before is not None:p["before"]=str(before)
        path="/api/v5/market/history-candles" if history else "/api/v5/market/candles"
        return self.parse_candles(await self._get(path,p),bar)

    async def backfill_candles(self,instrument,bar,since_ms,progress=None,max_pages=2000):
        all_rows={}; after=None
        for page in range(max_pages):
            rows=await self.candles(instrument,bar,300,after=after)
            if not rows:break
            for c in rows: all_rows[c.timestamp]=c
            oldest=min(c.timestamp for c in rows)
            if progress: await progress(oldest,page+1)
            if oldest<=since_ms:break
            if after is not None and oldest>=after:break
            after=oldest
            await asyncio.sleep(.12)
        return [all_rows[k] for k in sorted(all_rows) if k>=since_ms]

    async def ticker(self,instrument="BTC-USDT-SWAP"):
        d=await self._get("/api/v5/market/ticker",{"instId":instrument}); return d[0] if d else None

    async def funding_history(self,instrument="BTC-USDT-SWAP",limit=100,after=None):
        p={"instId":instrument,"limit":min(100,limit)}
        if after:p["after"]=str(after)
        return [(int(x["fundingTime"]),float(x["fundingRate"])) for x in await self._get("/api/v5/public/funding-rate-history",p)]

    async def open_interest(self,instrument="BTC-USDT-SWAP"):
        d=await self._get("/api/v5/public/open-interest",{"instType":"SWAP","instId":instrument})
        return (int(d[0].get("ts",time.time()*1000)),float(d[0]["oiCcy"] or d[0]["oi"])) if d else None

    async def bulk_funding_history(self,begin_ms:int,end_ms:int,inst_family="BTC-USDT") -> list[tuple[int,float]]:
        """Download official monthly public-data ZIPs. Missing months remain missing."""
        params={"module":"3","instType":"SWAP","instFamilyList":inst_family,"dateAggrType":"monthly","begin":str(begin_ms),"end":str(end_ms)}
        items=await self._get("/api/v5/public/market-data-history",params); urls=[]
        def collect(x):
            if isinstance(x,dict):
                for k,v in x.items():
                    if isinstance(v,str) and v.startswith("http") and ("url" in k.lower() or ".zip" in v):urls.append(v)
                    else:collect(v)
            elif isinstance(x,list):
                for y in x:collect(y)
        collect(items); out={}
        async with httpx.AsyncClient(timeout=60,follow_redirects=True) as client:
            for url in dict.fromkeys(urls):
                raw=(await client.get(url)).content
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as z:
                        payload=b"\n".join(z.read(n) for n in z.namelist() if n.lower().endswith(".csv"))
                except zipfile.BadZipFile: payload=raw
                text=payload.decode("utf-8-sig",errors="replace")
                for row in csv.DictReader(io.StringIO(text)):
                    try:
                        if row.get("instrument_name","").upper() not in ("BTC-USDT-SWAP",""):continue
                        ts=int(float(row["funding_time"])); ts=ts*1000 if ts<10_000_000_000 else ts
                        out[ts]=float(row["funding_rate"])
                    except (KeyError,TypeError,ValueError):continue
        return sorted(out.items())

    async def stream(self,on_message:Callable[[dict],Awaitable[None]],stop:asyncio.Event):
        delay=1
        while not stop.is_set():
            try:
                async with websockets.connect(self.ws_url,ping_interval=20,ping_timeout=15) as ws:
                    await on_message({"event":"reconnected"})
                    args=[{"channel":"tickers","instId":"BTC-USDT-SWAP"},{"channel":"candle1H","instId":"BTC-USDT-SWAP"},{"channel":"candle4H","instId":"BTC-USDT-SWAP"},{"channel":"funding-rate","instId":"BTC-USDT-SWAP"},{"channel":"open-interest","instId":"BTC-USDT-SWAP"}]
                    await ws.send(json.dumps({"op":"subscribe","args":args})); delay=1
                    async for raw in ws:
                        msg=json.loads(raw)
                        if "data" in msg:await on_message(msg)
                        if stop.is_set():break
            except (OSError,websockets.WebSocketException,asyncio.TimeoutError):
                await asyncio.sleep(delay); delay=min(60,delay*2)
