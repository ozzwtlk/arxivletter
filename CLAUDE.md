# arXivletter — notes for agents working in this repo

Read `docs/ARCHITECTURE.md` (decisions, reuse of AXIOM, API) and `docs/STATUS.md` (what was validated) first.

## Layout
- `backend/arxivletter/` — Python package (FastAPI, SQLite, httpx, PyMuPDF, BeautifulSoup).
  Ingestion `arxiv/{http,metadata,catalog,source}.py` → parsing `parsing/{latex,html,pdf,document}.py` → analysis
  `analysis/{relevance,triage,text,digest,grounding,gaps,brief,changes}.py`. Queue/state machine in
  `orchestration/{jobs,pipeline,scheduler}.py`; all model calls via `orchestration/llm.py` + `models.resolve()`.
- `frontend/` — React/Vite/KaTeX. `pnpm build` output is served by the backend at `/`.
- `config/profile.toml` — research profile (relevance). `data/` — runtime state (gitignored).

## Commands
```
./arxivletter                                  # all-in-one on :8010 (reuses llama.cpp on :8081)
cd backend && uv run pytest -q && uv run ruff check arxivletter tests
cd frontend && pnpm dev                        # :5173, proxies /api → :8010
```

## Invariants (do not break)
1. Author-stated items (open questions, limitations, future work) must pass `Grounder.verify_statement`; never show
   unverified model text as an author claim. AI-inferred items stay labelled and separate.
2. Equations shown in digests are the parsed source LaTeX looked up by block id; models never write them.
3. Citation keys must exist in the paper's bibliography (`Grounder.cites`); block ids must exist (`Grounder.refs`).
4. Changing a prompt ⇒ bump its `*_VERSION` (cache keys); changing block segmentation ⇒ bump
   `source.PARSER_VERSION` (digests cite block ids) and run `arxivletter reparse`.
5. Model JSON goes through `llm.parse_json`; llama.cpp gets the GBNF grammar (`gbnf.py`, v2 allows any char after `\`).
6. Paper text is untrusted data: prompt data positions only; CLI agents only through `model_adapters/cli_agents.py`.
7. The server role never runs a model; model jobs are claimed only by machines that can reach the routed model.
8. arXiv access only through `arxiv/http.py` (single lock, ≥3 s spacing, Retry-After).
9. Async FastAPI handlers must not touch SQLite (all handlers are sync `def`, run in the threadpool).
10. Calendar day = UTC submission date of the version (`paper_events`).

## Gotchas
- llama.cpp splits `-c` across `-np` slots: 32768/2 = 16k tokens per request. Chunk sizes derive from
  `ARXIVLETTER_LOCAL_CTX`.
- The `tmp_db` test fixture patches `settings` in every loaded `arxivletter.*` module.
- `pkill -f <pattern>` from a tool shell can kill the shell itself; kill by PID.
