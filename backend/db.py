from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from .models import Candle, Settings, SignalAdvice


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS candles (
 instrument TEXT NOT NULL, timeframe TEXT NOT NULL, ts INTEGER NOT NULL,
 open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
 volume REAL NOT NULL, volume_ccy REAL, confirm INTEGER NOT NULL,
 PRIMARY KEY(instrument,timeframe,ts)
);
CREATE TABLE IF NOT EXISTS funding (instrument TEXT NOT NULL, ts INTEGER NOT NULL, rate REAL NOT NULL, PRIMARY KEY(instrument,ts));
CREATE TABLE IF NOT EXISTS open_interest (instrument TEXT NOT NULL, ts INTEGER NOT NULL, value REAL NOT NULL, PRIMARY KEY(instrument,ts));
CREATE TABLE IF NOT EXISTS signals (dedupe_key TEXT PRIMARY KEY, candle_ts INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS backtests (id TEXT PRIMARY KEY, status TEXT NOT NULL, progress REAL NOT NULL, message TEXT NOT NULL, payload TEXT);
CREATE TABLE IF NOT EXISTS news_items (id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, published_at INTEGER NOT NULL, observed_at INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at DESC);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as con:
            con.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=20, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def upsert_candles(self, instrument: str, candles: Iterable[Candle]) -> int:
        rows = [(instrument,c.timeframe,c.timestamp,c.open,c.high,c.low,c.close,c.volume,c.volume_ccy,int(c.confirm)) for c in candles]
        with self._lock, self.connect() as con:
            con.executemany("INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(instrument,timeframe,ts) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,volume_ccy=excluded.volume_ccy,confirm=excluded.confirm", rows)
        return len(rows)

    def candles(self, instrument: str, timeframe: str, limit: int = 500, confirmed_only: bool = False) -> list[Candle]:
        where = " AND confirm=1" if confirmed_only else ""
        with self.connect() as con:
            rows = con.execute(f"SELECT * FROM candles WHERE instrument=? AND timeframe=?{where} ORDER BY ts DESC LIMIT ?", (instrument,timeframe,limit)).fetchall()
        return [Candle(timestamp=r["ts"],open=r["open"],high=r["high"],low=r["low"],close=r["close"],volume=r["volume"],volume_ccy=r["volume_ccy"],confirm=bool(r["confirm"]),timeframe=r["timeframe"]) for r in reversed(rows)]

    def upsert_funding(self, instrument: str, rows: Iterable[tuple[int,float]]) -> None:
        with self._lock, self.connect() as con:
            con.executemany("INSERT OR REPLACE INTO funding VALUES(?,?,?)", ((instrument,*r) for r in rows))

    def latest_funding(self, instrument: str) -> tuple[int,float] | None:
        with self.connect() as con:
            r=con.execute("SELECT ts,rate FROM funding WHERE instrument=? ORDER BY ts DESC LIMIT 1",(instrument,)).fetchone()
        return (r["ts"],r["rate"]) if r else None

    def funding_rows(self, instrument: str, since: int = 0) -> list[tuple[int,float]]:
        with self.connect() as con:
            rows=con.execute("SELECT ts,rate FROM funding WHERE instrument=? AND ts>=? ORDER BY ts",(instrument,since)).fetchall()
        return [(r["ts"],r["rate"]) for r in rows]

    def upsert_oi(self, instrument: str, ts: int, value: float) -> None:
        with self._lock, self.connect() as con: con.execute("INSERT OR REPLACE INTO open_interest VALUES(?,?,?)",(instrument,ts,value))

    def latest_oi(self, instrument: str) -> tuple[int,float] | None:
        with self.connect() as con: r=con.execute("SELECT ts,value FROM open_interest WHERE instrument=? ORDER BY ts DESC LIMIT 1",(instrument,)).fetchone()
        return (r["ts"],r["value"]) if r else None

    def save_signal(self, advice: SignalAdvice) -> bool:
        key=f"{advice.instrument}:{advice.strategy}:{advice.action}:{advice.candle_close_at}"
        with self._lock, self.connect() as con:
            cur=con.execute("INSERT OR IGNORE INTO signals VALUES(?,?,?,?)",(key,advice.candle_close_at,advice.model_dump_json(by_alias=True),advice.created_at))
        return cur.rowcount > 0

    def signal_history(self, limit: int=50) -> list[dict]:
        with self.connect() as con: rows=con.execute("SELECT payload FROM signals ORDER BY candle_ts DESC LIMIT ?",(limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get_settings(self) -> Settings:
        with self.connect() as con: r=con.execute("SELECT payload FROM settings WHERE id=1").fetchone()
        return Settings.model_validate_json(r[0]) if r else Settings()

    def put_settings(self, settings: Settings) -> None:
        with self._lock, self.connect() as con: con.execute("INSERT OR REPLACE INTO settings VALUES(1,?)",(settings.model_dump_json(),))

    def save_backtest(self, id: str, status: str, progress: float, message: str, payload: dict | None=None) -> None:
        with self._lock, self.connect() as con: con.execute("INSERT OR REPLACE INTO backtests VALUES(?,?,?,?,?)",(id,status,progress,message,json.dumps(payload) if payload else None))

    def get_backtest(self,id: str) -> dict | None:
        with self.connect() as con: r=con.execute("SELECT * FROM backtests WHERE id=?",(id,)).fetchone()
        return dict(r) if r else None

    def upsert_news(self, items: Iterable[dict]) -> int:
        rows=[]
        for item in items:
            rows.append((str(item["id"]),str(item["url"]),int(item["publishedAt"]),int(item["observedAt"]),json.dumps(item,ensure_ascii=False)))
        with self._lock, self.connect() as con:
            con.executemany("INSERT INTO news_items VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET url=excluded.url,published_at=excluded.published_at,observed_at=MIN(news_items.observed_at,excluded.observed_at),payload=excluded.payload",rows)
        return len(rows)

    def news_items(self, limit:int=50, decision_at:int|None=None, since:int=0) -> list[dict]:
        cutoff=decision_at if decision_at is not None else 9_999_999_999_999
        with self.connect() as con:
            rows=con.execute("SELECT payload FROM news_items WHERE published_at<=? AND observed_at<=? AND published_at>=? ORDER BY published_at DESC LIMIT ?",(cutoff,cutoff,since,limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def clear_local_data(self) -> None:
        with self._lock, self.connect() as con:
            for table in ("candles","funding","open_interest","signals","settings","backtests","news_items"): con.execute(f"DELETE FROM {table}")
