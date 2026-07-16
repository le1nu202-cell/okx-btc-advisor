import asyncio
import json
import sqlite3
import time

import pytest

from backend.backtest import run_backtest
from backend.db import Database
from backend.models import AdviceAction,Candle,DataQuality,MarketRegime,Settings,SignalAdvice
from backend.okx import OKXError,OKXPublicClient


def c(ts,confirm=True):return Candle(timestamp=ts,open=1,high=2,low=.5,close=1.5,volume=10,timeframe="1H",confirm=confirm)


@pytest.mark.parametrize("kwargs",[
    {"base_url":"http://www.okx.com"},
    {"base_url":"https://www.okx.com.evil.test"},
    {"base_url":"https://www.okx.com:443"},
    {"base_url":"https://user@www.okx.com"},
    {"ws_url":"wss://ws.okx.com:8443/ws/v5/private"},
    {"ws_url":"wss://evil.test:8443/ws/v5/public"},
    {"business_ws_url":"wss://ws.okx.com:8443/ws/v5/public"},
])
def test_okx_client_rejects_nonofficial_or_private_endpoints(kwargs):
    with pytest.raises(ValueError):
        OKXPublicClient(**kwargs)


def test_okx_client_accepts_only_the_official_public_market_endpoints():
    client=OKXPublicClient(
        base_url="https://www.okx.com/",
        ws_url="wss://ws.okx.com:8443/ws/v5/public",
        business_ws_url="wss://ws.okx.com:8443/ws/v5/business",
    )
    assert client.base_url=="https://www.okx.com"
    assert client.ws_url.endswith("/ws/v5/public")
    assert client.business_ws_url.endswith("/ws/v5/business")


def test_db_dedupe_order_and_settings(tmp_path):
    db=Database(tmp_path/"x.db");db.upsert_candles("X",[c(2),c(1),c(2)])
    assert [x.timestamp for x in db.candles("X","1H")]==[1,2]
    assert [x.timestamp for x in db.candles_since("X","1H",2,confirmed_only=True)]==[2]
    s=Settings(equity=100,risk_percent=.5,leverage=1);db.put_settings(s);assert db.get_settings()==s


def test_candle_upsert_never_downgrades_confirmed_or_accepts_stale_live_data(tmp_path):
    db=Database(tmp_path/"candle-quality.db")
    live=Candle(timestamp=1,open=100,high=108,low=95,close=106,volume=80,volume_ccy=40,timeframe="1H",confirm=False)
    stale=live.model_copy(update={"high":107,"low":96,"close":104,"volume":70,"volume_ccy":35})
    confirmed=live.model_copy(update={"close":105,"volume":85,"volume_ccy":42,"confirm":True})
    poorer_confirmed=confirmed.model_copy(update={"high":107,"low":96,"close":104,"volume":75,"volume_ccy":38})
    late_unconfirmed=live.model_copy(update={"high":110,"low":90,"close":109,"volume":100,"volume_ccy":50})

    db.upsert_candles("X",[live,stale])
    assert db.candles("X","1H")==[live]
    db.upsert_candles("X",[confirmed,poorer_confirmed,late_unconfirmed])
    assert db.candles("X","1H")==[confirmed]


def test_signal_dedupe_uses_direction_not_watch_candidate_level(tmp_path):
    db=Database(tmp_path/"signals.db")
    base=dict(strategy="trend",candle_close_at=123,regime=MarketRegime.TREND,direction_score=40,
              confidence=30,invalidation="x",explanation="x",data_quality=DataQuality(fresh=True))
    watch=SignalAdvice(action=AdviceAction.WATCH_LONG,**base)
    candidate=SignalAdvice(action=AdviceAction.LONG_CANDIDATE,**{**base,"direction_score":70})
    opposite=SignalAdvice(action=AdviceAction.WATCH_SHORT,**{**base,"direction_score":-40})
    assert db.save_signal(watch) is True
    assert db.save_signal(candidate) is False
    assert db.save_signal(opposite) is True
    assert len(db.signal_history())==2


def test_okx_parse_dedupe_confirm():
    rows=[["1609462800000","1","2",".5","1.5","10","15","0","1"],["1609459200000","1","2",".5","1.5","10","15","0","0"]]
    got=OKXPublicClient.parse_candles(rows,"1H")
    assert [x.timestamp for x in got]==[1609459200000,1609462800000] and not got[0].confirm and got[1].confirm


def test_okx_parse_duplicate_never_downgrades_confirmed_candle():
    ts="1609459200000"
    confirmed=[ts,"100","108","95","105","85","42","0","1"]
    poorer_confirmed=[ts,"100","107","96","104","75","38","0","1"]
    delayed_live=[ts,"100","110","90","109","100","50","0","0"]
    got=OKXPublicClient.parse_candles([confirmed,poorer_confirmed,delayed_live],"1H")
    assert len(got)==1 and got[0].confirm is True
    assert got[0].close==105 and got[0].volume==85


def test_okx_parse_rejects_nonfinite_invalid_ohlc_and_time():
    valid_ts="1609459200000"
    rows=[
        [valid_ts,"nan","2",".5","1.5","10","15","0","1"],
        [valid_ts,"3","2",".5","1.5","10","15","0","1"],
        ["-2","1","2",".5","1.5","10","15","0","1"],
        [valid_ts,"1","2",".5","1.5","-1","15","0","1"],
    ]
    assert OKXPublicClient.parse_candles(rows,"1H")==[]
    assert OKXPublicClient.parse_candles([],"1m")==[]
    assert OKXPublicClient.parse_candles([],"15m")==[]
    with pytest.raises(ValueError):OKXPublicClient.parse_candles([],"5m")


def test_okx_parse_never_accepts_a_future_confirmed_candle():
    now=1_800_000_000_000
    def row(timestamp,confirm):
        return [str(timestamp),"100","101","99","100","10","10","0",str(confirm)]
    got=OKXPublicClient.parse_candles([
        row(now-3600_000,1),
        row(now-30*60_000,1),
        row(now-30*60_000,0),
    ],"1H",now_ms=now)
    assert [(item.timestamp,item.confirm) for item in got]==[(now-3600_000,True),(now-30*60_000,False)]


def test_okx_parses_only_finite_current_public_metrics():
    now=1_800_000_000_000
    assert OKXPublicClient.parse_ticker({"last":"100","ts":str(now)},now)==(100.0,now)
    assert OKXPublicClient.parse_ticker({"last":"NaN","ts":str(now)},now) is None
    assert OKXPublicClient.parse_ticker({"last":"-1","ts":str(now)},now) is None
    assert OKXPublicClient.parse_ticker({"last":"100","ts":str(now+60_001)},now) is None
    funding=OKXPublicClient.parse_funding_rows([
        {"fundingTime":str(now),"fundingRate":"0.001"},
        {"fundingTime":str(now),"fundingRate":"NaN"},
    ],now)
    assert funding==[(now,.001)]
    assert OKXPublicClient.parse_open_interest({"ts":str(now),"oiCcy":"12"},now)==(now,12.0)
    assert OKXPublicClient.parse_open_interest({"ts":str(now),"oiCcy":"-1"},now) is None


def test_database_rejects_nonfinite_funding_and_open_interest(tmp_path):
    db=Database(tmp_path/"finite-market.db")
    db.upsert_funding("X",[(1,.001),(2,float("nan")),(3,2.0)])
    db.upsert_oi("X",1,float("inf"));db.upsert_oi("X",2,-1);db.upsert_oi("X",3,4)
    assert db.funding_rows("X")==[(1,.001)]
    assert db.latest_oi("X")== (3,4.0)


def test_open_interest_context_requires_a_full_local_lookback(tmp_path):
    db=Database(tmp_path/"oi-context.db");day=24*3600_000
    db.upsert_oi("X",100,100);db.upsert_oi("X",100+day-1,105)
    assert db.open_interest_context("X") is None
    db.upsert_oi("X",100+day,110)
    context=db.open_interest_context("X")
    assert context["baselineTime"]==100 and context["changePercent"]==pytest.approx(.1)


def test_open_interest_context_is_as_of_safe_and_rejects_stale_samples(tmp_path):
    db=Database(tmp_path/"oi-as-of.db");day=24*3600_000;minute=60_000
    db.upsert_oi("X",100,100)
    db.upsert_oi("X",100+day,110)
    db.upsert_oi("X",100+day+5*minute,120)
    context=db.open_interest_context("X",as_of_ms=100+day)
    assert context["latestTime"]==100+day and context["changePercent"]==pytest.approx(.1)
    assert db.open_interest_context("X",as_of_ms=100+day+36*minute) is None

    sparse=Database(tmp_path/"oi-sparse.db")
    sparse.upsert_oi("X",100,100);sparse.upsert_oi("X",100+day+31*minute,110)
    assert sparse.open_interest_context("X") is None


def test_open_interest_sampling_compacts_each_bucket_and_rejects_time_regression(tmp_path):
    db=Database(tmp_path/"oi-sampling.db");bucket=5*60_000
    db.upsert_oi("X",bucket+1,100);db.upsert_oi("X",bucket+2,101);db.upsert_oi("X",bucket,999)
    assert db.latest_oi("X")== (bucket+2,101.0)
    with db.session() as con:count=con.execute("SELECT COUNT(*) FROM open_interest WHERE instrument='X'").fetchone()[0]
    assert count==1


def test_open_interest_v3_migration_keeps_latest_row_per_five_minute_bucket(tmp_path):
    path=tmp_path/"oi-v2.db";con=sqlite3.connect(path)
    con.execute("CREATE TABLE open_interest (instrument TEXT NOT NULL,ts INTEGER NOT NULL,value REAL NOT NULL,PRIMARY KEY(instrument,ts))")
    con.executemany("INSERT INTO open_interest VALUES('X',?,?)",[(300_001,1),(300_002,2),(600_001,3)])
    con.execute("PRAGMA user_version=2");con.commit();con.close()
    db=Database(path)
    with db.session() as current:
        rows=current.execute("SELECT ts,value FROM open_interest ORDER BY ts").fetchall()
    assert [(row["ts"],row["value"]) for row in rows]==[(300_002,2),(600_001,3)]


@pytest.mark.asyncio
async def test_bulk_funding_rejects_response_driven_untrusted_url(monkeypatch):
    client=OKXPublicClient()
    async def fake_get(*args,**kwargs):return [{"url":"http://127.0.0.1/private.zip"}]
    monkeypatch.setattr(client,"_get",fake_get)
    with pytest.raises(OKXError,match="untrusted"):
        await client.bulk_funding_history(1,2)


@pytest.mark.asyncio
@pytest.mark.parametrize("url",[
    "https://static.okx.com:4443/a.zip",
    "https://user@static.okx.com/a.zip",
])
async def test_bulk_funding_rejects_nonstandard_port_and_userinfo(monkeypatch,url):
    client=OKXPublicClient()
    async def fake_get(*args,**kwargs):return [{"url":url}]
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
        is_redirect=False
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
async def test_bulk_funding_rejects_redirect_without_following_it(monkeypatch):
    client=OKXPublicClient()
    async def fake_get(*args,**kwargs):return [{"url":"https://static.okx.com/a.zip"}]
    monkeypatch.setattr(client,"_get",fake_get)

    class Response:
        url=type("URL",(),{"scheme":"https","host":"static.okx.com"})()
        is_redirect=True
        headers={"location":"https://evil.test/a.zip"}
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def raise_for_status(self):raise AssertionError("redirect must be rejected first")
        async def aiter_bytes(self):
            raise AssertionError("redirect body must not be read")
    class FakeHTTPClient:
        def __init__(self,**kwargs):
            assert kwargs["follow_redirects"] is False
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def stream(self,*args,**kwargs):return Response()
    monkeypatch.setattr("backend.okx.httpx.AsyncClient",FakeHTTPClient)
    with pytest.raises(OKXError,match="redirects are not allowed"):
        await client.bulk_funding_history(1,2)


@pytest.mark.asyncio
async def test_funding_backfill_pages_deduplicates_and_stops_at_cutoff(monkeypatch):
    client=OKXPublicClient();calls=[]
    async def page(instrument,limit,after):
        calls.append(after)
        return [(200,.002),(300,.003)] if after is None else [(100,.001),(200,.002)]
    async def no_wait(delay):pass
    monkeypatch.setattr(client,"funding_history",page)
    monkeypatch.setattr("backend.okx.asyncio.sleep",no_wait)
    assert await client.backfill_funding_history("X",100)==[(100,.001),(200,.002),(300,.003)]
    assert calls==[None,200]


@pytest.mark.asyncio
async def test_stream_survives_bad_frame_and_handler_error(monkeypatch):
    stop=asyncio.Event();events=[]
    valid=json.dumps({"arg":{"channel":"tickers"},"data":[{"last":"100","ts":str(int(time.time()*1000))}]})
    class FakeWebSocket:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,value):pass
        def __aiter__(self):
            async def frames():
                yield "not-json"
                yield valid
            return frames()
    monkeypatch.setattr("backend.okx.websockets.connect",lambda *args,**kwargs:FakeWebSocket())
    async def handler(message):
        events.append(message.get("event","data"))
        if "data" in message:raise RuntimeError("handler failed")
        if message.get("event")=="message_error" and events.count("message_error")>=2:stop.set()
    client=OKXPublicClient()
    await asyncio.wait_for(client._stream_endpoint(client.ws_url,[],"public",handler,stop),1)
    assert events.count("message_error")==2


@pytest.mark.asyncio
async def test_stream_disables_redirects_and_pins_the_official_socket(monkeypatch):
    stop=asyncio.Event(); captured={}
    valid=json.dumps({"arg":{"channel":"tickers"},"data":[{"last":"100","ts":str(int(time.time()*1000))}]})
    class Connector:
        process_redirect=None
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,value):pass
        def __aiter__(self):
            async def frames():yield valid
            return frames()
    connector=Connector()
    def connect(url,**kwargs):
        captured.update({"url":url,**kwargs})
        return connector
    monkeypatch.setattr("backend.okx.websockets.connect",connect)
    async def handler(message):
        if "data" in message:stop.set()
    client=OKXPublicClient()
    await client._stream_endpoint(client.ws_url,[],"public",handler,stop)
    redirect=RuntimeError("redirect")
    assert connector.process_redirect(redirect) is redirect
    assert captured["url"]==client.ws_url
    assert captured["host"]=="ws.okx.com" and captured["port"]==8443


@pytest.mark.asyncio
async def test_stream_backoff_resets_only_after_valid_market_data(monkeypatch):
    stop=asyncio.Event();sleeps=[];connection=0
    class FakeWebSocket:
        def __init__(self,frames):self.frames=frames
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,value):pass
        def __aiter__(self):
            async def iterate():
                for frame in self.frames:yield frame
            return iterate()
    def connect(*args,**kwargs):
        nonlocal connection
        connection+=1
        # The third connection proves health with a real data frame. Its later
        # close must therefore restart at the one-second delay.
        valid=json.dumps({"arg":{"channel":"tickers"},"data":[{"last":"100","ts":str(int(time.time()*1000))}]})
        frames=['{"arg":{"channel":"tickers"},"data":[{}]}'] if connection==2 else [valid] if connection==3 else []
        return FakeWebSocket(frames)
    async def sleep(delay):
        sleeps.append(delay)
        if len(sleeps)==4:stop.set()
    async def handler(message):pass
    monkeypatch.setattr("backend.okx.websockets.connect",connect)
    monkeypatch.setattr("backend.okx.asyncio.sleep",sleep)
    client=OKXPublicClient()
    await client._stream_endpoint(client.ws_url,[],"public",handler,stop)
    assert sleeps==[1,2,1,2]


@pytest.mark.asyncio
async def test_stream_splits_candles_onto_the_public_business_endpoint(monkeypatch):
    client=OKXPublicClient();stop=asyncio.Event();calls=[]
    async def endpoint(url,args,name,on_message,event):calls.append((url,args,name));event.set()
    monkeypatch.setattr(client,"_stream_endpoint",endpoint)
    async def handler(message):pass
    await client.stream(handler,stop)
    by_name={name:(url,args) for url,args,name in calls}
    assert by_name["public"][0]==client.ws_url and by_name["candles"][0]==client.business_ws_url
    assert all(not item["channel"].startswith("candle") for item in by_name["public"][1])
    assert {item["channel"] for item in by_name["candles"][1]}=={"candle1m","candle15m","candle1H","candle4H"}


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
    # Without a versioned article history the first-seen payload must remain
    # immutable; otherwise the later title would leak into the earlier cutoff.
    assert rows[0]["title"]=="Bitcoin event"


def test_news_source_health_is_point_in_time_and_expires(tmp_path):
    db=Database(tmp_path/"news-source-status.db")
    observed=1_700_000_000_000
    assert db.save_news_source_status([{"source":"okx","ok":True,"itemCount":2,"observedAt":observed}])==1
    assert db.news_source_status(observed-1,15*60_000)==[]
    fresh=db.news_source_status(observed+15*60_000,15*60_000)
    assert fresh[0]["ok"] is True and fresh[0]["observedAt"]==observed
    stale=db.news_source_status(observed+15*60_000+1,15*60_000)
    assert stale[0]["ok"] is False and "过期" in stale[0]["error"]
    db.clear_local_data()
    assert db.news_source_status(observed+1,15*60_000)==[]


def test_backtest_schema_migrates_old_database_and_marks_interrupted(tmp_path):
    path=tmp_path/"old.db"
    con=sqlite3.connect(path)
    con.execute("CREATE TABLE backtests (id TEXT PRIMARY KEY,status TEXT NOT NULL,progress REAL NOT NULL,message TEXT NOT NULL,payload TEXT)")
    con.execute("INSERT INTO backtests VALUES('old','running',.4,'work',NULL)")
    con.commit();con.close()
    db=Database(path)
    with db.session() as current:
        columns={row["name"] for row in current.execute("PRAGMA table_info(backtests)")}
        version=current.execute("PRAGMA user_version").fetchone()[0]
    assert {"request_payload","created_at","updated_at","started_at","finished_at"}<=columns
    assert version>=2
    assert db.interrupt_incomplete_backtests()==1
    row=db.get_backtest("old")
    assert row["status"]=="interrupted" and row["finished_at"] is not None


def test_backtest_result_history_survives_database_reopen_and_safe_delete(tmp_path):
    path=tmp_path/"jobs.db";db=Database(path)
    db.create_backtest("job",{"strategy":"combined","years":3})
    db.save_backtest("job","running",.5,"running")
    assert db.delete_backtest("job") is False
    db.save_backtest("job","complete",1,"done",{"netReturn":.12})
    reopened=Database(path)
    row=reopened.backtest_history(1)[0]
    assert row["id"]=="job" and json.loads(row["payload"])["netReturn"]==.12
    assert json.loads(row["request_payload"])["years"]==3
    assert row["created_at"]<=row["updated_at"] and row["finished_at"] is not None
    assert reopened.delete_backtest("job") is True
    assert reopened.get_backtest("job") is None


def test_backtest_json_rejects_nonfinite_and_legacy_corruption_is_safe(tmp_path):
    db=Database(tmp_path/"finite-jobs.db")
    db.create_backtest("job",{"years":3})
    with pytest.raises(ValueError,match="Out of range float"):
        db.save_backtest("job","complete",1,"bad",{"netReturn":float("nan")})
    with db.session() as con:
        con.execute("UPDATE backtests SET status='complete',payload=? WHERE id='job'",('{"netReturn":NaN}',))
    from backend.main import _backtest_response
    response=_backtest_response(db.get_backtest("job"))
    assert response["result"] is None
    assert "损坏" in response["message"]


def test_market_analysis_snapshots_are_immutable_idempotent_and_cleared(tmp_path):
    db=Database(tmp_path/"market-analysis.db")
    first={
        "instrument":"BTC-USDT-SWAP","asOf":1_800_000_000_000,
        "modelVersion":"indicator-regime-v06.0.0","overallBias":"SHORT_BIAS",
        "actionContext":"WAIT","compositeScore":-42.0,
        "dataQuality":{"status":"NORMAL"},"snapshotTrigger":"15m_CLOSE",
    }
    second={**first,"asOf":first["asOf"]+900_000,"compositeScore":-35.0}
    assert db.save_market_analysis_snapshot(first) is True
    assert db.save_market_analysis_snapshot(first) is False
    assert db.save_market_analysis_snapshot({**first,"snapshotTrigger":"REST_RECONCILE"}) is False
    assert db.save_market_analysis_snapshot(second) is True
    history=db.market_analysis_history("BTC-USDT-SWAP","indicator-regime-v06.0.0",10)
    assert [row["asOf"] for row in history]==[first["asOf"],second["asOf"]]
    assert all(row["snapshotSource"]=="LIVE_OBSERVED" for row in history)
    db.clear_local_data()
    assert db.market_analysis_history("BTC-USDT-SWAP","indicator-regime-v06.0.0",10)==[]


def test_market_analysis_snapshot_retention_uses_decision_time(tmp_path,monkeypatch):
    import backend.db as db_module

    db=Database(tmp_path/"market-analysis-retention.db")
    now=1_800_000_000_000
    retention=180*86400_000
    clock=[now-retention-10_000]
    monkeypatch.setattr(db_module.time,"time",lambda:clock[0]/1000)
    base={
        "instrument":"BTC-USDT-SWAP","modelVersion":"indicator-regime-v06.0.0",
        "overallBias":"NEUTRAL","dataQuality":{"status":"NORMAL"},
    }
    assert db.save_market_analysis_snapshot({**base,"asOf":clock[0]}) is True
    clock[0]=now
    assert db.save_market_analysis_snapshot({**base,"asOf":now}) is True
    history=db.market_analysis_history("BTC-USDT-SWAP","indicator-regime-v06.0.0",10)
    assert [item["asOf"] for item in history]==[now]
