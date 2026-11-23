"""Persistent job queue in SQLite (shared by the server and its computing machines).

Job kinds and who runs them
  harvest  OAI-PMH window for one category set            network only  → server (or all-in-one)
  fetch    download + parse one paper version              network + CPU → server if FETCH_ON_SERVER, else workers
  triage   relevance/topics/one-liner (title + abstract)    model (local)
  digest   full or light digest of one paper version        model (local)
  gaps     AI-inferred research gaps / ideas                model (premium, local fallback)
  brief    daily synthesis                                  model (premium, local fallback)

Robustness
  * idempotent enqueue: at most one active (pending/running) job per (kind, target);
  * claim = one BEGIN IMMEDIATE transaction (also across machines: workers run it on the server's database);
  * a running job holds a lease (`lease_until`) renewed by the runner at every model call; an expired lease (machine
    crashed, network gone) returns the job to the queue — no WebSocket bookkeeping needed;
  * failures retry with exponential backoff (`not_before`), up to max_attempts; arXiv 4xx errors fail fast;
  * a machine only claims jobs whose model it can reach (a laptop without Qwen never takes a digest job).
Priorities: harvest ≫ fetch > triage > gaps > brief > digest (ordered by relevance); user requests jump ahead.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from arxivletter.config import settings
from arxivletter.storage import db

log = logging.getLogger("arxivletter.jobs")

KIND_GROUP = {"triage": "triage", "digest": "digest", "gaps": "gaps", "brief": "brief"}
NET_KINDS = ("harvest", "fetch")
MODEL_KINDS = ("triage", "digest", "gaps", "brief")
BASE_PRIORITY = {"harvest": 1000, "fetch": 500, "triage": 400, "gaps": 300, "brief": 250, "digest": 100}

# Older servers promote manual requests by priority without updating their reason.
BELOW_DIGEST_THRESHOLD = (
    "kind='digest' AND reason='auto' AND priority<1100 AND EXISTS "
    "(SELECT 1 FROM papers WHERE papers.id=substr(jobs.target,1,instr(jobs.target,'v')-1) "
    "AND COALESCE(papers.relevance,0)<?)"
)

WORKER_NAME: str | None = None  # this machine's name in worker mode
_stop = threading.Event()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ts(dt: datetime | None = None) -> str:
    return (dt or _now()).strftime("%Y-%m-%dT%H:%M:%SZ")


class Interrupted(Exception):
    pass


def enqueue(kind: str, target: str, *, priority: float = 0, model: str | None = None, reason: str = "auto",
            force: bool = False, max_attempts: int = 4) -> str | None:
    """Returns the job id (existing active job or new). With force=False an already *done* job for the same target
    blocks re-enqueueing only for harvest windows (they are cheap to repeat otherwise)."""
    with db.connect(immediate=True) as c:
        row = c.execute("SELECT id, status FROM jobs WHERE kind=? AND target=? AND status IN ('pending','running')",
                        (kind, target)).fetchone()
        if row:
            if force:  # a user request: move it to the front
                c.execute("UPDATE jobs SET priority=MAX(priority, ?), model=COALESCE(?, model), not_before=NULL, "
                          "reason=?, updated_at=? WHERE id=? AND status='pending'",
                          (BASE_PRIORITY[kind] + priority + 1000, model, reason, ts(), row["id"]))
            return row["id"]
        jid = uuid.uuid4().hex[:12]
        c.execute("INSERT INTO jobs(id,kind,target,status,priority,model,reason,max_attempts,created_at,updated_at) "
                  "VALUES (?,?,?,'pending',?,?,?,?,?,?)",
                  (jid, kind, target, BASE_PRIORITY[kind] + priority + (1000 if force else 0), model, reason,
                   max_attempts, ts(), ts()))
        return jid


def can_run(job, models_ok=None) -> bool:
    """Can *this* machine run the job? Model jobs need the routed model (or its fallback) to be reachable."""
    group = KIND_GROUP.get(job["kind"])
    if not group:
        return True
    from arxivletter.orchestration import models
    r = models.resolve(group, job["model"])
    ok = models_ok or models.available
    return ok("local" if models.kind(r.model) == "local" else r.model) or bool(
        r.fallback and ok("local" if models.kind(r.fallback) == "local" else r.fallback))


def claim(kinds: tuple[str, ...], *, overflow_model: str | None = None,
          budget: int | None = None, explicit_codex: bool = False) -> dict | None:
    now = ts()
    eligible = ""
    if overflow_model:
        eligible = "AND (model IS NULL OR model='codex:default') " if explicit_codex else "AND model IS NULL AND attempts=0 "
    elif explicit_codex:
        eligible = "AND (kind!='digest' OR (model IS NOT NULL AND model!='codex:default')) "
    args = (*kinds, now)
    if "digest" in kinds:
        eligible += f"AND NOT ({BELOW_DIGEST_THRESHOLD}) "
        args = (*kinds, settings.digest_min_relevance, now)
    with db.connect(immediate=True) as c:
        rows = c.execute(
            f"SELECT * FROM jobs WHERE status='pending' AND kind IN ({','.join('?' * len(kinds))}) "
            f"{eligible}AND (not_before IS NULL OR not_before<=?) ORDER BY priority DESC, created_at LIMIT 50",
            args).fetchall()
        for j in rows:
            if not overflow_model and not can_run(j):
                continue
            token = uuid.uuid4().hex
            lease = ts(_now() + timedelta(seconds=settings.job_lease_s))
            c.execute("UPDATE jobs SET status='running', attempts=attempts+1, worker=?, run_token=?, lease_until=?, "
                      "updated_at=? WHERE id=? AND status='pending'", (WORKER_NAME or "local", token, lease, now, j["id"]))
            claimed = dict(j, run_token=token, attempts=j["attempts"] + 1)
            if overflow_model:
                claimed.update(model=overflow_model, codex_overflow=True, call_budget=budget)
            return claimed
    return None


def renew(job: dict) -> None:
    """Extend the lease; raise Interrupted if the job was cancelled or reassigned meanwhile."""
    row = db.q1("SELECT status, run_token FROM jobs WHERE id=?", (job["id"],))
    if not row or row["status"] != "running" or row["run_token"] != job["run_token"]:
        raise Interrupted(f"job {job['id']} is no longer ours")
    if _stop.is_set():
        raise Interrupted("shutting down")
    with db.connect() as c:
        c.execute("UPDATE jobs SET lease_until=? WHERE id=? AND run_token=?",
                  (ts(_now() + timedelta(seconds=settings.job_lease_s)), job["id"], job["run_token"]))


def finish(job: dict, result: dict | None) -> None:
    with db.connect() as c:
        c.execute("UPDATE jobs SET status='done', result=?, error=NULL, lease_until=NULL, run_token=NULL, updated_at=? "
                  "WHERE id=? AND run_token=?", (json.dumps(result or {}, default=str)[:20000], ts(), job["id"],
                                                 job["run_token"]))


def fail(job: dict, error: str, permanent: bool = False) -> bool:
    """Returns True if the job will be retried."""
    retry = not permanent and job["attempts"] < job["max_attempts"]
    delay = min(60 * 2 ** (job["attempts"] + 1), 6 * 3600)
    with db.connect() as c:
        c.execute("UPDATE jobs SET status=?, error=?, not_before=?, lease_until=NULL, run_token=NULL, worker=NULL, "
                  "updated_at=? WHERE id=? AND run_token=?",
                  ("pending" if retry else "failed", error[:4000], ts(_now() + timedelta(seconds=delay)) if retry else None,
                   ts(), job["id"], job["run_token"]))
    return retry


def release(job: dict) -> None:
    """Interrupted (shutdown): back to the queue without counting the attempt."""
    with db.connect() as c:
        c.execute("UPDATE jobs SET status='pending', attempts=MAX(0, attempts-1), lease_until=NULL, run_token=NULL, "
                  "worker=NULL, updated_at=? WHERE id=? AND run_token=?", (ts(), job["id"], job["run_token"]))


def requeue_expired() -> int:
    with db.connect(immediate=True) as c:
        n = c.execute("UPDATE jobs SET status='pending', lease_until=NULL, run_token=NULL, worker=NULL, updated_at=? "
                      "WHERE status='running' AND lease_until<?", (ts(), ts())).rowcount
    if n:
        log.warning("requeued %d job(s) whose lease expired", n)
    return n


def recover_own() -> None:
    """At startup: jobs this machine was running when it stopped go back to the queue."""
    with db.connect() as c:
        c.execute("UPDATE jobs SET status='pending', lease_until=NULL, run_token=NULL, worker=NULL, updated_at=? "
                  "WHERE status='running' AND worker=?", (ts(), WORKER_NAME or "local"))


def cancel(job_id: str) -> bool:
    with db.connect() as c:
        return c.execute("UPDATE jobs SET status='cancelled', run_token=NULL, lease_until=NULL, updated_at=? "
                         "WHERE id=? AND status IN ('pending','running')", (ts(), job_id)).rowcount > 0


def retry(job_id: str) -> bool:
    with db.connect() as c:
        return c.execute("UPDATE jobs SET status='pending', attempts=0, not_before=NULL, error=NULL, updated_at=? "
                         "WHERE id=? AND status IN ('failed','cancelled')", (ts(), job_id)).rowcount > 0


def retry_failed(kind: str | None = None) -> int:
    with db.connect() as c:
        return c.execute("UPDATE jobs SET status='pending', attempts=0, not_before=NULL, error=NULL, updated_at=? "
                         "WHERE status='failed'" + (" AND kind=?" if kind else ""),
                         (ts(), kind) if kind else (ts(),)).rowcount


def stats() -> dict:
    rows = db.q("SELECT kind, status, COUNT(*) n FROM jobs GROUP BY kind, status")
    out: dict = {}
    for r in rows:
        out.setdefault(r["kind"], {})[r["status"]] = r["n"]
    return out


# ---------------------------------------------------------------- runner
def _run(job: dict) -> dict:
    from arxivletter.orchestration import llm, models, pipeline
    tok = llm.job_checkpoint.set(lambda: renew(job))
    mtok = models.run_model.set(job.get("model"))
    btok = llm.call_ceiling.set(job.get("call_budget"))
    try:
        return pipeline.HANDLERS[job["kind"]](job)
    finally:
        llm.job_checkpoint.reset(tok)
        models.run_model.reset(mtok)
        llm.call_ceiling.reset(btok)


def _claim_codex_overflow() -> dict | None:
    from arxivletter.orchestration import models

    routing = models.load_routing()
    model = "codex:default"
    explicit = routing["groups"]["digest"] == model
    if not explicit and (not routing["cloud_enabled"] or routing["groups"]["digest"] not in ("auto", "local")):
        return None
    limit = models.daily_limit()
    ceiling = limit if explicit else min(settings.codex_digest_daily_ceiling, limit)
    if ceiling > 0 and models.available(model) and models.calls_today(model) < ceiling:
        return claim(("digest",), overflow_model=model, budget=ceiling, explicit_codex=explicit)
    return None


def _loop(kinds: tuple[str, ...], idle_exit: bool) -> None:
    from arxivletter.arxiv.http import FetchError
    from arxivletter.orchestration import models
    from arxivletter.orchestration.llm import BudgetExceeded

    while not _stop.is_set():
        try:
            explicit = (not idle_exit and "digest" in kinds
                        and models.load_routing()["groups"]["digest"] == "codex:default")
            job = claim(kinds, explicit_codex=explicit)
            if not job and kinds == ("gaps", "brief") and not idle_exit:
                job = _claim_codex_overflow()
        except Exception as e:  # database briefly unreachable (worker mode): back off
            log.warning("claim failed: %s", e)
            time.sleep(10)
            continue
        if not job:
            if idle_exit and not db.q1(f"SELECT 1 FROM jobs WHERE status IN ('pending','running') AND kind IN "
                                       f"({','.join('?' * len(kinds))}) AND (not_before IS NULL OR not_before<=?)",
                                       (*kinds, ts())):
                return
            _stop.wait(2.0)
            continue
        t0 = time.monotonic()
        log.info("job %s %s %s (attempt %d)%s", job["id"], job["kind"], job["target"], job["attempts"],
                 " via Codex overflow" if job.get("codex_overflow") else "")
        try:
            result = _run(job)
            finish(job, result)
            log.info("job %s done in %.0fs", job["id"], time.monotonic() - t0)
        except (Interrupted, BudgetExceeded) as e:
            if isinstance(e, Interrupted) or job.get("codex_overflow"):
                release(job)
            else:
                fail(job, str(e))
        except FetchError as e:
            permanent = e.status is not None and 400 <= e.status < 500 and e.status not in (408, 429)
            will = fail(job, f"FetchError: {e}", permanent)
            log.warning("job %s failed (%s): %s", job["id"], "retrying later" if will else "giving up", e)
        except Exception as e:
            will = fail(job, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2500:]}")
            log.error("job %s failed (%s): %s", job["id"], "retrying later" if will else "giving up", e)


def start(kind_sets: list[tuple[str, ...]], idle_exit: bool = False) -> list[threading.Thread]:
    recover_own()
    _stop.clear()
    threads = []
    for kinds in kind_sets:
        t = threading.Thread(target=_loop, args=(kinds, idle_exit), daemon=True, name="jobs-" + "+".join(kinds))
        t.start()
        threads.append(t)
    return threads


def kind_sets_for(role: str) -> list[tuple[str, ...]]:
    """Threads per role. Premium jobs have a separate pool from bulk local work."""
    net = [("harvest",), ("fetch",)]
    llm_sets = (
        [("triage", "digest")] * max(1, settings.llm_workers)
        + [("gaps", "brief")] * max(1, settings.premium_workers)
    )
    if role == "server":
        return net if settings.fetch_on_server else [("harvest",)]
    if role == "worker":
        return llm_sets + ([] if settings.fetch_on_server else [("fetch",)])
    return net + llm_sets


def stop() -> None:
    _stop.set()
    from arxivletter.model_adapters.cli_agents import kill_all
    kill_all()


def run_until_idle(kind_sets: list[tuple[str, ...]]) -> None:
    for t in start(kind_sets, idle_exit=True):
        t.join()
