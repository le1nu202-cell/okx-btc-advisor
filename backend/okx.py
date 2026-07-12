from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import time
import zipfile
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx
import websockets

from .models import Candle


class OKXError(RuntimeError): pass


class OKXPublicClient:
    BULK_DOWNLOAD_HOSTS = {"static.okx.com"}
    MAX_BULK_URLS = 64
    MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
    MAX_ZIP_ENTRIES = 32
    MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
    MAX_COMPRESSION_RATIO = 200

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
        if timeframe not in {"1H","4H"}:
            raise ValueError(f"unsupported candle timeframe: {timeframe}")
        found={}
        now_ms=int(time.time()*1000)
        for r in rows:
            if len(r)<9:continue
            try:
                ts=int(r[0]); values=[float(r[i]) for i in range(1,6)]
                volume_ccy=float(r[6]) if r[6] else None
            except (TypeError,ValueError,OverflowError):
                continue
            o,h,l,c_value,volume=values
            finite=all(math.isfinite(x) for x in values) and (volume_ccy is None or math.isfinite(volume_ccy))
            valid_time=1_230_768_000_000 <= ts <= now_ms+24*3600_000
            valid_prices=min(o,c_value)>=l>0 and max(o,c_value)<=h and h>0
            if not finite or not valid_time or not valid_prices or volume<0 or (volume_ccy is not None and volume_ccy<0):
                continue
            c=Candle(timestamp=ts,open=o,high=h,low=l,close=c_value,volume=volume,volume_ccy=volume_ccy,confirm=str(r[8])=="1",timeframe=timeframe)
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
        collect(items); urls=list(dict.fromkeys(urls))
        if len(urls)>self.MAX_BULK_URLS:
            raise OKXError("too many bulk funding files")
        out={}
        async with httpx.AsyncClient(timeout=60,follow_redirects=True) as client:
            for url in urls:
                parts=urlsplit(url)
                if parts.scheme!="https" or (parts.hostname or "").lower() not in self.BULK_DOWNLOAD_HOSTS:
                    raise OKXError("untrusted bulk funding download URL")
                raw=bytearray()
                async with client.stream("GET",url) as response:
                    response.raise_for_status()
                    final=response.url
                    if final.scheme!="https" or (final.host or "").lower() not in self.BULK_DOWNLOAD_HOSTS:
                        raise OKXError("bulk funding redirect left the trusted host")
                    length=response.headers.get("content-length")
                    if length and int(length)>self.MAX_DOWNLOAD_BYTES:
                        raise OKXError("bulk funding download exceeds size limit")
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw)>self.MAX_DOWNLOAD_BYTES:
                            raise OKXError("bulk funding download exceeds size limit")
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as z:
                        members=[x for x in z.infolist() if x.filename.lower().endswith(".csv") and not x.is_dir()]
                        if len(members)>self.MAX_ZIP_ENTRIES:
                            raise OKXError("bulk funding ZIP has too many entries")
                        total=sum(x.file_size for x in members)
                        if total>self.MAX_UNCOMPRESSED_BYTES:
                            raise OKXError("bulk funding ZIP expands beyond size limit")
                        if any(x.file_size/max(1,x.compress_size)>self.MAX_COMPRESSION_RATIO for x in members):
                            raise OKXError("bulk funding ZIP compression ratio is unsafe")
                        payload=b"\n".join(z.read(x) for x in members)
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
                        try:
                            msg=json.loads(raw)
                            if not isinstance(msg,dict):continue
                            if "data" in msg:await on_message(msg)
                        except Exception as exc:
                            # A malformed frame or one failed handler invocation must not kill
                            # the sole long-lived market stream.
                            try:await on_message({"event":"message_error","error":type(exc).__name__})
                            except Exception:pass
                        if stop.is_set():break
            except Exception as exc:
                try:await on_message({"event":"reconnecting","error":type(exc).__name__})
                except Exception:pass
                await asyncio.sleep(delay); delay=min(60,delay*2)
