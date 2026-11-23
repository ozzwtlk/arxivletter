"""Digest/gaps pipeline on a fake model (grounding enforced in code), and the HTTP API."""

import json

from fastapi.testclient import TestClient

from arxivletter.arxiv import catalog
from arxivletter.orchestration import pipeline
from arxivletter.storage import db

from test_ingest_queue import meta

EXTRACT = {
    "section_gists": [{"section": "S2 Model", "gist": "Derives the pair-production rate (1)."}],
    "claims": [{"text": "The rate is exponentially suppressed.", "kind": "result", "refs": ["E1", "P999"]}],
    "equations": [{"id": "E1", "meaning": "Schwinger rate", "importance": 3}],
    "author_statements": [
        {"kind": "future_work", "summary": "Back-reaction is left for future work.",
         "quote": "which we leave for future work", "ref": "P5"},
        {"kind": "open_question", "summary": "Invented.", "quote": "We could not explain the muon g-2 anomaly", "ref": "P3"},
    ],
    "key_citations": [{"key": "Gies:2005", "role": "worldline numerics"}, {"key": "Fake:2030", "role": "x"}],
}
DIGEST = {
    "tldr": "Computes DM freeze-in through Schwinger pair production.", "central_question": "Can strong fields make DM?",
    "physical_motivation": "m", "intuition": "i",
    "assumptions": [{"text": "Constant field", "refs": ["P3"]}], "methods": [{"text": "Rate (1)", "refs": ["E1"]}],
    "key_equations": [{"id": "E1", "meaning": "rate", "role": "r"}, {"id": "P1", "meaning": "not an equation", "role": ""}],
    "results": [{"text": "Abundance matches for 42.7 GeV", "refs": ["E1"]}], "limitations": [],
    "implications": "x", "takeaways": ["t"],
    "reading_guide": {"audience": "a", "prerequisites": [], "sections_to_read": ["S2", "Sec. 9"], "difficulty": 3},
    "open_question_notes": [{"id": "Q1", "importance": 3, "context": "needs a self-consistent field"}],
}
GAPS = {
    "gaps": [{"title": "Inhomogeneous fields", "kind": "unexplored_regime", "description": "d", "reasoning": "r",
              "grounding": [{"ref": "P5", "quote": "We neglect back-reaction of the produced pairs"}, {"ref": "Q1", "quote": ""}],
              "builds_on": ["Q1", "Q9"], "first_steps": "f", "difficulty": 3, "impact": 4, "confidence": "medium",
              "relevance_to_reader": "y", "cite_keys": ["Gies:2005", "Invented:2031"], "novelty_check": "n"}],
    "ideas": [], "assessment": {"strengths": [], "weak_points": [], "robustness": "r"}, "what_to_check_first": "w",
}


def respond(req):
    if "TASK: take notes" in req.user_text:
        return json.dumps(EXTRACT)
    if "research gaps" in req.user_text or "TASK: return JSON with\n- gaps" in req.user_text:
        return json.dumps(GAPS)
    return json.dumps(DIGEST)


def setup_paper(sample_doc, rel=8.0):
    catalog.upsert(meta())
    with db.connect() as c:
        c.execute("INSERT INTO content VALUES (?,?,?,?,?,?,?)", ("2611.00001", 1, "latex", db.gz(sample_doc), 100,
                                                                   json.dumps({"source_kind": "latex"}), catalog.now()))
        c.execute("INSERT INTO analyses(paper_id,version,kind,data,created_at) VALUES ('2611.00001',1,'triage',?,?)",
                  (json.dumps({"one_liner": "x", "relevance": 8}), catalog.now()))
        c.execute("UPDATE papers SET relevance=? WHERE id='2611.00001'", (rel,))


def test_full_digest_is_grounded(tmp_db, fake_local, sample_doc):
    fake_local(respond)
    setup_paper(sample_doc)
    caveat = next(b["id"] for s in sample_doc["sections"] for b in s["blocks"] if "back-reaction" in b.get("text", ""))
    EXTRACT["author_statements"][0]["ref"] = caveat
    r = pipeline.run_digest({"id": "j", "target": "2611.00001v1", "model": None, "reason": "auto", "run_token": "t"})
    assert r["depth"] == "full"
    d = pipeline.analysis("2611.00001", "digest")[0]
    quotes = [q["quote"] for q in d["open_questions"]]
    assert "which we leave for future work" in quotes
    assert not any("g-2" in q for q in quotes)  # fabricated quote dropped
    assert any(q["match"] == "detected" and "open question" in q["quote"] for q in d["open_questions"])  # recall backstop
    q1 = next(q for q in d["open_questions"] if q["quote"] == "which we leave for future work")
    assert q1["importance"] == 3 and q1["section_title"] == "Caveats"
    assert [e["id"] for e in d["key_equations"]] == ["E1"] and d["key_equations"][0]["latex"].startswith("\\Gamma")
    assert d["results"][0]["unsupported_numbers"] == ["42.7"]
    assert d["reading_guide"]["sections_to_read"] == ["S2"]
    assert [c["key"] for c in d["key_citations"]] == ["Gies:2005"]
    assert d["section_gists"]["S2"].startswith("Derives")
    assert d["grounding"]["dropped_unverified"] == 1
    # premium gaps were queued automatically (relevance 8 ≥ 6.5) — run them on the fake model
    assert db.q1("SELECT 1 FROM jobs WHERE kind='gaps' AND target='2611.00001v1'")
    out = pipeline.run_gaps({"id": "g", "target": "2611.00001v1", "model": None, "reason": "auto", "run_token": "t"})
    gp = pipeline.analysis("2611.00001", "gaps")[0]
    g = gp["gaps"][0]
    assert g["cite_keys"] == ["Gies:2005"] and g["builds_on"] == ["Q1"] and not g["ungrounded"]
    assert g["grounding"][0]["match"] == "exact" and g["grounding"][1]["kind"] == "author_statement"
    assert out["gaps"] == 1


def test_light_digest_for_less_relevant_papers(tmp_db, fake_local, sample_doc):
    light = dict(DIGEST, author_statements=[{"kind": "open_question", "summary": "s",
                                              "quote": "It remains an open question whether the rate survives", "ref": "P1"}])
    light.pop("open_question_notes")
    ad = fake_local(lambda req: json.dumps(light))
    setup_paper(sample_doc, rel=2.0)
    r = pipeline.run_digest({"id": "j", "target": "2611.00001v1", "model": None, "reason": "auto", "run_token": "t"})
    assert r["depth"] == "light" and len(ad.requests) == 1
    d = pipeline.analysis("2611.00001", "digest")[0]
    assert d["open_questions"][0]["match"] == "moved"
    assert not db.q1("SELECT 1 FROM jobs WHERE kind='gaps'")  # below the premium threshold


def test_api(tmp_db, fake_local, sample_doc):
    from arxivletter.api.app import create_app
    fake_local(respond)
    setup_paper(sample_doc)
    with TestClient(create_app(start_background=False)) as cl:
        assert cl.get("/api/health").json()["papers"] == 1
        cal = cl.get("/api/calendar?month=2026-11").json()
        assert cal["days"][0]["day"] == "2026-11-18" and cal["days"][0]["new"] == 1
        day = cl.get("/api/days/2026-11-18").json()
        assert day["papers"][0]["id"] == "2611.00001" and day["papers"][0]["one_liner"] == "x"
        pipeline.run_digest({"id": "j", "target": "2611.00001v1", "model": None, "reason": "auto", "run_token": "t"})
        p = cl.get("/api/papers/2611.00001").json()
        assert p["analyses"]["digest"]["data"]["tldr"].startswith("Computes") and p["n_open_questions"] >= 1
        assert cl.get("/api/papers/2611.00001/content").json()["document"]["stats"]["equations"] == 4
        assert cl.get("/api/papers?q=schwinger").json()["total"] == 1
        assert cl.get("/api/gaps?start=2026-11-01&end=2026-11-30").json()["total"] >= 1
        assert cl.post("/api/papers/2611.00001/actions", json={"action": "star"}).json()["starred"] is True
        assert cl.post("/api/papers/2611.00001/process", json={"what": "gaps"}).status_code == 200
        assert "arxivletter_papers" in cl.get("/api/metrics").text
        assert cl.put("/api/profile", json={"text": "[reader]\ndescription='x'\n"}).json()["rescored"] == 1
        assert cl.put("/api/profile", json={"text": "not = [toml"}).status_code == 400
