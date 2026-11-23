"""Metadata parsing, deduplicating upserts, the job queue (leases, retries) and the per-paper state machine."""

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest

from arxivletter.arxiv import catalog, metadata
from arxivletter.orchestration import jobs, pipeline
from arxivletter.storage import db

RAW = """<record xmlns="http://www.openarchives.org/OAI/2.0/"><header><identifier>oai:arXiv.org:2611.00001</identifier>
<datestamp>2026-11-20</datestamp></header><metadata>
<arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>2611.00001</id>
<version version="v1"><date>Wed, 18 Nov 2026 10:00:00 GMT</date><size>100kb</size></version>
<title>Freeze-in dark matter
  from the Schwinger effect</title>
<authors>A. One (MIT), B. Two and C. Three</authors><categories>hep-ph hep-th</categories>
<comments>10 pages</comments><abstract>  We study dark matter freeze-in via Schwinger pair production in strong fields. </abstract>
</arXivRaw></metadata></record>"""


def meta(v2: bool = False):
    d = metadata.parse_raw_record(ET.fromstring(RAW))
    if v2:
        d["versions"].append({"v": 2, "date": "2026-11-21T09:00:00Z", "size": "120kb", "source_type": None})
    return d


def test_parse_raw_record_and_ids():
    d = meta()
    assert d["id"] == "2611.00001" and d["title"] == "Freeze-in dark matter from the Schwinger effect"
    assert d["authors"] == ["A. One", "B. Two", "C. Three"]
    assert d["primary_category"] == "hep-ph" and d["versions"][0]["date"] == "2026-11-18T10:00:00Z"
    assert metadata.parse_id("https://arxiv.org/abs/2611.00001v3") == ("2611.00001", 3)
    assert metadata.parse_id("hep-ph/0505099") == ("hep-ph/0505099", None)
    assert metadata.set_spec("hep-ph") == "physics:hep-ph"
    with pytest.raises(ValueError):
        metadata.parse_id("not an id")


def test_upsert_dedup_revisions_and_calendar(tmp_db):
    assert catalog.upsert(meta())["status"] == "new"
    assert catalog.upsert(meta())["status"] == "unchanged"
    m = meta()
    m["comments"] = "10 pages, accepted"
    assert catalog.upsert(m)["status"] == "updated"
    assert catalog.upsert(meta(v2=True)) == {"status": "revised", "paper_id": "2611.00001", "version": 2}
    ev = [dict(r) for r in db.q("SELECT version, day, kind FROM paper_events ORDER BY version")]
    assert ev == [{"version": 1, "day": "2026-11-18", "kind": "new"}, {"version": 2, "day": "2026-11-21", "kind": "revision"}]
    p = db.q1("SELECT * FROM papers")
    assert p["latest_version"] == 2 and p["submitted_date"] == "2026-11-18"
    assert p["relevance"] > 4 and "Dark matter" in json.loads(p["topics"]) and "Strong fields" in json.loads(p["topics"])
    assert db.q1("SELECT COUNT(*) n FROM paper_fts WHERE paper_fts MATCH 'schwinger'")["n"] == 1


def test_advance_state_machine(tmp_db, sample_doc):
    catalog.upsert(meta())
    pipeline.advance("2611.00001")
    kinds = {r["kind"]: r["target"] for r in db.q("SELECT kind, target FROM jobs")}
    assert kinds == {"fetch": "2611.00001v1", "triage": "2611.00001"}
    pipeline.advance("2611.00001")  # idempotent
    assert db.q1("SELECT COUNT(*) n FROM jobs")["n"] == 2
    with db.connect() as c:
        c.execute("INSERT INTO content VALUES (?,?,?,?,?,?,?)", ("2611.00001", 1, "latex", db.gz(sample_doc), 100, "{}", catalog.now()))
        c.execute("INSERT INTO analyses(paper_id,version,kind,data,created_at) VALUES ('2611.00001',1,'triage','{}',?)", (catalog.now(),))
        c.execute("UPDATE jobs SET status='done'")
    pipeline.advance("2611.00001")
    assert db.q1("SELECT target FROM jobs WHERE kind='digest'")["target"] == "2611.00001v1"


def test_queue_claim_lease_retry_and_expiry(tmp_db):
    jid = jobs.enqueue("harvest", "physics:hep-ph|2026-11-01|2026-11-02")
    assert jobs.enqueue("harvest", "physics:hep-ph|2026-11-01|2026-11-02") == jid  # deduplicated
    j = jobs.claim(("harvest",))
    assert j and j["id"] == jid and jobs.claim(("harvest",)) is None
    assert jobs.fail(j, "boom") is True  # retried later with backoff
    row = db.q1("SELECT status, not_before FROM jobs WHERE id=?", (jid,))
    assert row["status"] == "pending" and row["not_before"] > jobs.ts()
    assert jobs.claim(("harvest",)) is None  # backoff respected
    with db.connect() as c:
        c.execute("UPDATE jobs SET not_before=NULL")
    j = jobs.claim(("harvest",))
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.connect() as c:
        c.execute("UPDATE jobs SET lease_until=? WHERE id=?", (past, jid))
    assert jobs.requeue_expired() == 1
    with pytest.raises(jobs.Interrupted):
        jobs.renew(j)  # the old attempt lost its lease
    j2 = jobs.claim(("harvest",))
    jobs.finish(j2, {"ok": 1})
    assert db.q1("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "done"


def test_model_jobs_wait_for_a_machine_with_the_model(tmp_db, fake_local):
    jobs.enqueue("triage", "2611.00001")
    assert jobs.claim(("triage",)) is None  # no local model reachable here
    fake_local(["{}"])
    assert jobs.claim(("triage",)) is not None
