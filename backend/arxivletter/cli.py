"""arXivletter command line.

  arxivletter serve [--role all|server] [--host H] [--port P] [--no-background]
  arxivletter worker --server URL [--name N]          compute for a server (token from ARXIVLETTER_TOKEN)
  arxivletter harvest [--from D] [--to D]             queue OAI harvests (default: lookback window) and run them
  arxivletter backfill --from D --to D [--process]    harvest a date range (optionally run the whole pipeline)
  arxivletter add <arXiv id> [...]                    add papers by id (arXiv API) and process them
  arxivletter process [--kinds fetch,triage,digest,gaps,brief] [--limit-day D]   run queued jobs until idle
  arxivletter fetch <id>                              download + parse one paper now, print the structure
  arxivletter sweep                                   self-healing pass (queue missing steps)
  arxivletter status                                  queue, papers, workers
  arxivletter rescore                                 recompute relevance after editing the profile
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from arxivletter.config import settings
from arxivletter.storage import db


def _setup_logging() -> None:
    fmt = os.environ.get("ARXIVLETTER_LOG_FORMAT", "text")
    level = os.environ.get("ARXIVLETTER_LOG_LEVEL", "INFO").upper()
    if fmt == "json":
        class J(logging.Formatter):
            def format(self, r):
                d = {"ts": self.formatTime(r), "level": r.levelname, "logger": r.name, "msg": r.getMessage()}
                if r.exc_info:
                    d["exc"] = self.formatException(r.exc_info)
                return json.dumps(d)
        h = logging.StreamHandler()
        h.setFormatter(J())
        logging.basicConfig(level=level, handlers=[h])
    else:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    ap = argparse.ArgumentParser(prog="arxivletter")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8010)
    p.add_argument("--role", choices=["all", "server"], default=None)
    p.add_argument("--no-background", action="store_true", help="API only: no job threads, no scheduler")
    p = sub.add_parser("worker")
    p.add_argument("--server", default=os.environ.get("ARXIVLETTER_SERVER_URL"))
    p.add_argument("--name", default=None)
    p = sub.add_parser("harvest")
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p = sub.add_parser("backfill")
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p.add_argument("--process", action="store_true")
    p = sub.add_parser("add")
    p.add_argument("ids", nargs="+")
    p.add_argument("--process", action="store_true")
    p = sub.add_parser("process")
    p.add_argument("--kinds", default="fetch,triage,digest,gaps,brief")
    p = sub.add_parser("fetch")
    p.add_argument("id")
    p = sub.add_parser("reparse", help="re-parse stored LaTeX sources with the current parser (no download)")
    p.add_argument("--refetch-missing", action="store_true", help="queue fetch jobs where no source text is stored")
    sub.add_parser("sweep")
    sub.add_parser("status")
    sub.add_parser("rescore")
    a = ap.parse_args(argv)

    if a.cmd == "worker":
        if not a.server:
            raise SystemExit("set --server or ARXIVLETTER_SERVER_URL")
        from arxivletter.orchestration.remote_worker import run
        run(a.server, os.environ.get("ARXIVLETTER_TOKEN", ""), a.name)
        return 0
    if a.cmd == "serve":
        if a.role:
            os.environ["ARXIVLETTER_ROLE"] = a.role
        import uvicorn
        from arxivletter.api.app import create_app
        uvicorn.run(create_app(start_background=not a.no_background), host=a.host, port=a.port, log_level="info")
        return 0

    db.init(settings.db_path)
    from arxivletter.orchestration import jobs, pipeline
    jobs.WORKER_NAME = f"cli-{os.getpid()}"  # never confused with a server's own jobs during its crash recovery
    if a.cmd == "harvest":
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        start = a.start or (now - timedelta(days=settings.harvest_lookback_days)).strftime("%Y-%m-%d")
        ids = pipeline.harvest_window(start, a.end or now.strftime("%Y-%m-%d"), reason="user", force=True)
        print(f"queued {len(ids)} harvest job(s); running them")
        jobs.run_until_idle([("harvest",)])
        print(json.dumps(pipeline.sweep()))
    elif a.cmd == "backfill":
        ids = pipeline.harvest_window(a.start, a.end, reason="backfill")
        print(f"queued {len(ids)} harvest job(s)")
        jobs.run_until_idle([("harvest",)])
        if a.process:
            jobs.run_until_idle(jobs.kind_sets_for("all")[1:])
    elif a.cmd == "add":
        from arxivletter.arxiv import catalog, metadata
        pids = [metadata.parse_id(x)[0] for x in a.ids]
        for m in metadata.api_lookup(pids):
            print(m["id"], catalog.upsert(m)["status"])
            pipeline.advance(m["id"])
        if a.process:
            jobs.run_until_idle(jobs.kind_sets_for("all")[1:])
    elif a.cmd == "process":
        kinds = tuple(k.strip() for k in a.kinds.split(",") if k.strip())
        sets = [s for s in jobs.kind_sets_for("all") if set(s) & set(kinds)]
        sets = [tuple(k for k in s if k in kinds) for s in sets]
        jobs.run_until_idle(sets)
    elif a.cmd == "fetch":
        from arxivletter.arxiv import metadata
        from arxivletter.arxiv.source import fetch_document
        pid, v = metadata.parse_id(a.id)
        if v is None:
            m = metadata.api_lookup([pid])
            v = m[0]["versions"][-1]["v"] if m else 1
        doc, prov = fetch_document(pid, v)
        print(json.dumps({"provenance": prov, "stats": doc["stats"], "warnings": doc["warnings"],
                          "sections": [(s["id"], s["number"], s["title"], len(s["blocks"])) for s in doc["sections"]]},
                         indent=1))
    elif a.cmd == "reparse":
        from arxivletter.arxiv.source import PARSER_VERSION, reparse
        ok = missing = 0
        redo: list[str] = []
        for r in db.q("SELECT paper_id, version, provenance FROM content WHERE source_kind='latex'"):
            prov = json.loads(r["provenance"])
            if prov.get("parser_version") == PARSER_VERSION:
                continue
            doc = reparse(r["paper_id"], r["version"])
            if doc is None:
                missing += 1
                if a.refetch_missing:
                    with db.connect() as c:
                        c.execute("DELETE FROM jobs WHERE kind='fetch' AND target=? AND status!='running'",
                                  (f"{r['paper_id']}v{r['version']}",))
                        c.execute("DELETE FROM content WHERE paper_id=? AND version=?", (r["paper_id"], r["version"]))
                    pipeline.advance(r["paper_id"])
                continue
            prov["parser_version"] = PARSER_VERSION
            old = db.ungz(db.q1("SELECT data FROM content WHERE paper_id=? AND version=?",
                                (r["paper_id"], r["version"]))["data"])
            sig = lambda d: [(b["id"], b.get("text") or b.get("latex")) for s in d["sections"] for b in s["blocks"]]  # noqa: E731
            with db.connect() as c:
                c.execute("UPDATE content SET data=?, chars=?, provenance=? WHERE paper_id=? AND version=?",
                          (db.gz(doc), doc["stats"]["chars"], json.dumps(prov), r["paper_id"], r["version"]))
                if sig(old) != sig(doc):  # digests cite block ids: redo them (unchanged chunks come from the cache)
                    n = c.execute("DELETE FROM analyses WHERE paper_id=? AND version=? AND kind IN ('digest','gaps')",
                                  (r["paper_id"], r["version"])).rowcount
                    if n:
                        c.execute("UPDATE papers SET status='triaged', digested_version=NULL WHERE id=?", (r["paper_id"],))
                        redo.append(r["paper_id"])
            ok += 1
        for pid in redo:
            pipeline.advance(pid)
        print(f"re-parsed {ok}; digests to redo: {len(redo)}; without stored source: {missing}"
              + (" (fetch queued)" if a.refetch_missing else ""))
    elif a.cmd == "sweep":
        print(json.dumps(pipeline.sweep()))
    elif a.cmd == "status":
        print(json.dumps({"jobs": jobs.stats(),
                          "papers": {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM papers GROUP BY status")},
                          "workers": [dict(r) for r in db.q("SELECT name, last_seen FROM workers")]}, indent=1))
    elif a.cmd == "rescore":
        from arxivletter.analysis.relevance import rescore_all
        print(f"rescored {rescore_all()} papers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
