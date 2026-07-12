import asyncio

import pytest

from backend.backtest import run_backtest
from backend.db import Database
from backend.models import Candle,Settings
from backend.okx import OKXError,OKXPublicClient


def c(ts,confirm=True):return Candle(timestamp=ts,open=1,high=2,low=.5,close=1.5,volume=10,timeframe="1H",confirm=confirm)


def test_db_dedupe_order_and_settings(tmp_path):
    db=Database(tmp_path/"x.db");db.upsert_candles("X",[c(2),c(1),c(2)])
    assert [x.timestamp for x in db.candles("X","1H")]==[1,2]
    s=Settings(equity=100,risk_percent=.5,leverage=1);db.put_settings(s);assert db.get_settings()==s


def test_okx_parse_dedupe_confirm():
    rows=[["1609462800000","1","2",".5","1.5","10","15","0","1"],["1609459200000","1","2",".5","1.5","10","15","0","0"]]
    got=OKXPublicClient.parse_candles(rows,"1H")
    assert [x.timestamp for x in got]==[1609459200000,1609462800000] and not got[0].confirm and got[1].confirm


def test_okx_parse_rejects_nonfinite_invalid_ohlc_and_time():
    valid_ts="1609459200000"
    rows=[
        [valid_ts,"nan","2",".5","1.5","10","15","0","1"],
        [valid_ts,"3","2",".5","1.5","10","15","0","1"],
        ["-2","1","2",".5","1.5","10","15","0","1"],
        [valid_ts,"1","2",".5","1.5","-1","15","0","1"],
    ]
    assert OKXPublicClient.parse_candles(rows,"1H")==[]
    with pytest.raises(ValueError):OKXPublicClient.parse_candles([],"1m")


@pytest.mark.asyncio
async def test_bulk_funding_rejects_response_driven_untrusted_url(monkeypatch):
    client=OKXPublicClient()
    async def fake_get(*args,**kwargs):return [{"url":"http://127.0.0.1/private.zip"}]
    monkeypatch.setattr(client,"_get",fake_get)
    with pytest.raises(OKXError,match="untrusted"):
        await client.bulk_funding_history(1,2)


@pytest.mark.asyncio
async def test_bulk_funding_rejects_oversized_download_before_body(monkeypatch):
    client=OKXPublicClient()
    async def fake_get(*args,**kwargs):return [{"url":"https://static.okx.com/a.zip"}]
    monkeypatch.setattr(client,"_get",fake_get)

    class Response:
        url=type("URL",(),{"scheme":"https","host":"static.okx.com"})()
        headers={"content-length":str(client.MAX_DOWNLOAD_BYTES+1)}
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def raise_for_status(self):pass
        async def aiter_bytes(self):
            yield b"should not be read"
    class FakeHTTPClient:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def stream(self,*args,**kwargs):return Response()
    monkeypatch.setattr("backend.okx.httpx.AsyncClient",lambda **kwargs:FakeHTTPClient())
    with pytest.raises(OKXError,match="size limit"):
        await client.bulk_funding_history(1,2)


@pytest.mark.asyncio
async def test_stream_survives_bad_frame_and_handler_error(monkeypatch):
    stop=asyncio.Event();events=[]
    class FakeWebSocket:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,value):pass
        def __aiter__(self):
            async def frames():
                yield "not-json"
                yield '{"data":[{}]}'
            return frames()
    monkeypatch.setattr("backend.okx.websockets.connect",lambda *args,**kwargs:FakeWebSocket())
    async def handler(message):
        events.append(message.get("event","data"))
        if "data" in message:raise RuntimeError("handler failed")
        if message.get("event")=="message_error" and events.count("message_error")>=2:stop.set()
    await asyncio.wait_for(OKXPublicClient().stream(handler,stop),1)
    assert events.count("message_error")==2


def test_backtest_insufficient_is_not_validated():
    out=run_backtest([c(i) for i in range(10)],[c(i) for i in range(10)],[])
    assert out["validationPass"] is False


def test_news_point_in_time_cutoff_and_clear(tmp_path):
    db=Database(tmp_path/"news.db")
    db.upsert_news([{"id":"n1","url":"https://example.com/n1","publishedAt":1000,"observedAt":1500,"title":"Bitcoin event","source":"test","relevance":1}])
    assert db.news_items(decision_at=1499)==[]
    assert len(db.news_items(decision_at=1500))==1
    db.clear_local_data()
    assert db.news_items()==[]


def test_news_upsert_preserves_first_observation_and_handles_same_url_new_id(tmp_path):
    db=Database(tmp_path/"news-stable.db")
    base={"id":"first","url":"https://example.com/story","publishedAt":1000,"observedAt":1500,"title":"Bitcoin event","source":"test","relevance":1}
    db.upsert_news([base])
    db.upsert_news([{**base,"id":"changed-cluster","observedAt":2500,"title":"Bitcoin event updated"}])
    rows=db.news_items(decision_at=1500)
    assert len(rows)==1
    assert rows[0]["id"]=="first"
    assert rows[0]["observedAt"]==1500
    assert rows[0]["title"]=="Bitcoin event updated"
