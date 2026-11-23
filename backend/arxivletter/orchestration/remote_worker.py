"""`arxivletter worker`: make this PC/laptop a computing machine for an arXivletter server.

Uses the server's database over HTTP (storage/db.py remote mode), runs the model jobs it can (local Qwen on the main
PC; Claude Code / Codex where signed in), and reports its models every minute (/internal/heartbeat). It keeps no
state of its own except the model cache directory; stopping it is always safe — running jobs return to the queue
when their lease expires (or immediately on a clean shutdown).
"""

from __future__ import annotations

import logging
import os
import platform
import signal
import socket
import threading
import time

log = logging.getLogger("arxivletter.worker")
HEARTBEAT_S = 60


def run(server: str, token: str, name: str | None = None) -> None:
    import httpx

    from arxivletter.orchestration import jobs, models
    from arxivletter.storage import db

    os.environ["ARXIVLETTER_ROLE"] = "worker"
    name = name or os.environ.get("ARXIVLETTER_WORKER_NAME") or socket.gethostname()
    if not token or len(token) < 16:
        raise SystemExit("ARXIVLETTER_TOKEN must be set (≥ 16 characters, the same as on the server)")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db.init_remote(server, token)
    db.q1("SELECT 1")  # fail fast on a wrong URL or token
    jobs.WORKER_NAME = name
    stop = threading.Event()
    kind_sets = jobs.kind_sets_for("worker")
    info = {"hostname": socket.gethostname(), "platform": platform.platform(terse=True)}
    hb = httpx.Client(timeout=30, headers={"Authorization": f"Bearer {token}"})

    def beat():
        while not stop.is_set():
            try:
                models._avail_cache.clear()
                rep = models.report()
                hb.post(server.rstrip("/") + "/internal/heartbeat",
                        json={"name": name, "info": info, "models": rep,
                              "kinds": sorted({k for ks in kind_sets for k in ks})}).raise_for_status()
            except Exception as e:
                log.warning("heartbeat failed: %s", e)
            stop.wait(HEARTBEAT_S)

    threading.Thread(target=beat, daemon=True, name="heartbeat").start()
    jobs.start(kind_sets)
    ok = [m["id"] for m in models.report()["models"] if m["available"]]
    log.info("worker %r connected to %s — models: %s — job kinds: %s", name, server, ", ".join(ok) or "none",
             kind_sets)

    def shutdown(*_):
        stop.set()
        jobs.stop()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while not stop.is_set():
        time.sleep(1)
    time.sleep(2)  # let running jobs hit their next checkpoint and release themselves
    try:  # a clean shutdown hands back what is still running (in-flight model answers are discarded) …
        jobs.recover_own()
        # … and tells the server right away instead of letting the heartbeat age out
        hb.post(server.rstrip("/") + "/internal/heartbeat", json={"name": name, "info": info, "offline": True})
    except Exception as e:
        log.warning("could not release jobs on shutdown: %s", e)
