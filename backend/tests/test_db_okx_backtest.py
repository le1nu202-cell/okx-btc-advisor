import asyncio

from backend.backtest import run_backtest
from backend.db import Database
from backend.models import Candle,Settings
from backend.okx import OKXPublicClient


def c(ts,confirm=True):return Candle(timestamp=ts,open=1,high=2,low=.5,close=1.5,volume=10,timeframe="1H",confirm=confirm)


def test_db_dedupe_order_and_settings(tmp_path):
    db=Database(tmp_path/"x.db");db.upsert_candles("X",[c(2),c(1),c(2)])
    assert [x.timestamp for x in db.candles("X","1H")]==[1,2]
    s=Settings(equity=100,risk_percent=.5,leverage=1);db.put_settings(s);assert db.get_settings()==s


def test_okx_parse_dedupe_confirm():
    rows=[["2","1","2",".5","1.5","10","15","0","1"],["1","1","2",".5","1.5","10","15","0","0"]]
    got=OKXPublicClient.parse_candles(rows,"1H")
    assert [x.timestamp for x in got]==[1,2] and not got[0].confirm and got[1].confirm


def test_backtest_insufficient_is_not_validated():
    out=run_backtest([c(i) for i in range(10)],[c(i) for i in range(10)],[])
    assert out["validationPass"] is False
