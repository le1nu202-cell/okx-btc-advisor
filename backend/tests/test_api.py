import os
import tempfile
import uuid
import asyncio
import pytest
os.environ["OKX_DISABLE_NETWORK"]="1"
os.environ["OKX_ADVISOR_DB"]=os.path.join(tempfile.gettempdir(),f"okx-advisor-{uuid.uuid4()}.db")

from fastapi.testclient import TestClient
from fastapi import HTTPException
from backend import main
from backend.main import app
from backend.models import BacktestRequest


def test_health_and_camel_case_contract():
    with TestClient(app) as client:
        assert client.get("/api/health").status_code==200
        body=client.get("/api/market/snapshot").json()
        assert "candles1H" in body and "connectionStatus" in body and "fundingRate" in body


def test_settings_validation_and_camel_case():
    with TestClient(app) as client:
        r=client.put("/api/settings",json={"equity":1000,"riskPercent":1,"leverage":2,"notificationsEnabled":True})
        assert r.status_code==200 and r.json()["riskPercent"]==1
        assert client.put("/api/settings",json={"riskPercent":3}).status_code==422


def test_technical_and_news_modules_degrade_independently():
    with TestClient(app) as client:
        technical=client.get("/api/technical/summary")
        news=client.get("/api/news?limit=5")
        assert technical.status_code==200 and technical.json()["status"]=="unavailable"
        assert news.status_code==200 and news.json()["analysis"]["status"]=="unavailable"


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
    release.set()
    await asyncio.gather(*list(main.runtime["backtest_tasks"]))
