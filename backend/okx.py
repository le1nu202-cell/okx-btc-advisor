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
    MIN_MARKET_TS = 1_230_768_000_000
    BULK_DOWNLOAD_HOSTS = {"static.okx.com"}
    MAX_BULK_URLS = 64
    MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
    MAX_ZIP_ENTRIES = 32
    MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
    MAX_COMPRESSION_RATIO = 200

    def __init__(self, base_url="https://www.okx.com", ws_url="wss://ws.okx.com:8443/ws/v5/public", business_ws_url="wss://ws.okx.com:8443/ws/v5/business", timeout=20):
        self.base_url=base_url.rstrip("/"); self.ws_url=ws_url; self.business_ws_url=business_ws_url; self.timeout=timeout

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

    @classmethod
    def parse_candles(cls,rows,timeframe,now_ms=None):
        if timeframe not in {"1H","4H"}:
            raise ValueError(f"unsupported candle timeframe: {timeframe}")
        found={}
        now_ms=int(time.time()*1000) if now_ms is None else int(now_ms)
        duration_ms=3600_000 if timeframe=="1H" else 4*3600_000
        for r in rows:
            if len(r)<9:continue
            try:
                ts=int(r[0]); values=[float(r[i]) for i in range(1,6)]
                volume_ccy=float(r[6]) if r[6] else None
            except (TypeError,ValueError,OverflowError):
                continue
            o,h,l,c_value,volume=values
            confirmed=str(r[8])=="1"
            finite=all(math.isfinite(x) for x in values) and (volume_ccy is None or math.isfinite(volume_ccy))
            # An unclosed candle may be charted, but a confirmed candle whose
            # close is still in the future must never enter advice/backtests.
            valid_time=(
                cls.MIN_MARKET_TS<=ts<=now_ms+60_000
                and (not confirmed or ts+duration_ms<=now_ms+60_000)
            )
            valid_prices=min(o,c_value)>=l>0 and max(o,c_value)<=h and h>0
            if not finite or not valid_time or not valid_prices or volume<0 or (volume_ccy is not None and volume_ccy<0):
                continue
            c=Candle(timestamp=ts,open=o,high=h,low=l,close=c_value,volume=volume,volume_ccy=volume_ccy,confirm=confirmed,timeframe=timeframe)
            previous=found.get(c.timestamp)
            if previous is None:
                found[c.timestamp]=c
                continue
            # Confirmation is an irreversible quality upgrade. At the same
            # quality level cumulative range/volume must not move backwards;
            # this prevents delayed, poorer data from replacing a richer row.
            if c.confirm != previous.confirm:
                if c.confirm:
                    found[c.timestamp]=c
                continue
            loses_currency_volume=previous.volume_ccy is not None and c.volume_ccy is None
            regresses_cumulative_data=(
                c.volume < previous.volume or c.high < previous.high or c.low > previous.low
                or (previous.volume_ccy is not None and c.volume_ccy is not None
                    and c.volume_ccy < previous.volume_ccy)
            )
            if not loses_currency_volume and not regresses_cumulative_data:
                found[c.timestamp]=c
        return [found[k] for k in sorted(found)]

    @classmethod
    def parse_ticker(cls,row,now_ms=None):
        now_ms=int(time.time()*1000) if now_ms is None else int(now_ms)
        try:price=float(row["last"]);ts=int(row["ts"])
        except (KeyError,TypeError,ValueError,OverflowError):return None
        if not math.isfinite(price) or price<=0 or not cls.MIN_MARKET_TS<=ts<=now_ms+60_000:return None
        return price,ts

    @classmethod
    def parse_funding_rows(cls,rows,now_ms=None,max_future_ms=24*3600_000):
        now_ms=int(time.time()*1000) if now_ms is None else int(now_ms);result={}
        for row in rows:
            try:ts=int(row["fundingTime"]);rate=float(row["fundingRate"])
            except (KeyError,TypeError,ValueError,OverflowError):continue
            if cls.MIN_MARKET_TS<=ts<=now_ms+max_future_ms and math.isfinite(rate) and abs(rate)<=1:
                result[ts]=rate
        return sorted(result.items())

    @classmethod
    def parse_open_interest(cls,row,now_ms=None):
        now_ms=int(time.time()*1000) if now_ms is None else int(now_ms)
        try:ts=int(row["ts"]);value=float(row.get("oiCcy") or row["oi"])
        except (KeyError,TypeError,ValueError,OverflowError):return None
        if not cls.MIN_MARKET_TS<=ts<=now_ms+60_000 or not math.isfinite(value) or value<0:return None
        return ts,value

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
        d=await self._get("/api/v5/market/ticker",{"instId":instrument}); return self.parse_ticker(d[0]) if d else None

    async def funding_history(self,instrument="BTC-USDT-SWAP",limit=100,after=None):
        p={"instId":instrument,"limit":min(100,limit)}
        if after:p["after"]=str(after)
        return self.parse_funding_rows(await self._get("/api/v5/public/funding-rate-history",p))

    async def backfill_funding_history(self,instrument="BTC-USDT-SWAP",since_ms=0,max_pages=8):
        """Page public funding observations far enough back for 90-day percentiles."""
        found={};after=None
        for _ in range(max_pages):
            rows=await self.funding_history(instrument,100,after)
            if not rows:break
            for timestamp,rate in rows:found[timestamp]=rate
            oldest=min(timestamp for timestamp,_ in rows)
            if oldest<=since_ms:break
            if after is not None and oldest>=after:break
            after=oldest
            await asyncio.sleep(.12)
        return [(timestamp,found[timestamp]) for timestamp in sorted(found) if timestamp>=since_ms]

    async def open_interest(self,instrument="BTC-USDT-SWAP"):
        d=await self._get("/api/v5/public/open-interest",{"instType":"SWAP","instId":instrument})
        return self.parse_open_interest(d[0]) if d else None

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
                        rate=float(row["funding_rate"])
                        if self.MIN_MARKET_TS<=ts<=int(time.time()*1000)+24*3600_000 and math.isfinite(rate) and abs(rate)<=1:out[ts]=rate
                    except (KeyError,TypeError,ValueError):continue
        return sorted(out.items())

    async def _stream_endpoint(self,url:str,args:list[dict],stream_name:str,on_message:Callable[[dict],Awaitable[None]],stop:asyncio.Event):
        delay=1
        while not stop.is_set():
            try:
                async with websockets.connect(url,ping_interval=20,ping_timeout=15) as ws:
                    await ws.send(json.dumps({"op":"subscribe","args":args}))
                    healthy=False
                    async for raw in ws:
                        try:
                            msg=json.loads(raw)
                            if not isinstance(msg,dict):continue
                            if msg.get("event")=="error":
                                raise OKXError(msg.get("msg") or "OKX rejected the public subscription")
                            if "data" in msg and self._valid_stream_message(msg):
                                # A TCP handshake alone is not a healthy market stream. Reset
                                # backoff and announce recovery only after OKX sends real data.
                                if not healthy:
                                    healthy=True;delay=1
                                    await on_message({"event":"reconnected","stream":stream_name})
                                await on_message({**msg,"_stream":stream_name})
                            elif "data" in msg:
                                await on_message({"event":"message_error","stream":stream_name,"error":"InvalidMarketData"})
                        except OKXError:
                            raise
                        except Exception as exc:
                            # A malformed frame or one failed handler invocation must not kill
                            # the sole long-lived market stream.
                            try:await on_message({"event":"message_error","error":type(exc).__name__})
                            except Exception:pass
                        if stop.is_set():break
                    if not stop.is_set():
                        raise OKXError("public market stream closed")
            except Exception as exc:
                try:await on_message({"event":"reconnecting","stream":stream_name,"error":type(exc).__name__})
                except Exception:pass
                if not stop.is_set():
                    await asyncio.sleep(delay); delay=min(60,delay*2)

    def _valid_stream_message(self,msg:dict)->bool:
        data=msg.get("data")
        if not isinstance(data,list) or not data:return False
        channel=str(msg.get("arg",{}).get("channel",''))
        try:
            if channel=="tickers":return self.parse_ticker(data[0]) is not None
            if channel=="funding-rate":return bool(self.parse_funding_rows(data,max_future_ms=48*3600_000))
            if channel=="open-interest":return self.parse_open_interest(data[0]) is not None
            if channel in {"candle1H","candle4H"}:return bool(self.parse_candles(data,"1H" if channel=="candle1H" else "4H"))
        except (TypeError,ValueError,IndexError):return False
        return False

    async def stream(self,on_message:Callable[[dict],Awaitable[None]],stop:asyncio.Event):
        public_args=[{"channel":"tickers","instId":"BTC-USDT-SWAP"},{"channel":"funding-rate","instId":"BTC-USDT-SWAP"},{"channel":"open-interest","instId":"BTC-USDT-SWAP"}]
        candle_args=[{"channel":"candle1H","instId":"BTC-USDT-SWAP"},{"channel":"candle4H","instId":"BTC-USDT-SWAP"}]
        await asyncio.gather(
            self._stream_endpoint(self.ws_url,public_args,"public",on_message,stop),
            self._stream_endpoint(self.business_ws_url,candle_args,"candles",on_message,stop),
        )
