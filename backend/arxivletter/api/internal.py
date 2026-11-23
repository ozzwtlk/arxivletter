"""Server ⇄ computing-machine plumbing (role "server", e.g. a Raspberry Pi in Docker). Adapted from AXIOM.

Workers (`arxivletter worker` on the main PC with the local model, or a laptop with Claude Code / Codex) connect OUT
to the server, so they work behind NAT/firewalls:

  /internal/db/run|begin|end   remote SQL with server-held BEGIN IMMEDIATE transactions — all data stays on the
                               server and nothing needs syncing (same protocol as AXIOM)
  /internal/heartbeat          the worker reports its name, models and job kinds every minute

Deviation from AXIOM: there is no WebSocket request forwarding. Everything a user asks for (re-digest, premium
analysis, backfill) becomes a queued job that the right machine claims, and a crashed worker's jobs come back via
expired leases. Everything under /internal requires `Authorization: Bearer $ARXIVLETTER_TOKEN`.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sqlite3
import threading
import time
import uuid

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException

from arxivletter.config import settings
from arxivletter.storage import db

log = logging.getLogger("arxivletter.server")


def role() -> str:
    return os.environ.get("ARXIVLETTER_ROLE", "all")


def _token() -> str:
    return os.environ.get("ARXIVLETTER_TOKEN", "")


def check_token(authorization: str = Header(default="")) -> None:
    tok = _token()
    if not tok or len(tok) < 16:
        raise HTTPException(503, "server has no ARXIVLETTER_TOKEN (≥ 16 chars) configured; workers are disabled")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {tok}".encode()):
        raise HTTPException(401, "bad worker token")


class _Tx:
    def __init__(self):
        self.conn = sqlite3.connect(settings.db_path, timeout=60, check_same_thread=False, isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("BEGIN IMMEDIATE")
        self.lock = threading.Lock()
        self.used = time.monotonic()


_txs: dict[str, _Tx] = {}
_txs_lock = threading.Lock()
TX_IDLE_S = 60


def _reap(idle: float = TX_IDLE_S) -> None:
    """Roll back transactions of workers that vanished (they would hold SQLite's write lock forever)."""
    now = time.monotonic()
    with _txs_lock:
        dead = [k for k, t in _txs.items() if now - t.used > idle]
        for k in dead:
            t = _txs.pop(k)
            try:
                t.conn.rollback()
                t.conn.close()
            except sqlite3.Error:
                pass
    if dead:
        log.warning("rolled back %d abandoned worker transaction(s)", len(dead))


def _result(cur: sqlite3.Cursor) -> dict:
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = [[db._enc(v) for v in r] for r in cur.fetchall()] if cols else []
    return {"cols": cols, "rows": rows, "rowcount": cur.rowcount, "lastrowid": cur.lastrowid}


def _reaper() -> None:
    while True:
        time.sleep(10)
        try:
            _reap()
        except Exception:
            log.exception("transaction reaper failed")


def install(app: FastAPI) -> None:
    if role() == "server" and not getattr(install, "_started", False):
        install._started = True
        threading.Thread(target=_reaper, daemon=True, name="tx-reaper").start()
    r = APIRouter(prefix="/internal", dependencies=[Depends(check_token)])

    @r.post("/db/run")
    def db_run(body: dict):
        sql, params, tx = body["sql"], [db._dec(p) for p in body.get("params") or []], body.get("tx")
        if tx:
            with _txs_lock:
                t = _txs.get(tx)
            if not t:
                raise HTTPException(409, "transaction expired")
            with t.lock:
                t.used = time.monotonic()
                try:
                    return _result(t.conn.execute(sql, params))
                except sqlite3.Error as e:
                    raise HTTPException(400, f"{type(e).__name__}: {e}") from None
        c = db.conn()
        try:
            out = _result(c.execute(sql, params))
            c.commit()
            return out
        except sqlite3.Error as e:
            c.rollback()
            raise HTTPException(400, f"{type(e).__name__}: {e}") from None

    @r.post("/db/begin")
    def db_begin():
        _reap()
        t = _Tx()
        tid = uuid.uuid4().hex
        with _txs_lock:
            _txs[tid] = t
        return {"tx": tid}

    @r.post("/db/end")
    def db_end(body: dict):
        with _txs_lock:
            t = _txs.pop(body["tx"], None)
        if not t:
            raise HTTPException(409, "transaction expired")
        with t.lock:
            (t.conn.commit if body.get("commit") else t.conn.rollback)()
            t.conn.close()
        return {"ok": True}

    @r.post("/heartbeat")
    def heartbeat(body: dict):
        name = str(body.get("name") or "worker")[:64]
        from arxivletter.arxiv.catalog import now
        if body.get("offline"):
            with db.connect() as c:
                c.execute("UPDATE workers SET last_seen='1970-01-01T00:00:00Z' WHERE name=?", (name,))
            return {"ok": True}
        with db.connect() as c:
            c.execute("INSERT INTO workers(name, info, models, kinds, last_seen) VALUES (?,?,?,?,?) ON CONFLICT(name) "
                      "DO UPDATE SET info=excluded.info, models=excluded.models, kinds=excluded.kinds, "
                      "last_seen=excluded.last_seen",
                      (name, json.dumps(body.get("info") or {}), json.dumps(body.get("models") or {}),
                       json.dumps(body.get("kinds") or []), now()))
        return {"ok": True, "server_time": now()}

    app.include_router(r)
