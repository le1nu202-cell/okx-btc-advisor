from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import AdviceAction, Candle, Settings, SignalAdvice


SCHEMA_VERSION = 4
OI_SAMPLE_BUCKET_MS = 5*60_000
NEWS_SOURCE_CHECK_RETENTION_MS = 7*86400_000


def _reject_json_constant(value:str):
    raise ValueError(f"non-finite JSON constant: {value}")


def _json_dumps(value) -> str:
    return json.dumps(value,ensure_ascii=False,allow_nan=False)


def _json_loads(value:str):
    return json.loads(value,parse_constant=_reject_json_constant)


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
CREATE TABLE IF NOT EXISTS backtests (
 id TEXT PRIMARY KEY, status TEXT NOT NULL, progress REAL NOT NULL, message TEXT NOT NULL,
 payload TEXT, request_payload TEXT, created_at INTEGER NOT NULL DEFAULT 0,
 updated_at INTEGER NOT NULL DEFAULT 0, started_at INTEGER, finished_at INTEGER
);
CREATE TABLE IF NOT EXISTS news_items (id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, published_at INTEGER NOT NULL, observed_at INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at DESC);
CREATE TABLE IF NOT EXISTS news_source_checks (
 source TEXT NOT NULL, observed_at INTEGER NOT NULL, ok INTEGER NOT NULL,
 item_count INTEGER NOT NULL, error TEXT,
 PRIMARY KEY(source,observed_at)
);
CREATE INDEX IF NOT EXISTS idx_news_source_checks_time ON news_source_checks(observed_at DESC);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.session() as con:
            current_version=con.execute("PRAGMA user_version").fetchone()[0]
            if current_version>SCHEMA_VERSION:
                raise RuntimeError(f"database schema {current_version} is newer than supported {SCHEMA_VERSION}")
            con.executescript(SCHEMA)
            self._migrate(con)

    def _migrate(self, con: sqlite3.Connection) -> None:
        """Apply small additive migrations without replacing user-owned local data."""
        current_version=con.execute("PRAGMA user_version").fetchone()[0]
        if current_version>SCHEMA_VERSION:
            raise RuntimeError(f"database schema {current_version} is newer than supported {SCHEMA_VERSION}")
        columns={row["name"] for row in con.execute("PRAGMA table_info(backtests)")}
        additions={
            "request_payload":"TEXT",
            "created_at":"INTEGER NOT NULL DEFAULT 0",
            "updated_at":"INTEGER NOT NULL DEFAULT 0",
            "started_at":"INTEGER",
            "finished_at":"INTEGER",
        }
        for name,declaration in additions.items():
            if name not in columns:
                con.execute(f"ALTER TABLE backtests ADD COLUMN {name} {declaration}")
        now=int(time.time()*1000)
        con.execute("UPDATE backtests SET created_at=? WHERE created_at=0",(now,))
        con.execute("UPDATE backtests SET updated_at=created_at WHERE updated_at=0")
        con.execute("CREATE INDEX IF NOT EXISTS idx_backtests_updated ON backtests(updated_at DESC)")
        if current_version<3:
            # Public OI can update every few seconds. One latest observation per
            # five-minute bucket is enough for the 24H confirmation label and
            # prevents unbounded multi-million-row growth.
            con.execute(
                "DELETE FROM open_interest WHERE (instrument,ts) NOT IN "
                "(SELECT instrument,MAX(ts) FROM open_interest GROUP BY instrument,ts/?)",
                (OI_SAMPLE_BUCKET_MS,),
            )
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

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
            con.executemany(
                "INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(instrument,timeframe,ts) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "volume=excluded.volume,volume_ccy=excluded.volume_ccy,confirm=excluded.confirm "
                "WHERE excluded.confirm>candles.confirm OR ("
                "excluded.confirm=candles.confirm "
                "AND NOT(candles.volume_ccy IS NOT NULL AND excluded.volume_ccy IS NULL) "
                "AND excluded.volume>=candles.volume "
                "AND excluded.high>=candles.high AND excluded.low<=candles.low "
                "AND (candles.volume_ccy IS NULL OR excluded.volume_ccy>=candles.volume_ccy))",
                rows,
            )
        return len(rows)

    def candles(self, instrument: str, timeframe: str, limit: int = 500, confirmed_only: bool = False) -> list[Candle]:
        where = " AND confirm=1" if confirmed_only else ""
        with self.session() as con:
            rows = con.execute(f"SELECT * FROM candles WHERE instrument=? AND timeframe=?{where} ORDER BY ts DESC LIMIT ?", (instrument,timeframe,limit)).fetchall()
        return [Candle(timestamp=r["ts"],open=r["open"],high=r["high"],low=r["low"],close=r["close"],volume=r["volume"],volume_ccy=r["volume_ccy"],confirm=bool(r["confirm"]),timeframe=r["timeframe"]) for r in reversed(rows)]

    def candles_since(self, instrument: str, timeframe: str, since: int = 0, confirmed_only: bool = False) -> list[Candle]:
        """Load the complete persisted interval without an arbitrary row limit."""
        where = " AND confirm=1" if confirmed_only else ""
        with self.session() as con:
            rows = con.execute(
                f"SELECT * FROM candles WHERE instrument=? AND timeframe=? AND ts>=?{where} ORDER BY ts",
                (instrument,timeframe,int(since)),
            ).fetchall()
        return [Candle(timestamp=r["ts"],open=r["open"],high=r["high"],low=r["low"],close=r["close"],volume=r["volume"],volume_ccy=r["volume_ccy"],confirm=bool(r["confirm"]),timeframe=r["timeframe"]) for r in rows]

    def upsert_funding(self, instrument: str, rows: Iterable[tuple[int,float]]) -> None:
        valid=[]
        for ts,rate in rows:
            try:ts,rate=int(ts),float(rate)
            except (TypeError,ValueError,OverflowError):continue
            if ts>0 and math.isfinite(rate) and abs(rate)<=1:valid.append((instrument,ts,rate))
        with self._lock, self.session() as con:
            con.executemany("INSERT OR REPLACE INTO funding VALUES(?,?,?)",valid)

    def latest_funding(self, instrument: str) -> tuple[int,float] | None:
        with self.session() as con:
            r=con.execute("SELECT ts,rate FROM funding WHERE instrument=? ORDER BY ts DESC LIMIT 1",(instrument,)).fetchone()
        return (r["ts"],r["rate"]) if r else None

    def funding_rows(self, instrument: str, since: int = 0) -> list[tuple[int,float]]:
        with self.session() as con:
            rows=con.execute("SELECT ts,rate FROM funding WHERE instrument=? AND ts>=? ORDER BY ts",(instrument,since)).fetchall()
        return [(r["ts"],r["rate"]) for r in rows]

    def upsert_oi(self, instrument: str, ts: int, value: float) -> None:
        try:ts,value=int(ts),float(value)
        except (TypeError,ValueError,OverflowError):return
        if ts<=0 or not math.isfinite(value) or value<0:return
        with self._lock, self.session() as con:
            latest=con.execute("SELECT ts FROM open_interest WHERE instrument=? ORDER BY ts DESC LIMIT 1",(instrument,)).fetchone()
            if latest and ts<latest["ts"]:return
            bucket_start=ts//OI_SAMPLE_BUCKET_MS*OI_SAMPLE_BUCKET_MS
            con.execute("DELETE FROM open_interest WHERE instrument=? AND ts>=? AND ts<?",(instrument,bucket_start,bucket_start+OI_SAMPLE_BUCKET_MS))
            con.execute("INSERT INTO open_interest VALUES(?,?,?)",(instrument,ts,value))

    def latest_oi(self, instrument: str) -> tuple[int,float] | None:
        with self.session() as con: r=con.execute("SELECT ts,value FROM open_interest WHERE instrument=? ORDER BY ts DESC LIMIT 1",(instrument,)).fetchone()
        return (r["ts"],r["value"]) if r else None

    def open_interest_context(
        self,
        instrument: str,
        lookback_ms: int = 24*3600_000,
        as_of_ms: int | None = None,
        max_sample_age_ms: int = 30*60_000,
    ) -> dict | None:
        """Return an as-of-safe 24H comparison from sufficiently close samples.

        OI is sampled locally rather than backfilled.  A very old row must not
        silently become a "24H" baseline after an outage, and a row observed
        after the candle decision must never leak into that decision.
        """
        try:
            lookback_ms=int(lookback_ms);max_sample_age_ms=int(max_sample_age_ms)
            cutoff=int(as_of_ms) if as_of_ms is not None else None
        except (TypeError,ValueError,OverflowError):
            return None
        if lookback_ms<=0 or max_sample_age_ms<0 or (cutoff is not None and cutoff<=0):return None
        with self.session() as con:
            if cutoff is None:
                latest=con.execute(
                    "SELECT ts,value FROM open_interest WHERE instrument=? ORDER BY ts DESC LIMIT 1",
                    (instrument,),
                ).fetchone()
            else:
                latest=con.execute(
                    "SELECT ts,value FROM open_interest WHERE instrument=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                    (instrument,cutoff),
                ).fetchone()
            if not latest:return None
            if cutoff is not None and cutoff-int(latest["ts"])>max_sample_age_ms:return None
            target=int(latest["ts"])-lookback_ms
            baseline=con.execute(
                "SELECT ts,value FROM open_interest WHERE instrument=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                (instrument,target),
            ).fetchone()
        if not baseline or target-int(baseline["ts"])>max_sample_age_ms or baseline["value"]<=0:return None
        change=float(latest["value"])/float(baseline["value"])-1
        if not math.isfinite(change):return None
        return {
            "latestTime":latest["ts"],"latest":latest["value"],
            "baselineTime":baseline["ts"],"baseline":baseline["value"],
            "changePercent":change,
        }

    def save_signal(self, advice: SignalAdvice) -> bool:
        direction=("LONG" if advice.action in (AdviceAction.LONG_CANDIDATE,AdviceAction.WATCH_LONG)
                   else "SHORT" if advice.action in (AdviceAction.SHORT_CANDIDATE,AdviceAction.WATCH_SHORT)
                   else advice.action.value)
        key=f"{advice.instrument}:{advice.strategy}:{direction}:{advice.candle_close_at}"
        with self._lock, self.session() as con:
            cur=con.execute("INSERT OR IGNORE INTO signals VALUES(?,?,?,?)",(key,advice.candle_close_at,advice.model_dump_json(by_alias=True),advice.created_at))
        return cur.rowcount > 0

    def signal_history(self, limit: int=50) -> list[dict]:
        with self.session() as con: rows=con.execute("SELECT payload FROM signals ORDER BY candle_ts DESC LIMIT ?",(limit,)).fetchall()
        result=[]
        for row in rows:
            try:result.append(_json_loads(row[0]))
            except (TypeError,ValueError,json.JSONDecodeError):continue
        return result

    def get_settings(self) -> Settings:
        with self.session() as con: r=con.execute("SELECT payload FROM settings WHERE id=1").fetchone()
        return Settings.model_validate_json(r[0]) if r else Settings()

    def put_settings(self, settings: Settings) -> None:
        with self._lock, self.session() as con: con.execute("INSERT OR REPLACE INTO settings VALUES(1,?)",(settings.model_dump_json(),))

    def create_backtest(self,id:str,request:dict|None=None,message:str="等待执行") -> None:
        now=int(time.time()*1000)
        with self._lock,self.session() as con:
            con.execute(
                "INSERT INTO backtests(id,status,progress,message,payload,request_payload,created_at,updated_at,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (id,"queued",0,message,None,_json_dumps(request) if request is not None else None,now,now,None,None),
            )

    def save_backtest(self, id: str, status: str, progress: float, message: str, payload: dict | None=None) -> None:
        now=int(time.time()*1000)
        started=now if status=="running" else None
        finished=now if status in {"complete","failed","interrupted"} else None
        encoded=_json_dumps(payload) if payload is not None else None
        with self._lock,self.session() as con:
            con.execute(
                "INSERT INTO backtests(id,status,progress,message,payload,request_payload,created_at,updated_at,started_at,finished_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status,progress=excluded.progress,message=excluded.message,"
                "payload=COALESCE(excluded.payload,backtests.payload),updated_at=excluded.updated_at,"
                "started_at=COALESCE(backtests.started_at,excluded.started_at),finished_at=COALESCE(excluded.finished_at,backtests.finished_at)",
                (id,status,progress,message,encoded,None,now,now,started,finished),
            )

    def get_backtest(self,id: str) -> dict | None:
        with self.session() as con: r=con.execute("SELECT * FROM backtests WHERE id=?",(id,)).fetchone()
        return dict(r) if r else None

    def backtest_history(self,limit:int=20,status:str|None=None) -> list[dict]:
        where=" WHERE status=?" if status else ""
        params=(status,limit) if status else (limit,)
        with self.session() as con:
            rows=con.execute(f"SELECT * FROM backtests{where} ORDER BY updated_at DESC,id DESC LIMIT ?",params).fetchall()
        return [dict(row) for row in rows]

    def interrupt_incomplete_backtests(self,message:str="服务重启，先前回测已中断") -> int:
        now=int(time.time()*1000)
        with self._lock,self.session() as con:
            cursor=con.execute(
                "UPDATE backtests SET status='interrupted',message=?,updated_at=?,finished_at=? WHERE status IN ('queued','running')",
                (message,now,now),
            )
        return cursor.rowcount

    def delete_backtest(self,id:str) -> bool:
        with self._lock,self.session() as con:
            cursor=con.execute("DELETE FROM backtests WHERE id=? AND status NOT IN ('queued','running')",(id,))
        return cursor.rowcount>0

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
                    # Keep the complete first-seen version immutable. Updating a title,
                    # publication timestamp or relevance while retaining the original
                    # observed_at would make a later publisher correction visible to an
                    # earlier decision and therefore introduce point-in-time leakage.
                    # Supporting corrections safely requires a versioned article table;
                    # until then, first observation is the conservative source of truth.
                    continue
                item["id"],item["publishedAt"],item["observedAt"]=item_id,published,observed
                con.execute(
                    "INSERT INTO news_items VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "url=excluded.url,published_at=excluded.published_at,observed_at=excluded.observed_at,payload=excluded.payload",
                    (item_id,url,published,observed,_json_dumps(item)),
                )
        return len(rows)

    def news_items(self, limit:int=50, decision_at:int|None=None, since:int=0) -> list[dict]:
        cutoff=decision_at if decision_at is not None else 9_999_999_999_999
        with self.session() as con:
            rows=con.execute("SELECT id,published_at,observed_at,payload FROM news_items WHERE published_at<=? AND observed_at<=? AND published_at>=? ORDER BY published_at DESC LIMIT ?",(cutoff,cutoff,since,limit)).fetchall()
        result=[]
        for row in rows:
            try:payload=_json_loads(row["payload"])
            except (TypeError,ValueError,json.JSONDecodeError):continue
            # Columns are authoritative so old databases affected by the previous payload
            # overwrite bug immediately regain point-in-time correctness.
            payload["id"],payload["publishedAt"],payload["observedAt"]=row["id"],row["published_at"],row["observed_at"]
            result.append(payload)
        return result

    def save_news_source_status(self,statuses:Iterable[dict]) -> int:
        rows=[]
        for raw in statuses:
            if not isinstance(raw,dict):continue
            source=str(raw.get("source") or "").strip().lower()[:50]
            try:
                value=raw.get("observedAt")
                observed=int(value if float(value)>=1_000_000_000_000 else float(value)*1000) if isinstance(value,(int,float)) else int(datetime.fromisoformat(str(value).replace("Z","+00:00")).timestamp()*1000)
                item_count=max(0,int(raw.get("itemCount",0) or 0))
            except (TypeError,ValueError,OverflowError):continue
            if not source or observed<=0:continue
            error=str(raw.get("error"))[:200] if raw.get("error") else None
            rows.append((source,observed,int(bool(raw.get("ok"))),item_count,error))
        if not rows:return 0
        newest=max(row[1] for row in rows)
        with self._lock,self.session() as con:
            con.executemany(
                "INSERT INTO news_source_checks VALUES(?,?,?,?,?) ON CONFLICT(source,observed_at) DO UPDATE SET "
                "ok=excluded.ok,item_count=excluded.item_count,error=excluded.error",
                rows,
            )
            con.execute("DELETE FROM news_source_checks WHERE observed_at<?",(newest-NEWS_SOURCE_CHECK_RETENTION_MS,))
        return len(rows)

    def news_source_status(self,decision_at:int,max_age_ms:int) -> list[dict]:
        with self.session() as con:
            rows=con.execute(
                "SELECT checks.* FROM news_source_checks checks JOIN ("
                "SELECT source,MAX(observed_at) observed_at FROM news_source_checks WHERE observed_at<=? GROUP BY source"
                ") latest ON latest.source=checks.source AND latest.observed_at=checks.observed_at ORDER BY checks.source",
                (decision_at,),
            ).fetchall()
        result=[]
        for row in rows:
            stale=decision_at-row["observed_at"]>max_age_ms
            result.append({"source":row["source"],"ok":bool(row["ok"]) and not stale,"itemCount":row["item_count"],"observedAt":row["observed_at"],"error":f"来源状态在决策时点已过期 {max(0,(decision_at-row['observed_at'])//60_000)} 分钟" if stale else row["error"]})
        return result

    def clear_local_data(self) -> None:
        with self._lock, self.session() as con:
            for table in ("candles","funding","open_interest","signals","settings","backtests","news_items","news_source_checks"): con.execute(f"DELETE FROM {table}")
