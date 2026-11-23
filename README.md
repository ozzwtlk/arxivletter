# arXivletter

A daily research digest of **every new hep-ph and hep-th arXiv paper** — new submissions, cross-lists and revisions —
for a dark-matter phenomenologist who also follows formal QFT, especially QFT in strong background fields.

* **Calendar homepage** by arXiv submission date: per-day counts, a heat bar for highly relevant papers, topic /
  category / relevance filters, search, and an AI-written **daily brief** (must-reads, themes, cross-paper opportunities).
* **Relevance ranking** against your research profile (`config/profile.toml`), explained on every paper.
* **Researcher-level digests** from the full LaTeX source: central question, physical motivation, intuition,
  assumptions, methods, **key equations (the paper's own LaTeX) and what they mean**, results, limitations,
  implications, takeaways, a reading guide — every statement linked to the paragraph or equation it comes from.
* **Open problems first**: limitations, open questions and future directions **stated by the authors**, as verbatim
  quotes verified against the source with section / file:line references — and, clearly separated,
  **AI-inferred research gaps and ideas** from a premium reasoning model, with their reasoning, grounding, first
  steps, impact/difficulty/confidence and a novelty check. A cross-paper **Research gaps** feed ranks them all.
* **Never fabricates**: quotes, block ids, equations and citation keys are checked in code; unverifiable author
  claims are dropped; numbers missing from the paper are flagged; models can only cite the paper's own bibliography.

It reuses [AXIOM](../Axiom)'s architecture: local Qwen (llama.cpp) for the token-heavy work, Claude Code / Codex /
APIs only for selective premium reasoning, the same model routing, caching and metering, and the same split into an
always-on **server** (Raspberry Pi, Docker) and **computing machines** (your PC with the GPU).

## Quick start (one machine)

```bash
./arxivletter            # builds the UI, starts or reuses the local model on :8081, opens http://127.0.0.1:8010
./arxivletter stop       # also: status, logs, restart, install-desktop
```

Requirements: `uv`, `pnpm`, a local OpenAI-compatible model server (`scripts/start-local-llm.sh` starts llama.cpp with
Qwen3.6-35B-A3B exactly like AXIOM; an already running one is reused). Optional: `claude` / `codex` CLIs signed in, or
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, for the premium analyses.

On start the scheduler harvests the last three days of hep-ph and hep-th via OAI-PMH, downloads and parses the sources,
triages every paper, and digests them in relevance order. Backfill older days in **Operations**.

## Server + main PC (recommended)

```bash
# on the Pi / home server
cp .env.example .env && nano .env                 # ARXIVLETTER_TOKEN (./arxivletter new-token), ARXIVLETTER_CONTACT_EMAIL
docker compose up -d --build                      # http://<server>:8282   (or: ./arxivletter ship-server pi@<pi>)

# on the main PC (local model + optional Claude Code / Codex)
./arxivletter setup-worker http://<server>:8282 main-pc
./arxivletter                                     # connects as a worker
```

Full instructions, observability and security: [docs/DEPLOY.md](docs/DEPLOY.md). Design, data model, API and the
deviations from AXIOM: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Validation on real papers and known limitations:
[docs/STATUS.md](docs/STATUS.md).

## Command line

```bash
cd backend
uv run arxivletter serve [--role all|server] [--port 8010]
uv run arxivletter worker --server http://<server>:8282          # token in ARXIVLETTER_TOKEN
uv run arxivletter harvest [--from 2026-11-18 --to 2026-11-20]   # OAI-PMH, then run the harvest jobs
uv run arxivletter backfill --from 2026-11-01 --to 2026-11-07 [--process]
uv run arxivletter add 2611.25931 [--process]                    # any paper by id
uv run arxivletter process --kinds fetch,triage,digest,gaps,brief   # run queued jobs until idle
uv run arxivletter fetch 2611.25931                              # parse one paper now and print its structure
uv run arxivletter reparse [--refetch-missing]                   # after parser upgrades (no re-download)
uv run arxivletter status | sweep | rescore
```

## Configuration

* `.env` / environment (`ARXIVLETTER_*`): categories, harvest interval, thresholds for full digests and premium
  analysis, daily premium cap, model endpoints, premium preference order — see [.env.example](.env.example).
* `config/profile.toml`: your research interests (weighted keywords, followed authors, description given to the
  models). Editable in **Settings**; saving re-ranks every paper.
* **Settings → Models**: model per task group (triage, extraction, digests, gaps, brief), premium on/off.

## Tests

```bash
cd backend && uv run pytest -q && uv run ruff check arxivletter tests
cd frontend && pnpm build
```
