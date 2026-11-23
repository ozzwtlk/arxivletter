"""FastAPI application: JSON API under /api (see docs/ARCHITECTURE.md), Prometheus metrics, the built frontend."""

from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from typing import Literal

from pydantic import BaseModel, Field

from arxivletter import __version__
from arxivletter.api import internal
from arxivletter.config import REPO_ROOT, settings
from arxivletter.storage import db

log = logging.getLogger("arxivletter.api")
ONLINE_S = 180


class BookmarkBody(BaseModel):
    key: str = Field(min_length=1, max_length=12000)
    paper_id: str = Field(min_length=1, max_length=100)
    kind: Literal["paper", "question", "gap", "idea"]
    paper_title: str = Field(max_length=4000)
    title: str = Field(max_length=4000)
    text: str = Field(max_length=50000)
    saved: bool


class ActionBody(BaseModel):
    action: str
    note: str | None = None


class ProcessBody(BaseModel):
    what: str  # digest | gaps | refetch | triage
    model: str | None = None


class AddBody(BaseModel):
    id: str


class BackfillBody(BaseModel):
    start: str
    end: str


class RoutingBody(BaseModel):
    groups: dict[str, str] = {}
    premium_default: str | None = None
    cloud_enabled: bool | None = None


class ProfileBody(BaseModel):
    text: str


def _j(s, default):
    return json.loads(s) if s else default


def _card(r, triage: dict | None = None, digest: dict | None = None, gaps: dict | None = None,
          event: dict | None = None) -> dict:
    authors = _j(r["authors"], [])
    detail = _j(r["relevance_detail"], {})
    return {
        "id": r["id"], "version": r["latest_version"], "title": r["title"], "authors": authors[:8],
        "n_authors": len(authors), "categories": _j(r["categories"], []), "primary_category": r["primary_category"],
        "submitted": r["submitted_v1"], "day": r["submitted_date"], "comments": r["comments"],
        "relevance": r["relevance"], "relevance_keyword": detail.get("keyword"), "relevance_model": detail.get("model"),
        "relevance_reason": detail.get("model_reason"),
        "interests": [i["interest"] for i in detail.get("interests", [])[:3]],
        "topics": _j(r["topics"], []), "status": r["status"], "digest_depth": r["digest_depth"],
        "starred": bool(r["starred"]), "hidden": bool(r["hidden"]), "read": bool(r["read_at"]),
        "one_liner": (triage or {}).get("one_liner"), "paper_type": (triage or {}).get("paper_type"),
        "tldr": (digest or {}).get("tldr"),
        "n_open_questions": len((digest or {}).get("open_questions") or []),
        "top_open_question": next((q.get("summary") or q.get("quote") for q in sorted(
            (digest or {}).get("open_questions") or [], key=lambda q: -(q.get("importance") or 0))), None),
        "n_gaps": len((gaps or {}).get("gaps") or []),
        "top_gap": ((gaps or {}).get("gaps") or [{}])[0].get("title"),
        "event_kind": (event or {}).get("kind"), "event_version": (event or {}).get("version"),
    }


def _analyses(ids: list[str]) -> dict[tuple[str, str], dict]:
    """Latest triage/digest/gaps per paper, in one query."""
    if not ids:
        return {}
    out: dict = {}
    for chunk in [ids[i:i + 400] for i in range(0, len(ids), 400)]:
        rows = db.q(f"SELECT paper_id, kind, version, data FROM analyses WHERE kind IN ('triage','digest','gaps') "
                    f"AND paper_id IN ({','.join('?' * len(chunk))}) ORDER BY version", chunk)
        for r in rows:
            out[(r["paper_id"], r["kind"])] = json.loads(r["data"])
    return out


def _cards(rows, events: dict | None = None) -> list[dict]:
    an = _analyses([r["id"] for r in rows])
    return [_card(r, an.get((r["id"], "triage")), an.get((r["id"], "digest")), an.get((r["id"], "gaps")),
                  (events or {}).get(r["id"])) for r in rows]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def create_app(start_background: bool = True) -> FastAPI:
    role = internal.role()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init(settings.db_path)
        threads = []
        if start_background:
            from arxivletter.orchestration import jobs, scheduler
            jobs.WORKER_NAME = None if role != "server" else "server"
            threads = jobs.start(jobs.kind_sets_for(role))
            if settings.scheduler and role in ("all", "server"):
                scheduler.start()
            log.info("started %d job thread(s) for role %s", len(threads), role)
        yield
        if start_background:
            from arxivletter.orchestration import jobs, scheduler
            scheduler.stop()
            jobs.stop()

    app = FastAPI(title="arXivletter", version=__version__, lifespan=lifespan)
    internal.install(app)

    # ------------------------------------------------------------ health / observability
    @app.get("/api/health")
    def health():
        db.q1("SELECT 1")
        last = db.q1("SELECT value FROM meta WHERE key='harvest.last_ok'")
        pend = db.q1("SELECT COUNT(*) n FROM jobs WHERE status IN ('pending','running')")["n"]
        return {"ok": True, "role": role, "version": __version__, "papers": db.q1("SELECT COUNT(*) n FROM papers")["n"],
                "jobs_active": pend, "last_harvest_ok": last["value"] if last else None}

    @app.get("/api/metrics", response_class=PlainTextResponse)
    def metrics():
        lines = ["# HELP arxivletter_papers Papers by status", "# TYPE arxivletter_papers gauge"]
        for r in db.q("SELECT status, COUNT(*) n FROM papers GROUP BY status"):
            lines.append(f'arxivletter_papers{{status="{r["status"]}"}} {r["n"]}')
        lines += ["# HELP arxivletter_jobs Jobs by kind and status", "# TYPE arxivletter_jobs gauge"]
        for r in db.q("SELECT kind, status, COUNT(*) n FROM jobs GROUP BY kind, status"):
            lines.append(f'arxivletter_jobs{{kind="{r["kind"]}",status="{r["status"]}"}} {r["n"]}')
        lines += ["# HELP arxivletter_llm_calls_total Model calls (24h)", "# TYPE arxivletter_llm_calls_total gauge"]
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        for r in db.q("SELECT model, model_class, cached, ok, COUNT(*) n, SUM(COALESCE(prompt_tokens,0)) pt, "
                      "SUM(COALESCE(completion_tokens,0)) ct, SUM(seconds) s FROM llm_calls WHERE ts>=? "
                      "GROUP BY model, model_class, cached, ok", (since,)):
            lab = f'model="{r["model"]}",class="{r["model_class"]}",cached="{r["cached"]}",ok="{r["ok"]}"'
            lines.append(f"arxivletter_llm_calls_total{{{lab}}} {r['n']}")
            lines.append(f"arxivletter_llm_prompt_tokens{{{lab}}} {r['pt']}")
            lines.append(f"arxivletter_llm_completion_tokens{{{lab}}} {r['ct']}")
            lines.append(f"arxivletter_llm_seconds{{{lab}}} {r['s'] or 0:.1f}")
        last = db.q1("SELECT value FROM meta WHERE key='harvest.last_ok'")
        if last:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last["value"].replace("Z", "+00:00"))).total_seconds()
            lines += ["# HELP arxivletter_harvest_age_seconds Seconds since the last successful harvest",
                      "# TYPE arxivletter_harvest_age_seconds gauge", f"arxivletter_harvest_age_seconds {age:.0f}"]
        online = sum(1 for w in _workers() if w["online"])
        lines += ["# TYPE arxivletter_workers_online gauge", f"arxivletter_workers_online {online}"]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------ calendar & days
    @app.get("/api/calendar")
    def calendar(month: str = Query(default=None, pattern=r"^\d{4}-\d{2}$")):
        month = month or _today()[:7]
        thr = settings.premium_min_relevance
        rows = db.q("""SELECT e.day,
                         SUM(e.kind='new') AS new, SUM(e.kind='revision') AS revisions,
                         MAX(CASE WHEN e.kind='new' THEN p.relevance END) AS top_relevance,
                         SUM(e.kind='new' AND p.relevance>=?) AS relevant,
                         SUM(e.kind='new' AND p.status='digested') AS digested,
                         SUM(e.kind='new' AND p.starred=1) AS starred
                       FROM paper_events e JOIN papers p ON p.id=e.paper_id
                       WHERE e.day LIKE ? AND p.hidden=0 GROUP BY e.day ORDER BY e.day""", (thr, month + "%"))
        briefs = {r["day"] for r in db.q("SELECT day FROM briefs WHERE day LIKE ?", (month + "%",))}
        months = [r["m"] for r in db.q("SELECT DISTINCT substr(day,1,7) m FROM paper_events ORDER BY m")]
        return {"month": month, "days": [dict(r, has_brief=r["day"] in briefs) for r in rows], "months": months,
                "latest_day": (db.q1("SELECT MAX(day) d FROM paper_events WHERE kind='new'") or {"d": None})["d"]}

    @app.get("/api/days/{day}")
    def day_view(day: str, include_revisions: bool = True):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise HTTPException(400, "day must be YYYY-MM-DD")
        rows = db.q("SELECT p.*, e.kind AS ekind, e.version AS eversion FROM paper_events e JOIN papers p "
                    "ON p.id=e.paper_id WHERE e.day=?" + ("" if include_revisions else " AND e.kind='new'")
                    + " ORDER BY p.relevance DESC", (day,))
        seen, uniq, events = set(), [], {}
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            uniq.append(r)
            events[r["id"]] = {"kind": r["ekind"], "version": r["eversion"]}
        b = db.q1("SELECT data, model, created_at FROM briefs WHERE day=?", (day,))
        brief = dict(json.loads(b["data"]), created_at=b["created_at"]) if b else None
        brief_job = db.q1("SELECT status FROM jobs WHERE kind='brief' AND target=? ORDER BY created_at DESC LIMIT 1", (day,))
        return {"day": day, "papers": _cards(uniq, events), "brief": brief,
                "brief_status": brief_job["status"] if brief_job else None}

    # ------------------------------------------------------------ search / listing
    @app.get("/api/papers")
    def papers(q: str | None = None, topic: str | None = None, start: str | None = None, end: str | None = None,
               min_relevance: float = 0, starred: bool = False, status: str | None = None, limit: int = 50,
               offset: int = 0, sort: str = "relevance"):
        where, args = ["p.hidden=0"], []
        if q:
            terms = " ".join(f'"{t}"' for t in re.findall(r"[\w\-\.]+", q)[:12])
            if re.fullmatch(r"\s*\d{4}\.\d{4,5}(v\d+)?\s*", q):
                where.append("p.id=?")
                args.append(q.strip().split("v")[0])
            elif terms:
                where.append("p.id IN (SELECT paper_id FROM paper_fts WHERE paper_fts MATCH ?)")
                args.append(terms)
        if topic:
            where.append("p.topics LIKE ?")
            args.append(f'%"{topic}"%')
        if start:
            where.append("p.submitted_date>=?")
            args.append(start)
        if end:
            where.append("p.submitted_date<=?")
            args.append(end)
        if min_relevance:
            where.append("p.relevance>=?")
            args.append(min_relevance)
        if starred:
            where.append("p.starred=1")
        if status:
            where.append("p.status=?")
            args.append(status)
        order = {"relevance": "p.relevance DESC", "date": "p.submitted_v1 DESC"}.get(sort, "p.relevance DESC")
        sql_where = " AND ".join(where)
        total = db.q1(f"SELECT COUNT(*) n FROM papers p WHERE {sql_where}", args)["n"]
        rows = db.q(f"SELECT p.* FROM papers p WHERE {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
                    (*args, min(limit, 200), offset))
        return {"total": total, "papers": _cards(rows)}

    @app.get("/api/papers/{pid:path}/content")
    def paper_content(pid: str, version: int | None = None):
        from arxivletter.orchestration.pipeline import load_content
        got = load_content(pid, version)
        if not got:
            raise HTTPException(404, "full text not fetched yet")
        doc, prov, v = got
        return {"version": v, "document": doc, "provenance": prov}

    @app.post("/api/papers/{pid:path}/actions")
    def paper_action(pid: str, body: ActionBody):
        sets = {"star": ("starred", 1), "unstar": ("starred", 0), "hide": ("hidden", 1), "unhide": ("hidden", 0)}
        with db.connect() as c:
            if body.action in sets:
                col, val = sets[body.action]
                n = c.execute(f"UPDATE papers SET {col}=? WHERE id=?", (val, pid)).rowcount
            elif body.action == "read":
                n = c.execute("UPDATE papers SET read_at=? WHERE id=?", (_today(), pid)).rowcount
            elif body.action == "unread":
                n = c.execute("UPDATE papers SET read_at=NULL WHERE id=?", (pid,)).rowcount
            elif body.action == "note":
                n = c.execute("UPDATE papers SET note=? WHERE id=?", ((body.note or "")[:20000], pid)).rowcount
            else:
                raise HTTPException(400, "unknown action")
        if not n:
            raise HTTPException(404, "no such paper")
        if body.action == "star":
            from arxivletter.orchestration.pipeline import advance
            advance(pid)  # starred papers get full digests and premium gaps
        return paper_detail(pid)

    @app.post("/api/papers/{pid:path}/process")
    def paper_process(pid: str, body: ProcessBody):
        from arxivletter.orchestration import jobs
        r = db.q1("SELECT latest_version, relevance FROM papers WHERE id=?", (pid,))
        if not r:
            raise HTTPException(404, "no such paper")
        c = db.q1("SELECT version FROM content WHERE paper_id=? ORDER BY version DESC LIMIT 1", (pid,))
        v = c["version"] if c else r["latest_version"]
        model = body.model if body.model not in (None, "", "auto") else None
        if body.what == "refetch":
            with db.connect() as cx:
                cx.execute("DELETE FROM content WHERE paper_id=? AND version=?", (pid, r["latest_version"]))
            jid = jobs.enqueue("fetch", f"{pid}v{r['latest_version']}", reason="user", force=True)
        elif body.what == "triage":
            jid = jobs.enqueue("triage", pid, reason="user", force=True, model=model)
        elif body.what in ("digest", "gaps"):
            if not c:
                raise HTTPException(409, "full text not fetched yet")
            if body.what == "gaps" and not db.q1("SELECT 1 FROM analyses WHERE paper_id=? AND kind='digest' AND version=?", (pid, v)):
                raise HTTPException(409, "the paper needs a digest first")
            with db.connect() as cx:  # a user request always produces a fresh job
                cx.execute("DELETE FROM jobs WHERE kind=? AND target=? AND status IN ('done','failed','cancelled')",
                           (body.what, f"{pid}v{v}"))
            jid = jobs.enqueue(body.what, f"{pid}v{v}", priority=r["relevance"] or 0, reason="user", force=True,
                               model=model)
        else:
            raise HTTPException(400, "what must be digest, gaps, refetch or triage")
        return {"job_id": jid}

    @app.get("/api/papers/{pid:path}")
    def paper_detail(pid: str):
        r = db.q1("SELECT * FROM papers WHERE id=?", (pid,))
        if not r:
            raise HTTPException(404, "no such paper")
        an = {}
        for a in db.q("SELECT * FROM analyses WHERE paper_id=? ORDER BY version", (pid,)):
            an[a["kind"]] = {"version": a["version"], "data": json.loads(a["data"]), "model": a["model"],
                             "model_class": a["model_class"], "prompt_version": a["prompt_version"],
                             "created_at": a["created_at"]}
        content = db.q1("SELECT version, source_kind, chars, provenance, fetched_at FROM content WHERE paper_id=? "
                        "ORDER BY version DESC LIMIT 1", (pid,))
        js = db.q("SELECT id, kind, target, status, attempts, error, model, reason, updated_at, worker FROM jobs "
                  "WHERE target=? OR target LIKE ? ORDER BY created_at DESC LIMIT 20", (pid, pid + "v%"))
        card = _card(r, an.get("triage", {}).get("data"), an.get("digest", {}).get("data"), an.get("gaps", {}).get("data"))
        return {**card, "authors": _j(r["authors"], []), "abstract": r["abstract"], "journal_ref": r["journal_ref"],
                "doi": r["doi"], "license": r["license"], "versions": _j(r["versions"], []), "note": r["note"],
                "relevance_detail": _j(r["relevance_detail"], {}), "analyses": an,
                "content": dict(content, provenance=json.loads(content["provenance"])) if content else None,
                "jobs": [dict(j, error=(j["error"] or "")[:600]) for j in js],
                "links": {"abs": f"https://arxiv.org/abs/{pid}", "pdf": f"https://arxiv.org/pdf/{pid}v{r['latest_version']}",
                          "html": f"https://arxiv.org/html/{pid}v{r['latest_version']}",
                          "inspire": f"https://inspirehep.net/arxiv/{pid}"}}

    @app.post("/api/papers")
    def add_paper(body: AddBody):
        from arxivletter.arxiv import catalog, metadata
        from arxivletter.orchestration.pipeline import advance
        try:
            pid, _ = metadata.parse_id(body.id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        metas = metadata.api_lookup([pid])
        if not metas:
            raise HTTPException(404, f"arXiv has no paper {pid}")
        r = catalog.upsert(metas[0])
        advance(pid)
        return paper_detail(pid) | {"upsert": r["status"]}

    # ------------------------------------------------------------ gaps feed
    @app.get("/api/gaps")
    def gaps_feed(start: str | None = None, end: str | None = None, topic: str | None = None, source: str = "all",
                  min_relevance: float = 0, limit: int = 150):
        end = end or _today()
        start = start or (datetime.fromisoformat(end) - timedelta(days=14)).strftime("%Y-%m-%d")
        where, args = ["p.hidden=0", "p.submitted_date BETWEEN ? AND ?", "COALESCE(p.relevance,0)>=?"], [start, end, min_relevance]
        if topic:
            where.append("p.topics LIKE ?")
            args.append(f'%"{topic}"%')
        rows = db.q(f"SELECT p.id, p.title, p.relevance, p.topics, p.submitted_date, p.authors FROM papers p "
                    f"WHERE {' AND '.join(where)} ORDER BY p.relevance DESC LIMIT 600", args)
        an = _analyses([r["id"] for r in rows])
        items = []
        for r in rows:
            base = {"paper_id": r["id"], "title": r["title"], "relevance": r["relevance"], "day": r["submitted_date"],
                    "topics": _j(r["topics"], []), "authors": _j(r["authors"], [])[:3]}
            dg, gp = an.get((r["id"], "digest")), an.get((r["id"], "gaps"))
            if dg and source in ("all", "author"):
                for q in dg.get("open_questions", []):
                    items.append({**base, "source": "author", "item": q,
                                  "score": (r["relevance"] or 0) + 1.5 * (q.get("importance") or 1)})
            if gp and source in ("all", "ai"):
                for g in gp.get("gaps", []):
                    items.append({**base, "source": "ai", "item": g, "model": gp.get("model"),
                                  "score": (r["relevance"] or 0) + 0.8 * (g.get("impact") or 1) +
                                  {"high": 1.5, "medium": 0.8, "low": 0}.get(g.get("confidence"), 0)})
                for idea in gp.get("ideas", []):
                    items.append({**base, "source": "idea", "item": idea, "model": gp.get("model"),
                                  "score": (r["relevance"] or 0) + 2})
        # diversity: each further item of the same paper ranks 1.2 lower, so one rich paper cannot fill the feed
        items.sort(key=lambda x: -x["score"])
        seen: dict[str, int] = {}
        for it in items:
            k = seen.get(it["paper_id"], 0)
            it["score"] -= 1.2 * k
            seen[it["paper_id"]] = k + 1
        items.sort(key=lambda x: -x["score"])
        return {"start": start, "end": end, "items": items[:limit], "total": len(items)}

    # ------------------------------------------------------------ briefs
    @app.post("/api/briefs/{day}")
    def make_brief(day: str, model: str | None = None):
        from arxivletter.orchestration import jobs
        with db.connect() as c:
            c.execute("DELETE FROM jobs WHERE kind='brief' AND target=? AND status IN ('done','failed','cancelled')", (day,))
        return {"job_id": jobs.enqueue("brief", day, reason="user", force=True,
                                       model=model if model not in (None, "", "auto") else None)}

    @app.get("/api/topics")
    def topics():
        from arxivletter.analysis.relevance import TOPICS
        counts = {t: 0 for t in TOPICS}
        since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        for r in db.q("SELECT topics FROM papers WHERE submitted_date>=?", (since,)):
            for t in _j(r["topics"], []):
                counts[t] = counts.get(t, 0) + 1
        return [{"name": t, "count_30d": n} for t, n in counts.items()]

    # ------------------------------------------------------------ operations
    @app.get("/api/jobs")
    def list_jobs(status: str | None = None, kind: str | None = None, limit: int = 100):
        where, args = [], []
        if status:
            where.append("status=?")
            args.append(status)
        if kind:
            where.append("kind=?")
            args.append(kind)
        sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + \
              " ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, " \
              "CASE WHEN status='pending' THEN -priority END, updated_at DESC LIMIT ?"
        from arxivletter.orchestration import jobs
        return {"stats": jobs.stats(), "jobs": [dict(r, error=(r["error"] or "")[:1500], result=_j(r["result"], None))
                                                for r in db.q(sql, (*args, min(limit, 500)))]}

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str):
        from arxivletter.orchestration import jobs
        if not jobs.retry(job_id):
            raise HTTPException(409, "only failed or cancelled jobs can be retried")
        return {"ok": True}

    @app.post("/api/jobs/retry-failed")
    def retry_failed(kind: str | None = None):
        from arxivletter.orchestration import jobs
        return {"retried": jobs.retry_failed(kind)}

    @app.delete("/api/jobs/{job_id}")
    def cancel_job(job_id: str):
        from arxivletter.orchestration import jobs
        if not jobs.cancel(job_id):
            raise HTTPException(409, "job is not pending or running")
        return {"ok": True}

    @app.get("/api/harvests")
    def harvests(limit: int = 50):
        return [dict(r) for r in db.q("SELECT * FROM harvests ORDER BY id DESC LIMIT ?", (limit,))]

    @app.post("/api/harvest")
    def harvest_now():
        from arxivletter.orchestration import pipeline
        end = _today()
        start = (datetime.now(timezone.utc) - timedelta(days=settings.harvest_lookback_days)).strftime("%Y-%m-%d")
        return {"job_ids": pipeline.harvest_window(start, end, reason="user", force=True)}

    @app.post("/api/backfill")
    def backfill(body: BackfillBody):
        from arxivletter.orchestration import pipeline
        try:
            s, e = datetime.fromisoformat(body.start), datetime.fromisoformat(body.end)
        except ValueError:
            raise HTTPException(400, "dates must be YYYY-MM-DD") from None
        if e < s or (e - s).days > 400:
            raise HTTPException(400, "end must be after start and the range at most 400 days")
        # announcements trail submissions by up to a few days: harvest a little past the end
        until = min(e + timedelta(days=5), datetime.now(timezone.utc).replace(tzinfo=None))
        return {"job_ids": pipeline.harvest_window(body.start, until.strftime("%Y-%m-%d"), reason="backfill")}

    @app.post("/api/sweep")
    def sweep():
        from arxivletter.orchestration import pipeline
        return pipeline.sweep()

    def _workers() -> list[dict]:
        now = datetime.now(timezone.utc)
        out = []
        for r in db.q("SELECT * FROM workers ORDER BY last_seen DESC"):
            seen = datetime.fromisoformat(r["last_seen"].replace("Z", "+00:00"))
            m = _j(r["models"], {})
            running = db.q("SELECT kind, target FROM jobs WHERE status='running' AND worker=?", (r["name"],))
            out.append({"name": r["name"], "online": (now - seen).total_seconds() < ONLINE_S, "last_seen": r["last_seen"],
                        "info": _j(r["info"], {}), "kinds": _j(r["kinds"], []),
                        "models": [{"id": x["id"], "label": x["label"], "available": x.get("available")}
                                   for x in m.get("models", [])],
                        "running": [dict(x) for x in running]})
        return out

    @app.get("/api/bookmarks")
    def bookmarks():
        return {"items": [dict(r) for r in db.q("SELECT * FROM bookmarks ORDER BY created_at DESC, key")]}

    @app.put("/api/bookmarks")
    def set_bookmark(body: BookmarkBody):
        with db.connect() as c:
            if body.saved:
                c.execute("INSERT OR IGNORE INTO bookmarks(key,paper_id,kind,paper_title,title,text) VALUES (?,?,?,?,?,?)",
                          (body.key, body.paper_id, body.kind, body.paper_title, body.title, body.text))
            else:
                c.execute("DELETE FROM bookmarks WHERE key=?", (body.key,))
        return {"saved": body.saved}

    @app.get("/api/workers")
    def workers():
        return {"role": role, "workers": _workers()}

    @app.get("/api/models")
    def models_report():
        from arxivletter.orchestration import models
        if role == "server":  # the server has no models: show what the online machines report
            seen: dict = {}
            for w in _workers():
                if not w["online"]:
                    continue
                r = db.q1("SELECT models FROM workers WHERE name=?", (w["name"],))
                for m in _j(r["models"], {}).get("models", []):
                    cur = seen.get(m["id"])
                    if not cur or (m.get("available") and not cur.get("available")):
                        seen[m["id"]] = dict(m, machine=w["name"])
            rep = {"models": list(seen.values()) or [dict(m, available=False, detail="no computing machine online")
                                                      for m in models.catalog()],
                   "groups": [{"id": g.id, "label": g.label, "heavy": g.heavy, "help": g.help, "auto": None}
                              for g in models.GROUPS]}
        else:
            rep = models.report()
        return rep | {"routing": models.load_routing(), "premium_order": list(settings.premium_order)}

    @app.put("/api/routing")
    def put_routing(body: RoutingBody):
        from arxivletter.orchestration import models
        try:
            return models.save_routing(body.groups, body.premium_default, body.cloud_enabled)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/usage")
    def usage(days: int = 14):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = db.q("SELECT substr(ts,1,10) day, model, model_class, task, SUM(cached=0) calls, SUM(cached) cached, "
                    "SUM(ok=0) errors, SUM(COALESCE(prompt_tokens,0)) pt, SUM(COALESCE(completion_tokens,0)) ct, "
                    "SUM(seconds) seconds FROM llm_calls WHERE ts>=? GROUP BY day, model, model_class, task "
                    "ORDER BY day DESC", (since,))
        return [dict(r) for r in rows]

    @app.get("/api/profile")
    def get_profile():
        from arxivletter.analysis import relevance
        p = relevance.profile()
        return {"text": relevance.profile_text(), "description": p.description,
                "interests": [{"name": i.name, "weight": i.weight, "keywords": list(i.keywords)} for i in p.interests],
                "follow": list(p.follow)}

    @app.put("/api/profile")
    def put_profile(body: ProfileBody):
        from arxivletter.analysis import relevance
        try:
            relevance.save_profile(body.text)
        except Exception as e:
            raise HTTPException(400, f"invalid profile: {e}") from None
        n = relevance.rescore_all()
        return get_profile() | {"rescored": n}

    @app.get("/api/settings")
    def get_settings():
        keys = ("categories", "harvest_every_min", "harvest_lookback_days", "full_digest_min_relevance",
                "digest_min_relevance", "premium_min_relevance", "premium_daily_papers", "brief_enabled", "brief_top_n",
                "reprocess_revisions_min_relevance", "local_llm_url", "local_model_name", "local_ctx_tokens",
                "subscription_daily_limit", "fetch_on_server")
        return {k: getattr(settings, k) for k in keys} | {"role": role}

    # ------------------------------------------------------------ frontend
    dist = REPO_ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "internal/")):
                raise HTTPException(404)
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    return app
