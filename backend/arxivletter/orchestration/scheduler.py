"""In-process scheduler (roles `all` and `server`): periodic harvests and the self-healing sweep.

Every HARVEST_EVERY_MIN minutes: harvest the last HARVEST_LOOKBACK_DAYS days of OAI datestamps for every category
(idempotent — already-known versions are no-ops, so overlapping windows are cheap and catch late OAI updates).
Every 10 minutes: pipeline.sweep() (expired leases, missing next steps, briefs for finished days).
State lives in the database (meta 'scheduler.last_harvest'), so restarts do not cause extra harvests.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from arxivletter.config import settings
from arxivletter.storage import db

log = logging.getLogger("arxivletter.scheduler")
_stop = threading.Event()
SWEEP_EVERY_S = 600


def _meta(key: str) -> str | None:
    r = db.q1("SELECT value FROM meta WHERE key=?", (key,))
    return r["value"] if r else None


def _set(key: str, value: str) -> None:
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value))


def harvest_due(now: datetime) -> bool:
    last = _meta("scheduler.last_harvest")
    if not last:
        return True
    return now - datetime.fromisoformat(last) >= timedelta(minutes=settings.harvest_every_min)


def tick(now: datetime | None = None) -> dict:
    from arxivletter.orchestration import pipeline
    now = now or datetime.now(timezone.utc)
    out: dict = {}
    if harvest_due(now):
        frm = (now - timedelta(days=settings.harvest_lookback_days)).strftime("%Y-%m-%d")
        out["harvest_jobs"] = pipeline.harvest_window(frm, now.strftime("%Y-%m-%d"))
        _set("scheduler.last_harvest", now.isoformat())
    last_sweep = _meta("scheduler.last_sweep")
    if not last_sweep or (now - datetime.fromisoformat(last_sweep)).total_seconds() >= SWEEP_EVERY_S:
        out["sweep"] = pipeline.sweep()
        _set("scheduler.last_sweep", now.isoformat())
    return out


def _loop() -> None:
    while not _stop.is_set():
        try:
            r = tick()
            if r:
                log.info("scheduler: %s", r)
        except Exception:
            log.exception("scheduler tick failed")
        _stop.wait(60)


def start() -> threading.Thread:
    _stop.clear()
    t = threading.Thread(target=_loop, daemon=True, name="scheduler")
    t.start()
    return t


def stop() -> None:
    _stop.set()
