import os
import tempfile
import uuid
os.environ["OKX_DISABLE_NETWORK"]="1"
os.environ["OKX_ADVISOR_DB"]=os.path.join(tempfile.gettempdir(),f"okx-advisor-{uuid.uuid4()}.db")

from fastapi.testclient import TestClient
from backend.main import app


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
