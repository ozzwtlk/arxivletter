"""Papers table maintenance: deduplicating upserts from OAI/API metadata, calendar events and full-text search.

Deduplication: one row per arXiv id; versions are merged by number (OAI dates win over API dates). A record whose
latest version did not change is a metadata update (journal-ref, DOI, comments) and never triggers reprocessing.
Calendar: one `paper_events` row per version, on the UTC day it was submitted (v1 = new, v>1 = revision). Versions
submitted long before the record was announced (an old paper revised today) are kept in `versions` but not put on
the calendar of their old day, which would otherwise look like a complete listing when it is not.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from arxivletter.analysis import relevance
from arxivletter.storage import db

EVENT_MAX_AGE_DAYS = 45


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(iso: str | None) -> str | None:
    return iso[:10] if iso else None


def _merge_versions(old: list[dict], new: list[dict]) -> list[dict]:
    by = {v["v"]: dict(v) for v in old}
    for v in new:
        cur = by.get(v["v"])
        if not cur or (v.get("date") and (not cur.get("date") or v.get("size"))):
            by[v["v"]] = {**(cur or {}), **{k: x for k, x in v.items() if x is not None}}
    return [by[k] for k in sorted(by)]


def upsert(meta: dict) -> dict:
    """Insert or update one paper. Returns {status: new|revised|updated|unchanged, paper_id, version}."""
    pid = meta["id"]
    with db.connect(immediate=True) as c:
        row = c.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
        old_versions = json.loads(row["versions"]) if row else []
        versions = _merge_versions(old_versions, meta["versions"])
        latest = versions[-1]
        v1 = next((v for v in versions if v["v"] == 1), versions[0])
        paper = {**meta, "versions": versions}
        if row is None:
            score, detail = relevance.combine(paper)
            c.execute(
                "INSERT INTO papers(id,title,authors,abstract,categories,primary_category,comments,journal_ref,doi,"
                "license,report_no,versions,latest_version,submitted_v1,submitted_date,latest_submitted,oai_datestamp,"
                "source_meta,first_seen,updated_at,relevance,relevance_detail,topics,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new')",
                (pid, meta["title"], json.dumps(meta["authors"]), meta["abstract"], json.dumps(meta["categories"]),
                 meta["primary_category"], meta.get("comments"), meta.get("journal_ref"), meta.get("doi"),
                 meta.get("license"), meta.get("report_no"), json.dumps(versions), latest["v"], v1.get("date"),
                 _day(v1.get("date")), latest.get("date"), meta.get("oai_datestamp"), meta.get("source_meta", "oai"),
                 now(), now(), score, json.dumps(detail),
                 json.dumps(relevance.topics_for(meta["title"], meta["abstract"]))))
            status = "new"
        else:
            changed_text = row["title"] != meta["title"] or row["abstract"] != meta["abstract"]
            detail = json.loads(row["relevance_detail"] or "{}")
            score = row["relevance"]
            topics = json.loads(row["topics"] or "[]")
            if changed_text:
                score, det = relevance.combine(paper, detail.get("model"))
                det["model_reason"] = detail.get("model_reason")
                detail = det
                topics = sorted(set(relevance.topics_for(meta["title"], meta["abstract"])) | set(detail.get("model_topics") or []))
            c.execute(
                "UPDATE papers SET title=?,authors=?,abstract=?,categories=?,primary_category=?,comments=?,journal_ref=?,"
                "doi=?,license=COALESCE(?,license),report_no=COALESCE(?,report_no),versions=?,latest_version=?,"
                "submitted_v1=?,submitted_date=?,latest_submitted=?,oai_datestamp=COALESCE(?,oai_datestamp),"
                "updated_at=?,relevance=?,relevance_detail=?,topics=? WHERE id=?",
                (meta["title"], json.dumps(meta["authors"]), meta["abstract"], json.dumps(meta["categories"]),
                 meta["primary_category"], meta.get("comments"), meta.get("journal_ref"), meta.get("doi"),
                 meta.get("license"), meta.get("report_no"), json.dumps(versions), latest["v"], v1.get("date"),
                 _day(v1.get("date")), latest.get("date"), meta.get("oai_datestamp"), now(), score,
                 json.dumps(detail), json.dumps(topics), pid))
            if latest["v"] > row["latest_version"]:
                status = "revised"
            elif (changed_text or row["comments"] != meta.get("comments") or row["journal_ref"] != meta.get("journal_ref")
                  or len(versions) != len(old_versions)):
                status = "updated"
            else:
                status = "unchanged"
        # calendar events
        ref = datetime.now(timezone.utc)
        if meta.get("oai_datestamp"):
            ref = datetime.fromisoformat(meta["oai_datestamp"] + "T23:59:59+00:00")
        for v in versions:
            if not v.get("date"):
                continue
            when = datetime.fromisoformat(v["date"].replace("Z", "+00:00"))
            # papers added by hand (API metadata) go on the calendar with all versions
            if meta.get("source_meta") != "api" and ref - when > timedelta(days=EVENT_MAX_AGE_DAYS):
                continue
            c.execute("INSERT OR REPLACE INTO paper_events(paper_id, version, day, kind, submitted) VALUES (?,?,?,?,?)",
                      (pid, v["v"], _day(v["date"]), "new" if v["v"] == 1 else "revision", v["date"]))
        if status != "unchanged":
            _index(c, pid)
    return {"status": status, "paper_id": pid, "version": latest["v"]}


def _index(c, pid: str) -> None:
    r = c.execute("SELECT title, authors, abstract FROM papers WHERE id=?", (pid,)).fetchone()
    d = c.execute("SELECT data FROM analyses WHERE paper_id=? AND kind='digest' ORDER BY version DESC LIMIT 1",
                  (pid,)).fetchone()
    digest = ""
    if d:
        dd = json.loads(d["data"])
        digest = " ".join(str(dd.get(k) or "") for k in ("tldr", "central_question", "physical_motivation",
                                                         "implications")) + " " + " ".join(
            q.get("text", "") for q in dd.get("open_questions", []))
    c.execute("DELETE FROM paper_fts WHERE paper_id=?", (pid,))
    c.execute("INSERT INTO paper_fts(paper_id, title, authors, abstract, digest) VALUES (?,?,?,?,?)",
              (pid, r["title"], " ".join(json.loads(r["authors"])), r["abstract"], digest))


def reindex(pid: str) -> None:
    with db.connect() as c:
        _index(c, pid)
