"""Job handlers and the per-paper state machine.

`advance(paper_id)` looks at what a paper has and queues what it needs next; it is called after every job and by
the scheduler's sweep, so the pipeline heals itself after crashes, config changes or manual deletions:

    metadata ──► fetch (latest version) ──┐
           └──► triage ───────────────────┴──► digest (full if relevant, else light) ──► gaps (premium, selective)
    day complete ──► brief (premium)

Cost control (selective escalation):
  * triage + digests run on the local model;
  * full map-reduce digests only for relevance ≥ FULL_DIGEST_MIN_RELEVANCE (or starred / requested);
  * premium gaps only for relevance ≥ PREMIUM_MIN_RELEVANCE, at most PREMIUM_DAILY_PAPERS automatic ones per day;
  * revisions are re-digested only if relevant (REVISION_MIN_RELEVANCE), starred, or the change is major;
  * every model output is cached by (model, prompt version, input) — unchanged chunks of a revision are free.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from arxivletter.arxiv import catalog
from arxivletter.config import settings
from arxivletter.orchestration import jobs
from arxivletter.storage import db

log = logging.getLogger("arxivletter.pipeline")


def _paper(pid: str) -> dict | None:
    r = db.q1("SELECT * FROM papers WHERE id=?", (pid,))
    if not r:
        return None
    d = dict(r)
    for k in ("authors", "categories", "versions", "topics", "relevance_detail"):
        d[k] = json.loads(d[k] or ("{}" if k == "relevance_detail" else "[]"))
    return d


def load_content(pid: str, version: int | None = None) -> tuple[dict, dict, int] | None:
    if version is None:
        r = db.q1("SELECT * FROM content WHERE paper_id=? ORDER BY version DESC LIMIT 1", (pid,))
    else:
        r = db.q1("SELECT * FROM content WHERE paper_id=? AND version=?", (pid, version))
    if not r:
        return None
    return db.ungz(r["data"]), json.loads(r["provenance"]), r["version"]


def analysis(pid: str, kind: str, version: int | None = None) -> tuple[dict, dict] | None:
    if version is None:
        r = db.q1("SELECT * FROM analyses WHERE paper_id=? AND kind=? ORDER BY version DESC LIMIT 1", (pid, kind))
    else:
        r = db.q1("SELECT * FROM analyses WHERE paper_id=? AND kind=? AND version=?", (pid, kind, version))
    if not r:
        return None
    return json.loads(r["data"]), dict(r)


def store_analysis(pid: str, version: int, kind: str, data: dict, model: str | None, model_class: str | None,
                   prompt_version: str | None) -> None:
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO analyses(paper_id,version,kind,data,model,model_class,prompt_version,created_at)"
                  " VALUES (?,?,?,?,?,?,?,?)", (pid, version, kind, json.dumps(data, ensure_ascii=False), model,
                                                model_class, prompt_version, catalog.now()))


def _active(kind: str, target: str) -> bool:
    return bool(db.q1("SELECT 1 FROM jobs WHERE kind=? AND target=? AND status IN ('pending','running')", (kind, target)))


def _failed(kind: str, target: str) -> bool:
    return bool(db.q1("SELECT 1 FROM jobs WHERE kind=? AND target=? AND status IN ('failed','cancelled') "
                      "AND NOT EXISTS (SELECT 1 FROM jobs j2 WHERE j2.kind=jobs.kind AND j2.target=jobs.target "
                      "AND j2.status IN ('done','pending','running'))", (kind, target)))


def premium_auto_today() -> int:
    start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    r = db.q1("SELECT COUNT(*) n FROM jobs WHERE kind='gaps' AND reason='auto' AND created_at>=?", (start,))
    return int(r["n"]) if r else 0


def advance(pid: str) -> list[str]:
    p = _paper(pid)
    if not p or p["hidden"]:
        return []
    v = p["latest_version"]
    target = f"{pid}v{v}"
    rel = p["relevance"] or 0.0
    queued: list[str] = []
    content = db.q1("SELECT version, source_kind FROM content WHERE paper_id=? ORDER BY version DESC LIMIT 1", (pid,))
    if (not content or content["version"] < v) and not _active("fetch", target) and not _failed("fetch", target):
        queued.append(jobs.enqueue("fetch", target, priority=rel))
    triaged = db.q1("SELECT 1 FROM analyses WHERE paper_id=? AND kind='triage'", (pid,))
    if not triaged and not _active("triage", pid) and not _failed("triage", pid):
        queued.append(jobs.enqueue("triage", pid, priority=rel))
    if not content or not triaged:
        return [q for q in queued if q]
    cv = content["version"]
    if rel < settings.digest_min_relevance:
        return [q for q in queued if q]
    dg = db.q1("SELECT version FROM analyses WHERE paper_id=? AND kind='digest' ORDER BY version DESC LIMIT 1", (pid,))
    dtarget = f"{pid}v{cv}"
    want_digest = dg is None
    if dg is not None and dg["version"] < cv:  # a revision
        ch = analysis(pid, "changes", cv)
        major = bool(ch and ch[0].get("major"))
        want_digest = p["starred"] or rel >= settings.reprocess_revisions_min_relevance or (major and rel >= 3)
    if want_digest and not _active("digest", dtarget) and not _failed("digest", dtarget):
        queued.append(jobs.enqueue("digest", dtarget, priority=rel * 10))
    if dg is not None and dg["version"] == cv:
        gp = db.q1("SELECT version FROM analyses WHERE paper_id=? AND kind='gaps' ORDER BY version DESC LIMIT 1", (pid,))
        eligible = rel >= settings.premium_min_relevance or p["starred"]
        if eligible and (gp is None or (gp["version"] < cv and rel >= settings.premium_min_relevance)) \
                and not _active("gaps", dtarget) and not _failed("gaps", dtarget) \
                and premium_auto_today() < settings.premium_daily_papers:
            queued.append(jobs.enqueue("gaps", dtarget, priority=rel))
    return [q for q in queued if q]


# ---------------------------------------------------------------- handlers
def run_harvest(job: dict) -> dict:
    from arxivletter.arxiv import metadata
    set_spec, from_day, until_day = job["target"].split("|")
    with db.connect() as c:
        hid = c.execute("INSERT INTO harvests(set_spec, from_day, until_day, status, started_at) VALUES (?,?,?,?,?)",
                        (set_spec, from_day, until_day, "running", catalog.now())).lastrowid
    counts = {"records": 0, "new": 0, "revised": 0, "updated": 0, "unchanged": 0}
    try:
        for rec in metadata.oai_records(set_spec, from_day, until_day):
            r = catalog.upsert(rec)
            counts["records"] += 1
            counts[r["status"]] += 1
            if r["status"] in ("new", "revised"):
                advance(r["paper_id"])
            if counts["records"] % 50 == 0:
                jobs.renew(job)
    except Exception as e:
        with db.connect() as c:
            c.execute("UPDATE harvests SET status='failed', records=?, new_papers=?, new_versions=?, finished_at=?, "
                      "error=? WHERE id=?", (counts["records"], counts["new"], counts["revised"], catalog.now(),
                                             str(e)[:1000], hid))
        raise
    with db.connect() as c:
        c.execute("UPDATE harvests SET status='done', records=?, new_papers=?, new_versions=?, finished_at=? WHERE id=?",
                  (counts["records"], counts["new"], counts["revised"], catalog.now(), hid))
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('harvest.last_ok', ?)", (catalog.now(),))
    return counts


def _split_target(t: str) -> tuple[str, int]:
    pid, _, v = t.rpartition("v")
    return pid, int(v)


def run_fetch(job: dict) -> dict:
    from arxivletter.analysis import changes
    from arxivletter.arxiv.source import fetch_document
    pid, v = _split_target(job["target"])
    p = _paper(pid)
    if not p:
        return {"skipped": "paper deleted"}
    doc, prov = fetch_document(pid, v, p["title"], p["abstract"])
    prov["fetched_by"] = jobs.WORKER_NAME or "local"
    data = db.gz(doc)
    prev = load_content(pid)
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO content(paper_id, version, source_kind, data, chars, provenance, fetched_at) "
                  "VALUES (?,?,?,?,?,?,?)", (pid, v, doc["source_kind"], data, doc["stats"]["chars"],
                                             json.dumps(prov), catalog.now()))
        c.execute("UPDATE papers SET fetched_version=?, status=CASE WHEN status='new' THEN 'fetched' ELSE status END "
                  "WHERE id=?", (v, pid))
    if prev and prev[2] < v:
        ch = changes.diff(prev[0], doc, prev[2], v, p.get("comments"))
        store_analysis(pid, v, "changes", ch, None, None, "changes-v1")
    advance(pid)
    return {"source_kind": doc["source_kind"], "stats": doc["stats"], "warnings": doc["warnings"][:5]}


def run_triage(job: dict) -> dict:
    from arxivletter.analysis import relevance, triage
    pid = job["target"]
    p = _paper(pid)
    if not p:
        return {"skipped": "paper deleted"}
    out, meta = triage.run(p, job.get("model"))
    store_analysis(pid, p["latest_version"], "triage", out, meta["model"], meta["model_class"], triage.PROMPT_VERSION)
    score, detail = relevance.combine(p, out["relevance"])
    detail["model_reason"] = out.get("reason")
    detail["model_topics"] = out.get("topics", [])
    topics = sorted(set(p["topics"]) | set(out.get("topics", [])))
    with db.connect() as c:
        c.execute("UPDATE papers SET relevance=?, relevance_detail=?, topics=?, "
                  "status=CASE WHEN status IN ('new','fetched') THEN 'triaged' ELSE status END WHERE id=?",
                  (score, json.dumps(detail), json.dumps(topics), pid))
    advance(pid)
    return {"relevance": score, "model_rating": out["relevance"], "model": meta["model"]}


def run_digest(job: dict) -> dict:
    from arxivletter.analysis import digest
    pid, v = _split_target(job["target"])
    p = _paper(pid)
    got = load_content(pid, v)
    if not p or not got:
        return {"skipped": "paper or content missing"}
    doc, prov, _ = got
    rel = p["relevance"] or 0
    depth = "full" if (rel >= settings.full_digest_min_relevance or p["starred"] or job["reason"] == "user") else "light"
    meta = dict(p, version=v)
    out = digest.run(meta, doc, depth, job.get("model"))
    out["version"] = v
    out["parser_version"] = prov.get("parser_version")
    store_analysis(pid, v, "digest", out, ", ".join(out["models"]), ",".join(out["model_classes"]),
                   "+".join(out["prompt_versions"]))
    with db.connect() as c:
        c.execute("UPDATE papers SET status='digested', digested_version=?, digest_depth=? WHERE id=?", (v, depth, pid))
    catalog.reindex(pid)
    advance(pid)
    return {"depth": depth, "open_questions": len(out["open_questions"]), "calls": out["calls"],
            "cached": out["cached_calls"], "models": out["models"], "grounding": out["grounding"]}


def run_gaps(job: dict) -> dict:
    from arxivletter.analysis import gaps
    pid, v = _split_target(job["target"])
    p = _paper(pid)
    got = load_content(pid, v)
    dg = analysis(pid, "digest", v)
    if not p or not got or not dg:
        return {"skipped": "paper, content or digest missing"}
    out, meta = gaps.run(dict(p, version=v), got[0], dg[0], job.get("model"))
    out["version"] = v
    store_analysis(pid, v, "gaps", out, meta["model"], meta["model_class"], gaps.PROMPT_VERSION)
    with db.connect() as c:
        c.execute("UPDATE papers SET gaps_version=? WHERE id=?", (v, pid))
    return {"gaps": len(out["gaps"]), "ideas": len(out["ideas"]), "model": meta["model"], "grounding": out["grounding"]}


def day_papers(day: str, limit: int | None = None) -> list[dict]:
    rows = db.q("SELECT p.* FROM paper_events e JOIN papers p ON p.id=e.paper_id WHERE e.day=? AND e.kind='new' "
                "AND p.hidden=0 ORDER BY p.relevance DESC" + (f" LIMIT {int(limit)}" if limit else ""), (day,))
    return [dict(r, topics=json.loads(r["topics"])) for r in rows]


def run_brief(job: dict) -> dict:
    from arxivletter.analysis import brief
    day = job["target"]
    papers = day_papers(day, settings.brief_top_n)
    if not papers:
        return {"skipped": "no papers"}
    notes, ids = [], []
    for p in papers:
        dg = analysis(p["id"], "digest")
        gp = analysis(p["id"], "gaps")
        tr = analysis(p["id"], "triage")
        notes.append(brief.paper_note(p, dg[0] if dg else None, gp[0] if gp else None, tr[0] if tr else None))
        ids.append(p["id"])
    out, meta = brief.run(day, notes, ids, job.get("model"))
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO briefs(day, data, model, created_at) VALUES (?,?,?,?)",
                  (day, json.dumps(out, ensure_ascii=False), meta["model"], catalog.now()))
    return {"papers": len(ids), "model": meta["model"]}


HANDLERS = {"harvest": run_harvest, "fetch": run_fetch, "triage": run_triage, "digest": run_digest,
            "gaps": run_gaps, "brief": run_brief}


# ---------------------------------------------------------------- scheduling helpers
def harvest_window(from_day: str, until_day: str, reason: str = "auto", force: bool = False) -> list[str]:
    from arxivletter.arxiv.metadata import set_spec
    ids = []
    start = datetime.fromisoformat(from_day)
    end = datetime.fromisoformat(until_day)
    while start <= end:  # ≤ 7-day windows keep each job short and resumable
        stop = min(start + timedelta(days=6), end)
        for cat in settings.categories:
            ids.append(jobs.enqueue("harvest", f"{set_spec(cat)}|{start.date()}|{stop.date()}", reason=reason,
                                    force=force))
        start = stop + timedelta(days=1)
    return [i for i in ids if i]


def maybe_brief(day: str) -> str | None:
    """Queue the brief for a past day once most of its top papers have digests."""
    if not settings.brief_enabled or db.q1("SELECT 1 FROM briefs WHERE day=?", (day,)) or _active("brief", day) \
            or _failed("brief", day):
        return None
    top = day_papers(day, settings.brief_top_n)
    if len(top) < 3:
        return None
    done = sum(1 for p in top if db.q1("SELECT 1 FROM analyses WHERE paper_id=? AND kind='digest'", (p["id"],)))
    if done >= 0.8 * len(top):
        return jobs.enqueue("brief", day)
    return None


def sweep(days: int = 10) -> dict:
    """Self-healing pass: expired leases back to the queue, every recent paper advanced, briefs for ready days."""
    n_exp = jobs.requeue_expired()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    queued = 0
    for r in db.q("SELECT DISTINCT paper_id FROM paper_events WHERE day>=?", (since,)):
        queued += len(advance(r["paper_id"]))
    briefs = 0
    for r in db.q("SELECT DISTINCT day FROM paper_events WHERE day>=? AND day<? ORDER BY day", (since,
                  datetime.now(timezone.utc).strftime("%Y-%m-%d"))):
        briefs += bool(maybe_brief(r["day"]))
    return {"expired_requeued": n_exp, "queued": queued, "briefs": briefs}
