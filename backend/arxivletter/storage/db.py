"""SQLite persistence (adapted from AXIOM). One connection per thread; WAL mode so the reader stays fast while
workers write. In worker mode every statement runs on the server's database over authenticated HTTP
(api/internal.py), and `with connect()` is a real server-side transaction (BEGIN IMMEDIATE)."""

from __future__ import annotations

import base64
import gzip
import json
from collections.abc import Mapping
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS bookmarks (
  key TEXT PRIMARY KEY, paper_id TEXT NOT NULL, kind TEXT NOT NULL,
  paper_title TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- one row per arXiv paper (id without version: 2611.12345 or hep-ph/0601001)
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '[]', abstract TEXT NOT NULL DEFAULT '',
  categories TEXT NOT NULL DEFAULT '[]', primary_category TEXT, comments TEXT, journal_ref TEXT, doi TEXT,
  license TEXT, report_no TEXT,
  versions TEXT NOT NULL DEFAULT '[]',          -- [{v, date (ISO, UTC), size, source_type}]
  latest_version INTEGER NOT NULL DEFAULT 1,
  submitted_v1 TEXT, submitted_date TEXT,      -- v1 submission (ISO) and its UTC date (calendar day)
  latest_submitted TEXT, oai_datestamp TEXT, source_meta TEXT NOT NULL DEFAULT 'oai',
  first_seen TEXT NOT NULL, updated_at TEXT NOT NULL,
  relevance REAL, relevance_detail TEXT NOT NULL DEFAULT '{}', topics TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'new',          -- new | fetched | triaged | digested | failed
  fetched_version INTEGER, digested_version INTEGER, digest_depth TEXT, gaps_version INTEGER,
  starred INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0, read_at TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS papers_day ON papers(submitted_date);
CREATE INDEX IF NOT EXISTS papers_rel ON papers(relevance);

-- calendar: one row per (paper, version) on the UTC day that version was submitted
CREATE TABLE IF NOT EXISTS paper_events (
  paper_id TEXT NOT NULL, version INTEGER NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL,  -- new | revision
  submitted TEXT NOT NULL, PRIMARY KEY (paper_id, version)
);
CREATE INDEX IF NOT EXISTS events_day ON paper_events(day);

-- parsed full text of one version (gzip JSON, see parsing/document.py) + provenance
CREATE TABLE IF NOT EXISTS content (
  paper_id TEXT NOT NULL, version INTEGER NOT NULL, source_kind TEXT NOT NULL,   -- latex | html | pdf | abstract
  data BLOB NOT NULL, chars INTEGER NOT NULL, provenance TEXT NOT NULL DEFAULT '{}', fetched_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, version)
);

-- model outputs per paper version: triage | extract | digest | gaps | changes
CREATE TABLE IF NOT EXISTS analyses (
  paper_id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
  model TEXT, model_class TEXT, prompt_version TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, version, kind)
);

CREATE TABLE IF NOT EXISTS briefs (
  day TEXT PRIMARY KEY, data TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harvests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, set_spec TEXT NOT NULL, from_day TEXT NOT NULL, until_day TEXT NOT NULL,
  status TEXT NOT NULL, records INTEGER NOT NULL DEFAULT 0, new_papers INTEGER NOT NULL DEFAULT 0,
  new_versions INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, finished_at TEXT, error TEXT
);

CREATE TABLE IF NOT EXISTS llm_cache (
  key TEXT PRIMARY KEY, task TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  response TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, paper_id TEXT, task TEXT NOT NULL, model TEXT NOT NULL,
  model_class TEXT NOT NULL, cached INTEGER NOT NULL, ok INTEGER NOT NULL, seconds REAL NOT NULL,
  prompt_tokens INTEGER, completion_tokens INTEGER, ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS llm_calls_ts ON llm_calls(ts);

-- persistent queue; a claimed job holds a lease that the runner renews, an expired lease is requeued
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL,
  priority REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 4,
  not_before TEXT, lease_until TEXT, worker TEXT, run_token TEXT, model TEXT, reason TEXT,
  error TEXT, result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, kind);
CREATE INDEX IF NOT EXISTS jobs_target ON jobs(kind, target);

CREATE TABLE IF NOT EXISTS workers (
  name TEXT PRIMARY KEY, info TEXT, models TEXT, kinds TEXT, last_seen TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5(
  paper_id UNINDEXED, title, authors, abstract, digest, tokenize='porter unicode61'
);
"""

_local = threading.local()
_db_path: Path | None = None


def init(path: Path) -> None:
    global _db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _db_path = path
    with connect() as c:
        c.executescript(SCHEMA)
        _migrate(c)
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


def _migrate(c: sqlite3.Connection) -> None:  # columns added after schema v1 go here (see AXIOM's db._COLUMNS)
    pass


# ---------------------------------------------------------------- remote mode (worker → server)
# On a worker machine the database lives on the server. The same q()/q1()/connect() API then runs each statement
# on the server's SQLite over authenticated HTTP (see api/internal.py); a `with connect()` block is a real
# server-side transaction (BEGIN IMMEDIATE), so job claiming stays atomic across machines.
_remote = None


class RemoteRow(Mapping):
    """Behaves like sqlite3.Row: r["col"], r[0], keys(), dict(r)."""
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols: list[str], vals: list[Any]):
        self._cols, self._vals = cols, vals

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._vals[k]
        try:
            return self._vals[self._cols.index(k)]
        except ValueError:
            raise KeyError(k) from None

    def __iter__(self):
        return iter(self._cols)

    def __len__(self):
        return len(self._cols)

    def keys(self):
        return list(self._cols)


def _enc(v: Any) -> Any:
    return {"$b64": base64.b64encode(v).decode()} if isinstance(v, (bytes, bytearray, memoryview)) else v


def _dec(v: Any) -> Any:
    return base64.b64decode(v["$b64"]) if isinstance(v, dict) and "$b64" in v else v


class RemoteCursor:
    def __init__(self, d: dict):
        cols = d.get("cols") or []
        self._rows = [RemoteRow(cols, [_dec(x) for x in r]) for r in d.get("rows") or []]
        self.rowcount = d.get("rowcount", -1)
        self.lastrowid = d.get("lastrowid")

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class RemoteConn:
    """Connection-like object for one server-side transaction (or autocommit when tx is None)."""

    def __init__(self, client, tx: str | None):
        self._client, self._tx = client, tx

    def execute(self, sql: str, args: tuple | list = ()) -> RemoteCursor:
        return RemoteCursor(self._client.run(self._tx, sql, [_enc(a) for a in args]))

    def commit(self) -> None:  # autocommit object: every statement is already committed
        pass


class RemoteDB:
    def __init__(self, base_url: str, token: str, client=None):
        import httpx
        self.base = base_url.rstrip("/")
        self._http = client or httpx.Client(timeout=120, headers={"Authorization": f"Bearer {token}"})
        self._tl = threading.local()

    def _post(self, path: str, body: dict) -> dict:
        r = self._http.post(self.base + path, json=body)
        if r.status_code >= 400:
            raise sqlite3.OperationalError(f"remote db {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def run(self, tx: str | None, sql: str, args: list) -> dict:
        return self._post("/internal/db/run", {"tx": tx, "sql": sql, "params": args})

    @contextmanager
    def transaction(self) -> Iterator[RemoteConn]:
        outer = getattr(self._tl, "tx", None)
        if outer:  # nested `with connect()` joins the outer transaction, like a single sqlite connection would
            yield RemoteConn(self, outer)
            return
        tx = self._post("/internal/db/begin", {})["tx"]
        self._tl.tx = tx
        try:
            yield RemoteConn(self, tx)
            self._post("/internal/db/end", {"tx": tx, "commit": True})
        except BaseException:
            try:
                self._post("/internal/db/end", {"tx": tx, "commit": False})
            finally:
                pass
            raise
        finally:
            self._tl.tx = None


def init_remote(base_url: str, token: str, client=None) -> None:
    """Worker mode: every database call goes to the server; the schema is the server's business."""
    global _remote
    _remote = RemoteDB(base_url, token, client)


def is_remote() -> bool:
    return _remote is not None


def conn() -> sqlite3.Connection:
    if _remote is not None:
        tx = getattr(_remote._tl, "tx", None)
        return RemoteConn(_remote, tx)  # type: ignore[return-value]
    if _db_path is None:
        raise RuntimeError("storage.db.init() was not called")
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != _db_path:
        c = sqlite3.connect(_db_path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn, _local.path = c, _db_path
    return c


@contextmanager
def connect(*, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    if _remote is not None:
        with _remote.transaction() as rc:
            yield rc  # type: ignore[misc]
        return
    c = conn()
    try:
        if immediate and not c.in_transaction:
            c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


def q(sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, args).fetchall()  # remote mode: RemoteRow objects with the same interface


def q1(sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
    return conn().execute(sql, args).fetchone()


def jdump(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def gz(o: Any) -> bytes:
    return gzip.compress(jdump(o).encode(), 6)


def ungz(b: bytes) -> Any:
    return json.loads(gzip.decompress(b).decode())
