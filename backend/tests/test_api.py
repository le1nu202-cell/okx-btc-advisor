import os
import tempfile
import uuid
import asyncio
import pytest
os.environ["OKX_DISABLE_NETWORK"]="1"
os.environ["OKX_ADVISOR_DB"]=os.path.join(tempfile.gettempdir(),f"okx-advisor-{uuid.uuid4()}.db")

from fastapi.testclient import TestClient
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect
from backend import main
from backend.main import app
from backend.models import AdviceAction,BacktestRequest,Candle,DataQuality,MarketRegime,SignalAdvice
from backend.db import Database


def test_health_and_camel_case_contract():
    with TestClient(app) as client:
        health=client.get("/api/health")
        assert health.status_code==200
        assert health.json()["appId"]=="okx-btc-advisor"
        assert health.json()["version"]=="0.5.0" and health.json()["instrument"]=="BTC-USDT-SWAP"
        body=client.get("/api/market/snapshot").json()
        assert "candles1H" in body and "connectionStatus" in body and "fundingRate" in body
        missing=client.get("/api/not-a-real-endpoint")
        assert missing.status_code==404 and missing.headers["content-type"].startswith("application/json")


def test_loopback_host_validation_and_security_headers():
    with TestClient(app) as client:
        api=client.get("/api/health")
        page=client.get("/")
        assert api.headers["cache-control"]=="no-store"
        assert page.headers["cache-control"]=="no-cache"
        asset_path=next(
            (part.split('"',1)[0] for part in page.text.split('src="')[1:] if part.startswith("/assets/")),
            None,
        )
        # dist is intentionally gitignored, so a backend-only test run from a clean checkout
        # may not have an asset to exercise until the frontend build step has run.
        if asset_path:
            asset=client.get(asset_path)
            assert asset.status_code==200
            assert asset.headers["cache-control"]=="public, max-age=31536000, immutable"
        missing_asset=client.get("/assets/release-audit-missing.js")
        assert missing_asset.status_code==404
        assert missing_asset.headers["cache-control"]=="no-cache"
        for response in (api,page):
            assert response.headers["x-content-type-options"]=="nosniff"
            assert response.headers["x-frame-options"]=="DENY"
            assert response.headers["referrer-policy"]=="no-referrer"
            assert "default-src 'self'" in response.headers["content-security-policy"]
            assert "style-src-attr 'unsafe-inline'" in response.headers["content-security-policy"]
        assert client.get("/api/health",headers={"host":"localhost:8765"}).status_code==200
        assert client.get("/api/health",headers={"host":"127.0.0.1:8765"}).status_code==200
        assert client.get("/api/health",headers={"host":"[::1]:8765"}).status_code==200
        assert client.get("/api/health",headers={"host":"evil@127.0.0.1:8765"}).status_code==400
        rejected=client.get("/api/health",headers={"host":"advisor.evil.example"})
        assert rejected.status_code==400


def test_websocket_origin_host_and_connection_limit(monkeypatch):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            assert ws.receive_json()["type"]=="ready"
        with client.websocket_connect("/ws/live") as ws:
            assert ws.receive_json()["type"]=="ready"
            ws.send_bytes(b"not-text")
            with pytest.raises(WebSocketDisconnect) as binary_error:ws.receive_text()
            assert binary_error.value.code==1003
        with client.websocket_connect("/ws/live") as ws:
            assert ws.receive_json()["type"]=="ready"
            ws.send_text("汉"*400)
            with pytest.raises(WebSocketDisconnect) as size_error:ws.receive_text()
            assert size_error.value.code==1009
        with pytest.raises(WebSocketDisconnect) as origin_error:
            with client.websocket_connect("/ws/live",headers={"origin":"https://evil.example"}):pass
        assert origin_error.value.code==1008
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/live",headers={"origin":"null"}):pass
        for origin in ("http://127.0.0.1:1","http://localhost:65535","http://testserver"):
            with pytest.raises(WebSocketDisconnect) as mismatched:
                with client.websocket_connect("/ws/live",headers={"origin":origin}):pass
            assert mismatched.value.code==1008
        with client.websocket_connect(
            "/ws/live",headers={"host":"127.0.0.1:8765","origin":"http://127.0.0.1:8765"},
        ) as ws:
            assert ws.receive_json()["type"]=="ready"
        with client.websocket_connect("/ws/live",headers={"origin":"http://127.0.0.1:5173"}) as ws:
            assert ws.receive_json()["type"]=="ready"
        with pytest.raises(WebSocketDisconnect) as host_error:
            with client.websocket_connect("/ws/live",headers={"host":"evil.example"}):pass
        assert host_error.value.code==1008
        monkeypatch.setattr(main,"MAX_WEBSOCKET_CONNECTIONS",0)
        with pytest.raises(WebSocketDisconnect) as limit_error:
            with client.websocket_connect("/ws/live"):pass
        assert limit_error.value.code==1013


def test_settings_validation_and_camel_case():
    with TestClient(app) as client:
        r=client.put("/api/settings",json={"equity":1000,"riskPercent":1,"leverage":2,"notificationsEnabled":True})
        assert r.status_code==200 and r.json()["riskPercent"]==1
        assert client.put("/api/settings",json={"riskPercent":3}).status_code==422
        assert client.put("/api/settings",json={"equity":1e16}).status_code==200
        non_finite=client.put("/api/settings",content='{"equity":1e999}',headers={"content-type":"application/json"})
        assert non_finite.status_code==422
        unsupported=client.put("/api/settings",json={"customParameters":{"emaFast":10}})
        assert unsupported.status_code==422


def test_history_lookback_uses_calendar_years_and_alignment_buffer():
    from datetime import datetime,timezone
    leap=int(datetime(2024,2,29,12,tzinfo=timezone.utc).timestamp()*1000)
    since=main._history_since_ms(leap,1)
    assert datetime.fromtimestamp(since/1000,timezone.utc)==datetime(2023,2,21,12,tzinfo=timezone.utc)


def test_public_news_exposes_cross_source_and_decay_weights():
    result=main._public_news({
        "status":"partial","asOf":1_000,"newsScore":2,"clusterCount":1,"sourceCoverage":.5,
        "rawImpact":.24,"coverageAdjustedImpact":.12,
        "sourceStatus":[{"source":"okx","ok":True,"itemCount":1,"observedAt":950},{"source":"feed","ok":False,"error":"offline","observedAt":950}],
        "clusters":[{
            "clusterId":"cluster-1","title":"Bitcoin ETF flow","url":"https://example.com/story",
            "source":"test","publishedAt":900,"observedAt":950,"direction":1,"importance":4,
            "importanceScore":82,"relevance":.9,"effectiveImpact":.12,"sourceCount":3,"timeDecay":.64,
            "directionConfidence":.8,"ageHours":10,"halfLifeHours":24,
            "weightBreakdown":{"severity":.85},"weightFormula":"formula",
        }],
    })
    item=result["items"][0]
    assert item["sourceCount"]==3 and item["timeDecay"]==pytest.approx(.64)
    assert item["weightBreakdown"]["severity"]==pytest.approx(.85)
    assert result["analysis"]["sourceCoverage"]==.5
    assert result["analysis"]["sourceStatus"][0]["source"]=="okx"
    assert result["analysis"]["status"]=="partial"


def test_news_is_neutral_until_a_live_source_check_succeeds(monkeypatch):
    monkeypatch.setitem(main.runtime,"news_checked",False)
    monkeypatch.setitem(main.runtime,"news_source_status",[])
    initial=main.news_for_decision(1_000)
    assert initial["analysis"]["status"]=="unavailable" and initial["analysis"]["score"]==0
    monkeypatch.setitem(main.runtime,"news_checked",True)
    monkeypatch.setitem(main.runtime,"news_source_status",[{"source":"all","ok":False,"error":"offline"}])
    failed=main.news_for_decision(1_000)
    assert failed["analysis"]["status"]=="unavailable" and failed["analysis"]["score"]==0


def test_stale_source_success_is_neutralized(monkeypatch):
    monkeypatch.setitem(main.runtime,"news_checked",True)
    monkeypatch.setitem(main.runtime,"news_source_status",[{"source":"okx","ok":True,"itemCount":2,"observedAt":1_000}])
    monkeypatch.setitem(main.runtime,"news_last_success",1_000)
    usable,message=main._news_sources_usable(1_000+main.NEWS_SOURCE_STALE_MS)
    assert usable and message==""
    usable,message=main._news_sources_usable(1_001+main.NEWS_SOURCE_STALE_MS)
    assert not usable and "过期" in message
    result=main._neutral_news_with_source_state(message)
    assert result["analysis"]["status"]=="unavailable" and result["analysis"]["score"]==0
    assert result["analysis"]["sourceStatus"]==[{"source":"okx","ok":False,"itemCount":2,"observedAt":1_000_000,"error":message}]


def test_news_decision_uses_persisted_source_state_at_cutoff(tmp_path,monkeypatch):
    local=Database(tmp_path/"point-in-time-news.db")
    decision=1_700_000_000_000
    local.upsert_news([{"id":"n1","url":"https://example.com/news","publishedAt":decision-60_000,"observedAt":decision-30_000,"title":"Bitcoin ETF records net inflow","source":"coindesk","relevance":1}])
    local.save_news_source_status([{"source":"coindesk","ok":True,"itemCount":1,"observedAt":decision-10_000}])
    # A later failed poll must not rewrite the source coverage of the old candle.
    local.save_news_source_status([{"source":"coindesk","ok":False,"itemCount":0,"error":"offline","observedAt":decision+60_000}])
    monkeypatch.setattr(main,"db",local)
    old=main.news_for_decision(decision)
    later=main.news_for_decision(decision+60_000)
    assert old["analysis"]["status"]=="fresh" and old["analysis"]["score"]>0
    assert later["analysis"]["status"]=="unavailable" and later["analysis"]["score"]==0


def test_snapshot_never_uses_unconfirmed_future_close_as_observation_time(tmp_path,monkeypatch):
    import time
    from datetime import datetime,timezone
    local=Database(tmp_path/"snapshot.db")
    hour=int(time.time()*1000)//3_600_000*3_600_000
    local.upsert_candles(main.INSTRUMENT,[
        Candle(timestamp=hour-3_600_000,open=100,high=102,low=99,close=101,volume=10,timeframe="1H",confirm=True),
        Candle(timestamp=hour,open=101,high=103,low=100,close=102,volume=8,timeframe="1H",confirm=False),
    ])
    local.upsert_oi(main.INSTRUMENT,hour+1_800_000,1_000)
    monkeypatch.setattr(main,"db",local)
    monkeypatch.setitem(main.runtime,"price",None);monkeypatch.setitem(main.runtime,"ticker_ts",None)
    value=main.snapshot()
    expected=datetime.fromtimestamp(hour/1000,timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert value.updated_at==expected and value.candles_1h[-1].confirm is False
    assert value.stale is True


def test_stale_ticker_converts_live_candidate_to_wait(monkeypatch):
    now=1_800_000_000_000
    candidate=SignalAdvice(strategy="trend",candle_close_at=now,regime=MarketRegime.TREND,action=AdviceAction.LONG_CANDIDATE,direction_score=70,technical_score=70,confidence=80,trigger_price=100,stop_loss=95,targets=[105,110],risk_reward=[1,2],invalidation="x",explanation="x",data_quality=DataQuality(fresh=True))
    monkeypatch.setitem(main.runtime,"ticker_ts",now-30_001);monkeypatch.setitem(main.runtime,"connection","connected")
    safe=main._apply_live_market_gate(candidate,now)
    assert safe.action==AdviceAction.WAIT and safe.regime==MarketRegime.STALE
    assert safe.trigger_price is None and safe.targets==[] and safe.data_quality.fresh is False


@pytest.mark.asyncio
async def test_live_ticker_rejects_nonfinite_future_and_time_regression(monkeypatch):
    now=int(__import__("time").time()*1000)
    monkeypatch.setitem(main.runtime,"price",100.0);monkeypatch.setitem(main.runtime,"ticker_ts",now)
    await main.on_okx({"arg":{"channel":"tickers"},"data":[{"last":"NaN","ts":str(now+1)}]})
    await main.on_okx({"arg":{"channel":"tickers"},"data":[{"last":"101","ts":str(now+120_000)}]})
    await main.on_okx({"arg":{"channel":"tickers"},"data":[{"last":"99","ts":str(now-1)}]})
    assert main.runtime["price"]==100.0 and main.runtime["ticker_ts"]==now
    await main.on_okx({"arg":{"channel":"tickers"},"data":[{"last":"101","ts":str(now+1)}]})
    assert main.runtime["price"]==101.0 and main.runtime["ticker_ts"]==now+1


@pytest.mark.asyncio
async def test_invalid_live_payload_does_not_restore_stream_health(monkeypatch):
    monkeypatch.setitem(main.runtime,"connection","reconnecting")
    monkeypatch.setitem(main.runtime,"stream_status",{"public":"reconnecting","candles":"connected"})
    await main.on_okx({"_stream":"public","arg":{"channel":"tickers"},"data":[{}]})
    assert main.runtime["stream_status"]["public"]=="reconnecting"
    assert main.runtime["connection"]=="reconnecting"


@pytest.mark.asyncio
async def test_reconnect_rechecks_latest_confirmed_signal(monkeypatch):
    checked=[]
    async def fake_resync():checked.append("resynced");return True
    async def fake_publish():checked.append(True);return True
    monkeypatch.setattr(main,"resync_market_history",fake_resync)
    monkeypatch.setattr(main,"publish_current_signal",fake_publish)
    await main.on_okx({"event":"reconnected","stream":"candles"})
    assert checked==["resynced",True]


def test_public_and_candle_stream_health_are_aggregated(monkeypatch):
    monkeypatch.setitem(main.runtime,"stream_status",{"public":"starting","candles":"starting"})
    assert main.update_stream_status("public","connected")=="degraded"
    assert main.update_stream_status("candles","connected")=="connected"
    assert main.update_stream_status("candles","reconnecting")=="degraded"


@pytest.mark.asyncio
async def test_periodic_rest_reconciliation_repairs_silent_candle_channels(monkeypatch):
    calls=[];published=[]
    async def fake_candles(instrument,tf,limit,history):calls.append((tf,history));return []
    async def fake_publish():published.append(True);return True
    monkeypatch.setattr(main.client,"candles",fake_candles)
    monkeypatch.setattr(main,"publish_current_signal",fake_publish)
    assert await main.reconcile_market_once() is False
    assert calls==[("1m",False),("15m",False),("1H",False),("4H",False)] and published==[]

    async def rows(instrument,tf,limit,history):
        calls.append((tf,history))
        return [Candle(timestamp=1,open=1,high=2,low=.5,close=1.5,volume=1,timeframe=tf,confirm=True)]
    stored=[]
    monkeypatch.setattr(main.client,"candles",rows)
    monkeypatch.setattr(main.db,"upsert_candles",lambda instrument,value,retention_before=None:stored.extend(value))
    assert await main.reconcile_market_once() is True
    assert {item.timeframe for item in stored}=={"1m","15m","1H","4H"} and published==[True]


@pytest.mark.asyncio
async def test_periodic_reconciliation_uses_deep_history_for_large_gap(monkeypatch):
    step=3600_000;previous=Candle(timestamp=step,open=1,high=2,low=.5,close=1.5,volume=1,timeframe="1H",confirm=True)
    current=Candle(timestamp=200*step,open=1,high=2,low=.5,close=1.5,volume=1,timeframe="1H",confirm=True)
    calls=[]
    monkeypatch.setattr(main.db,"candles",lambda instrument,tf,limit:[previous] if tf=="1H" else [])
    async def rows(instrument,tf,limit,history):
        calls.append((tf,limit,history))
        return [current.model_copy(update={"timeframe":tf})] if tf=="1H" else []
    monkeypatch.setattr(main.client,"candles",rows)
    monkeypatch.setattr(main.db,"upsert_candles",lambda *args:None)
    monkeypatch.setattr(main,"publish_current_signal",lambda:asyncio.sleep(0,result=False))
    await main.reconcile_market_once()
    assert ("1H",100,False) in calls and ("1H",300,True) in calls


def test_technical_and_news_modules_degrade_independently():
    with TestClient(app) as client:
        technical=client.get("/api/technical/summary")
        news=client.get("/api/news?limit=5")
        assert technical.status_code==200 and technical.json()["status"]=="unavailable"
        assert news.status_code==200 and news.json()["analysis"]["status"]=="unavailable"


def test_technical_freshness_uses_last_closed_1h_age():
    now=1_700_000_000_000
    current={"ready":True,"as_of":now-3600_000,"four_hour_as_of":now-4*3600_000}
    assert main._technical_summary_is_fresh(current,now)
    assert not main._technical_summary_is_fresh({**current,"as_of":now-3*3600_000-1},now)
    assert not main._technical_summary_is_fresh({**current,"four_hour_as_of":now-8*3600_000},now)
    assert not main._technical_summary_is_fresh({**current,"ready":False},now)


def test_clear_local_data_also_clears_runtime_cache():
    main.runtime["price"],main.runtime["ticker_ts"]=123.0,456
    main.runtime["news"]={"items":[{"id":"x"}],"analysis":{"status":"fresh","score":5}}
    with TestClient(app) as client:
        assert client.delete("/api/local-data").status_code==200
        snapshot=client.get("/api/market/snapshot").json()
        news=client.get("/api/news").json()
    assert snapshot["price"] is None
    assert news["items"]==[] and news["analysis"]["status"]=="unavailable"


@pytest.mark.asyncio
async def test_clear_local_data_schedules_full_public_resync(monkeypatch):
    called=asyncio.Event()
    async def fake_resync():called.set();return True
    monkeypatch.delenv("OKX_DISABLE_NETWORK",raising=False)
    monkeypatch.setattr(main,"resync_market_history",fake_resync)
    monkeypatch.setitem(main.runtime,"resync_task",None)
    result=await main.clear_data()
    await asyncio.wait_for(called.wait(),1)
    await main.runtime["resync_task"]
    assert result=={"cleared":True,"resyncing":True}


@pytest.mark.asyncio
async def test_backtest_endpoint_allows_only_one_active_job(monkeypatch):
    started,release=asyncio.Event(),asyncio.Event()
    async def fake_execute(job_id,request):
        started.set()
        try:await release.wait()
        finally:
            if main.backtest_lock.locked():main.backtest_lock.release()
    monkeypatch.setattr(main,"execute_backtest",fake_execute)
    first=await main.backtests(BacktestRequest(years=1))
    await started.wait()
    with pytest.raises(HTTPException) as exc:
        await main.backtests(BacktestRequest(years=1))
    assert exc.value.status_code==409
    with pytest.raises(HTTPException) as clear_exc:
        await main.clear_data()
    assert clear_exc.value.status_code==409
    release.set()
    await asyncio.gather(*list(main.runtime["backtest_tasks"]))


@pytest.mark.asyncio
async def test_execute_backtest_merges_persisted_history_when_download_is_partial(tmp_path,monkeypatch):
    import time
    local=Database(tmp_path/"merged-history.db");now=int(time.time()*1000)
    one=[Candle(timestamp=now-(310-i)*3_600_000,open=100,high=102,low=99,close=101,volume=10,timeframe="1H",confirm=True) for i in range(310)]
    four=[Candle(timestamp=now-(110-i)*4*3_600_000,open=100,high=102,low=99,close=101,volume=10,timeframe="4H",confirm=True) for i in range(110)]
    rates=[(now-(50-i)*8*3_600_000,.0001) for i in range(50)]
    future_one=one[-1].model_copy(update={"timestamp":now})
    future_four=four[-1].model_copy(update={"timestamp":now})
    local.upsert_candles(main.INSTRUMENT,[*one,*four,future_one,future_four]);local.upsert_funding(main.INSTRUMENT,rates)
    local.create_backtest("merged",{"strategy":"combined","years":1,"feeBps":5,"slippageBps":5})
    monkeypatch.setattr(main,"db",local)
    async def empty(*args,**kwargs):return []
    monkeypatch.setattr(main.client,"backfill_candles",empty)
    monkeypatch.setattr(main.client,"bulk_funding_history",empty)
    monkeypatch.setattr(main.client,"funding_history",empty)
    monkeypatch.setattr(main,"_backtest_horizon_coverage",lambda *args:{"complete":True,"durationCoverage":1,"requestedStart":0,"requestedEnd":now})
    captured={}
    async def fake_run(c1,c4,funding,*args):
        captured.update(c1=len(c1),c4=len(c4),funding=len(funding))
        return {"status":"complete","validationPass":False}
    monkeypatch.setattr(main,"run_backtest_isolated",fake_run)
    await main.backtest_lock.acquire()
    await main.execute_backtest("merged",BacktestRequest(years=1))
    assert captured=={"c1":310,"c4":110,"funding":50}
    assert local.get_backtest("merged")["status"]=="complete"


def test_requested_backtest_horizon_rejects_short_partial_history():
    end=1_800_000_000_000;start=end-3*365*86400_000
    c1=[Candle(timestamp=end-(300-i)*3600_000,open=1,high=2,low=.5,close=1.5,volume=1,timeframe="1H",confirm=True) for i in range(300)]
    c4=[Candle(timestamp=end-(100-i)*4*3600_000,open=1,high=2,low=.5,close=1.5,volume=1,timeframe="4H",confirm=True) for i in range(100)]
    coverage=main._backtest_horizon_coverage(c1,c4,start,end)
    assert coverage["complete"] is False and coverage["durationCoverage"]<.1


def test_requested_backtest_horizon_accepts_full_calendar_span():
    end=1_800_000_000_000;start=end-365*86400_000
    c1=[Candle(timestamp=start+i*3600_000,open=1,high=2,low=.5,close=1.5,volume=1,timeframe="1H",confirm=True) for i in range(365*24)]
    c4=[Candle(timestamp=start+i*4*3600_000,open=1,high=2,low=.5,close=1.5,volume=1,timeframe="4H",confirm=True) for i in range(365*6)]
    coverage=main._backtest_horizon_coverage(c1,c4,start,end)
    assert coverage["complete"] is True and coverage["durationCoverage"]==pytest.approx(1)


def test_backtest_history_detail_restart_recovery_and_delete(tmp_path,monkeypatch):
    local=Database(tmp_path/"api-jobs.db");monkeypatch.setattr(main,"db",local)
    local.create_backtest("finished",{"strategy":"combined","years":3})
    local.save_backtest("finished","complete",1,"done",{"netReturn":.25})
    local.create_backtest("crashed",{"strategy":"trend","years":1})
    local.save_backtest("crashed","running",.4,"running")
    with TestClient(app) as client:
        history=client.get("/api/backtests?limit=10").json()["items"]
        by_id={row["id"]:row for row in history}
        assert by_id["finished"]["result"]["netReturn"]==.25
        assert by_id["finished"]["request"]["years"]==3
        assert by_id["crashed"]["status"]=="interrupted"
        assert client.get("/api/backtests/finished").json()["createdAt"] is not None
        assert client.delete("/api/backtests/finished").status_code==200
        assert client.get("/api/backtests/finished").status_code==404
        local.create_backtest("active",{"years":1})
        assert client.delete("/api/backtests/active").status_code==409
