"""Server (stores, schedules, serves) ⇄ worker (computes) against a real `arxivletter serve --role server` process."""

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

TOKEN = "test-token-0123456789abcdef"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("server-data")
    port = _free_port()
    env = dict(os.environ, ARXIVLETTER_DATA_DIR=str(data), ARXIVLETTER_TOKEN=TOKEN, ARXIVLETTER_SCHEDULER="0")
    p = subprocess.Popen([sys.executable, "-m", "arxivletter.cli", "serve", "--role", "server", "--port", str(port)],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            if httpx.get(url + "/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        p.kill()
        pytest.fail("server did not start")
    yield url
    p.terminate()
    p.wait(10)


@pytest.fixture
def remote(server):
    from arxivletter.orchestration import jobs
    from arxivletter.storage import db
    db.init_remote(server, TOKEN)
    jobs.WORKER_NAME = "pc"
    yield server
    db._remote = None
    jobs.WORKER_NAME = None


def test_worker_premium_pool_is_independent(monkeypatch):
    from arxivletter.config import Settings
    from arxivletter.orchestration import jobs

    monkeypatch.setenv("ARXIVLETTER_LLM_WORKERS", "2")
    monkeypatch.setenv("ARXIVLETTER_PREMIUM_WORKERS", "10")
    monkeypatch.setattr(jobs, "settings", Settings())
    pools = jobs.kind_sets_for("worker")
    assert pools.count(("triage", "digest")) == 2
    assert pools.count(("gaps", "brief")) == 10
    assert ("gaps", "brief") not in jobs.kind_sets_for("server")


def test_internal_requires_token(server):
    assert httpx.post(server + "/internal/db/run", json={"sql": "SELECT 1", "params": []}).status_code == 401
    r = httpx.post(server + "/internal/db/run", json={"sql": "SELECT 1", "params": []},
                   headers={"Authorization": "Bearer " + TOKEN})
    assert r.status_code == 200 and r.json()["rows"] == [[1]]


def test_worker_claims_through_the_servers_database(remote, fake_local):
    from arxivletter.orchestration import jobs
    from arxivletter.storage import db
    jid = jobs.enqueue("triage", "2611.99999")
    fake_local(["{}"])
    j = jobs.claim(("triage",))
    assert j["id"] == jid
    assert db.q1("SELECT worker, status FROM jobs WHERE id=?", (jid,))["worker"] == "pc"
    jobs.renew(j)
    jobs.finish(j, {"ok": True})
    assert db.q1("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "done"
    with db.connect() as c:  # a server-side transaction; gzip blobs round-trip
        c.execute("INSERT INTO meta(key, value) VALUES ('blob', ?)", (db.gz({"a": 1}).hex(),))
    assert db.q1("SELECT value FROM meta WHERE key='blob'")


def test_overflow_claim_does_not_replace_normal_digest_route(remote, monkeypatch):
    from arxivletter.orchestration import jobs
    from arxivletter.storage import db

    monkeypatch.setattr(jobs, "can_run", lambda job: True)
    explicit = jobs.enqueue("digest", "2611.99998v1", priority=99, model="local")
    eligible = jobs.enqueue("digest", "2611.99999v1")
    claimed = jobs.claim(("digest",), overflow_model="codex:default")
    assert claimed["id"] == eligible and claimed["model"] == "codex:default"
    assert claimed["codex_overflow"]
    jobs.release(claimed)
    assert db.q1("SELECT status FROM jobs WHERE id=?", (explicit,))["status"] == "pending"
    row = db.q1("SELECT status, model, attempts FROM jobs WHERE id=?", (eligible,))
    assert (row["status"], row["model"], row["attempts"]) == ("pending", None, 0)

    # Explicit Codex routing reserves unassigned digests for the premium pool, not the local lane.
    assert jobs.claim(("digest",), explicit_codex=True)["id"] == explicit
    assert jobs.claim(("digest",), explicit_codex=True) is None
    claimed = jobs.claim(("digest",), overflow_model="codex:default", budget=60, explicit_codex=True)
    assert claimed["id"] == eligible and claimed["call_budget"] == 60


def test_remote_budget_counts_in_flight_calls(remote):
    from arxivletter.orchestration import llm

    llm._reserve_call(None, "digest", "codex:test", "premium", 1)
    with pytest.raises(llm.BudgetExceeded):
        llm._reserve_call(None, "digest", "codex:test", "premium", 1)


def test_bookmarks_persist_and_toggle(server):
    url = server + "/api/bookmarks"
    item = {"key": "question-test", "paper_id": "2611.99999", "kind": "question",
            "paper_title": "Paper", "title": "An open question", "text": "This remains open."}
    for _ in range(2):
        assert httpx.put(url, json={**item, "saved": True}).json() == {"saved": True}
    saved = httpx.get(url).json()["items"]
    assert len(saved) == 1 and all(saved[0][k] == v for k, v in item.items())
    assert httpx.put(url, json={**item, "saved": False}).json() == {"saved": False}
    assert httpx.get(url).json() == {"items": []}


def test_heartbeat_and_workers_endpoint(server):
    r = httpx.post(server + "/internal/heartbeat", headers={"Authorization": "Bearer " + TOKEN},
                   json={"name": "main-pc", "models": {"models": [{"id": "local", "label": "Qwen", "kind": "local",
                                                                    "available": True}]}, "kinds": ["digest"]})
    assert r.status_code == 200
    w = httpx.get(server + "/api/workers").json()
    assert w["role"] == "server" and w["workers"][0]["name"] == "main-pc" and w["workers"][0]["online"]
    m = httpx.get(server + "/api/models").json()
    assert any(x["id"] == "local" and x["available"] and x["machine"] == "main-pc" for x in m["models"])
