from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
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
        with self.session() as con:
            con.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=20, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    @contextmanager
    def session(self):
        """Return a transactional connection that is always closed explicitly."""
        con = self.connect()
        try:
            with con:
                yield con
        finally:
            con.close()

    def upsert_candles(self, instrument: str, candles: Iterable[Candle]) -> int:
        rows = [(instrument,c.timeframe,c.timestamp,c.open,c.high,c.low,c.close,c.volume,c.volume_ccy,int(c.confirm)) for c in candles]
        with self._lock, self.session() as con:
            con.executemany("INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(instrument,timeframe,ts) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,volume_ccy=excluded.volume_ccy,confirm=excluded.confirm", rows)
        return len(rows)

    def candles(self, instrument: str, timeframe: str, limit: int = 500, confirmed_only: bool = False) -> list[Candle]:
        where = " AND confirm=1" if confirmed_only else ""
        with self.session() as con:
            rows = con.execute(f"SELECT * FROM candles WHERE instrument=? AND timeframe=?{where} ORDER BY ts DESC LIMIT ?", (instrument,timeframe,limit)).fetchall()
        return [Candle(timestamp=r["ts"],open=r["open"],high=r["high"],low=r["low"],close=r["close"],volume=r["volume"],volume_ccy=r["volume_ccy"],confirm=bool(r["confirm"]),timeframe=r["timeframe"]) for r in reversed(rows)]

    def upsert_funding(self, instrument: str, rows: Iterable[tuple[int,float]]) -> None:
        with self._lock, self.session() as con:
            con.executemany("INSERT OR REPLACE INTO funding VALUES(?,?,?)", ((instrument,*r) for r in rows))

    def latest_funding(self, instrument: str) -> tuple[int,float] | None:
        with self.session() as con:
            r=con.execute("SELECT ts,rate FROM funding WHERE instrument=? ORDER BY ts DESC LIMIT 1",(instrument,)).fetchone()
        return (r["ts"],r["rate"]) if r else None

    def funding_rows(self, instrument: str, since: int = 0) -> list[tuple[int,float]]:
        with self.session() as con:
            rows=con.execute("SELECT ts,rate FROM funding WHERE instrument=? AND ts>=? ORDER BY ts",(instrument,since)).fetchall()
        return [(r["ts"],r["rate"]) for r in rows]

    def upsert_oi(self, instrument: str, ts: int, value: float) -> None:
        with self._lock, self.session() as con: con.execute("INSERT OR REPLACE INTO open_interest VALUES(?,?,?)",(instrument,ts,value))

    def latest_oi(self, instrument: str) -> tuple[int,float] | None:
        with self.session() as con: r=con.execute("SELECT ts,value FROM open_interest WHERE instrument=? ORDER BY ts DESC LIMIT 1",(instrument,)).fetchone()
        return (r["ts"],r["value"]) if r else None

    def save_signal(self, advice: SignalAdvice) -> bool:
        key=f"{advice.instrument}:{advice.strategy}:{advice.action}:{advice.candle_close_at}"
        with self._lock, self.session() as con:
            cur=con.execute("INSERT OR IGNORE INTO signals VALUES(?,?,?,?)",(key,advice.candle_close_at,advice.model_dump_json(by_alias=True),advice.created_at))
        return cur.rowcount > 0

    def signal_history(self, limit: int=50) -> list[dict]:
        with self.session() as con: rows=con.execute("SELECT payload FROM signals ORDER BY candle_ts DESC LIMIT ?",(limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get_settings(self) -> Settings:
        with self.session() as con: r=con.execute("SELECT payload FROM settings WHERE id=1").fetchone()
        return Settings.model_validate_json(r[0]) if r else Settings()

    def put_settings(self, settings: Settings) -> None:
        with self._lock, self.session() as con: con.execute("INSERT OR REPLACE INTO settings VALUES(1,?)",(settings.model_dump_json(),))

    def save_backtest(self, id: str, status: str, progress: float, message: str, payload: dict | None=None) -> None:
        with self._lock, self.session() as con: con.execute("INSERT OR REPLACE INTO backtests VALUES(?,?,?,?,?)",(id,status,progress,message,json.dumps(payload) if payload else None))

    def get_backtest(self,id: str) -> dict | None:
        with self.session() as con: r=con.execute("SELECT * FROM backtests WHERE id=?",(id,)).fetchone()
        return dict(r) if r else None

    def upsert_news(self, items: Iterable[dict]) -> int:
        def milliseconds(value) -> int:
            if isinstance(value,(int,float)):
                return int(value)
            return int(datetime.fromisoformat(str(value).replace("Z","+00:00")).timestamp()*1000)
        rows=list(items)
        with self._lock, self.session() as con:
            for raw in rows:
                item=dict(raw)
                item_id,url=str(item["id"]),str(item["url"])
                published,observed=milliseconds(item["publishedAt"]),milliseconds(item["observedAt"])
                # A publisher can correct its timestamp, and a news cluster can gain another
                # source. Treat the canonical URL as the stable identity as well as the id.
                existing=con.execute(
                    "SELECT id,published_at,observed_at,payload FROM news_items WHERE id=? OR url=? LIMIT 1",
                    (item_id,url),
                ).fetchone()
                if existing:
                    item_id=existing["id"]
                    published=min(published,existing["published_at"])
                    observed=min(observed,existing["observed_at"])
                item["id"],item["publishedAt"],item["observedAt"]=item_id,published,observed
                con.execute(
                    "INSERT INTO news_items VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "url=excluded.url,published_at=excluded.published_at,observed_at=excluded.observed_at,payload=excluded.payload",
                    (item_id,url,published,observed,json.dumps(item,ensure_ascii=False)),
                )
        return len(rows)

    def news_items(self, limit:int=50, decision_at:int|None=None, since:int=0) -> list[dict]:
        cutoff=decision_at if decision_at is not None else 9_999_999_999_999
        with self.session() as con:
            rows=con.execute("SELECT id,published_at,observed_at,payload FROM news_items WHERE published_at<=? AND observed_at<=? AND published_at>=? ORDER BY published_at DESC LIMIT ?",(cutoff,cutoff,since,limit)).fetchall()
        result=[]
        for row in rows:
            payload=json.loads(row["payload"])
            # Columns are authoritative so old databases affected by the previous payload
            # overwrite bug immediately regain point-in-time correctness.
            payload["id"],payload["publishedAt"],payload["observedAt"]=row["id"],row["published_at"],row["observed_at"]
            result.append(payload)
        return result

    def clear_local_data(self) -> None:
        with self._lock, self.session() as con:
            for table in ("candles","funding","open_interest","signals","settings","backtests","news_items"): con.execute(f"DELETE FROM {table}")
