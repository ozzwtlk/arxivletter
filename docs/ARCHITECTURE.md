# arXivletter — Architecture

A research digest of every new **hep-ph** and **hep-th** arXiv paper (new submissions, cross-lists and revisions),
built for a dark-matter phenomenologist who also follows formal QFT in strong background fields. The design goal:
make it fast to decide *what to read* and *what to investigate next*, without ever presenting model output as the
authors' claims.

## What was reused from AXIOM (`../Axiom`)

| AXIOM piece | Reused as | Changes |
|---|---|---|
| Stack: Python/FastAPI/SQLite (WAL, FTS5)/httpx; React/Vite/KaTeX, plain CSS tokens | same | — |
| `storage/db.py`: thread-local SQLite + **remote mode** (worker runs SQL on the server, server-held `BEGIN IMMEDIATE` transactions, `RemoteRow`) | `storage/db.py` | new schema; gzip helpers |
| `api/internal.py`: token-protected `/internal/db/*`, transaction reaper | `api/internal.py` | WebSocket forwarding replaced by `/internal/heartbeat` (see deviations) |
| `model_adapters/*`: llama.cpp OpenAI adapter with LaTeX-tolerant **GBNF**, Claude Code / Codex CLI adapters (no tools, empty cwd, no MCP/settings/hooks, no API keys), Anthropic API | copied | GBNF v2 (bug fix below) |
| `orchestration/llm.py`: cache keyed by (model, prompt version, input, decoding), metering in `llm_calls`, caps, `call_json` with validator-driven retries, fallback and escalation, `parse_json` LaTeX-escape repair | copied | per-day caps instead of per-book budgets; `\u…` repair |
| `orchestration/models.py`: task groups, `auto` routing, per-group Settings, per-request override | adapted | groups: triage, extract, digest (local) · gaps, brief (premium) |
| Semantic windows (never split an equation from its paragraph / continuation) | `analysis/text.py` chunking | sized for 16k-token llama.cpp slots |
| Evidence over self-report: faithfulness checker may only revert/drop/flag | `analysis/grounding.py` | quotes/ids/citations/numbers verified against the parsed source |
| Equations are always the source's LaTeX (invariant 1) | key equations are looked up by block id | — |
| Launcher (`./axiom`), Docker server image, Pi compose, `ship-server`, `setup-worker` | `./arxivletter`, `docker/`, `deploy/pi/` | ports 8010 (local) / 8282 (server) |
| Reader look: Source Serif + Inter, Okabe–Ito hues, KaTeX math-protecting Markdown renderer | `frontend/src/styles.css`, `lib/markdown.ts` | per-paper KaTeX macros, `[cite:…]` links |

**Bug found and fixed (also present in AXIOM):** AXIOM's GBNF grammar only allowed a *letter* after a backslash in
JSON strings, so `61\%`, `\,`, `\;`, `\{` were impossible; llama.cpp then forced the model into a letter command
(`61\%` came out as `61\pi` on a real paper). `model_adapters/gbnf.py` v2 allows any printable character after
`\` (`DECODING = gbnf-latex-v2`, part of cache keys), and `parse_json` repairs `\u` not followed by hex
(`\upsilon`, `\underline`). AXIOM should take the same two-line fix.

## Deviations from AXIOM (and why)

1. **The server does network + parsing work** (harvest, download, LaTeX/HTML/PDF parsing), not just storage. These
   need no model and little CPU (fine on a Pi 5), and doing them on the always-on box keeps the calendar and full
   texts current while the PC is off. `ARXIVLETTER_FETCH_ON_SERVER=0` moves fetching to workers.
2. **No WebSocket request forwarding.** AXIOM forwards interactive AI requests to a worker. Here every compute request
   (re-digest, premium analysis, brief) is a queued job; the reader polls. Jobs hold **leases** renewed at every model
   call, so a vanished worker's jobs return to the queue automatically (`requeue_expired`). Workers report models via
   `/internal/heartbeat`. Simpler, and robust to flaky links.
3. **Cloud on by default.** AXIOM keeps book text local unless the user opts in; arXiv papers are public, so `auto` may
   use premium models — but only for the two reasoning groups, selectively (see cost control).
4. **Per-day caps** for subscription CLIs / paid APIs instead of per-book budgets.

## Components

```
backend/arxivletter/
  arxiv/http.py        polite client: one lock, ≥3.1 s between requests, Retry-After, exponential backoff
  arxiv/metadata.py    OAI-PMH arXivRaw (all versions + exact dates), arXiv API (id lookup, submittedDate search)
  arxiv/catalog.py     dedup upserts (id + version), calendar events, FTS index
  arxiv/source.py      e-print → LaTeX parse; else arXiv HTML; else PDF; else abstract. Provenance + stored source text
  parsing/latex.py     main-file detection, \input flattening with file:line provenance, env-shortcut macros (\beq),
                       front matter, sections (revtex/JHEP numbering + LaTeXML anchors), display math (aligned/array
                       conversion, numbering incl. subequations/\tag/appendix), floats, \ref/\eqref resolution,
                       citations, footnotes, .bbl/.bib bibliography, KaTeX macros
  parsing/html.py      LaTeXML HTML (alttext TeX, exact equation tags, bib ids)
  parsing/pdf.py       PyMuPDF text layer (marked low math fidelity)
  analysis/relevance.py  profile (TOML) keyword score + model rating → relevance; topic taxonomy
  analysis/text.py     block rendering, chunking, light selection, author-statement phrase detector
  analysis/triage.py   local: relevance 0–10 + reason, topics, type, one-liner
  analysis/digest.py   local: full (map per chunk → reduce) or light (one call); grounding enforced
  analysis/gaps.py     premium: AI-inferred gaps, ideas, critical assessment
  analysis/brief.py    premium: daily cross-paper synthesis
  analysis/changes.py  deterministic diff between versions
  analysis/grounding.py  quote / id / citation / number verification
  orchestration/       llm (calls, cache), models (routing), jobs (queue), pipeline (handlers, state machine),
                       scheduler, remote_worker
  api/app.py           JSON API, Prometheus metrics, SPA
frontend/src/          Calendar home, day view + brief, paper view, research-gap feed, search, operations, settings
```

## Data model (SQLite, `data/arxivletter.db`)

`papers` (one row per arXiv id; versions JSON; relevance + explanation; topics; status; star/hide/read/note) ·
`paper_events` (calendar: one row per version on its UTC submission day, `new` / `revision`) · `content` (gzip JSON of
the parsed document per version + provenance) · `analyses` (triage / digest / gaps / changes per version) · `briefs` ·
`jobs` · `harvests` · `workers` · `llm_cache` · `llm_calls` · `paper_fts` (FTS5 over title, authors, abstract, digest).
`data/papers/<id>/v<n>.src.json.gz`: the source text files (tex/bbl/bib), for `arxivletter reparse`.

## Ingestion

* **Harvest** (scheduler, hourly): OAI-PMH `ListRecords`, `metadataPrefix=arXivRaw`, sets `physics:hep-ph` and
  `physics:hep-th`, datestamps of the last `HARVEST_LOOKBACK_DAYS` days. Includes new papers, cross-lists and
  replacements; a record without a new version is a metadata update and never reprocessed. Backfill = the same over
  any range (7-day jobs).
* **Calendar = arXiv submission date (UTC)** of each version: v1 → "new" on its day, vN → "revision" on its day.
  Versions submitted > 45 days before their announcement (old paper revised now) are not back-filled onto old days.
* **Fetch**: `export.arxiv.org/e-print/<id>vN` → LaTeX; `arxiv.org/html/<id>vN` → LaTeXML; `export.arxiv.org/pdf`
  → text layer; else abstract only. A parse with < 1,500 characters or < 5 paragraphs counts as failed. Every attempt
  is recorded in the provenance.
* **Parsed document**: sections → blocks with stable ids (`P12` paragraph, `E5` equation, `F2` figure, `T1` table);
  paragraphs are Markdown with `$…$`; citations `[cite:key]`; equations carry number, label, source file and line.
  Equation numbers are reconstructed from the class conventions and matched LaTeXML's exact numbering on the test
  papers. KaTeX rendered 18,153 of 18,513 display equations (98.06%) in the 175-paper validation sample;
  unrenderable equations remain available as raw TeX. See [validation status](STATUS.md).

## Processing and cost control

State machine (`pipeline.advance`, also run by the 10-minute sweep — the pipeline heals itself):

```
metadata ─► fetch(vN) ─┐
         └► triage ────┴► digest(vN): full if relevance ≥ 4.5 or starred, else light ─► gaps(vN) if relevance ≥ 6.5
day mostly digested ─► brief(day)
```

| Stage | Model | Calls per paper | Input |
|---|---|---|---|
| triage | local | 1 short | title, abstract, profile |
| light digest | local | 1 | abstract + intro + conclusions + author-statement paragraphs (~20k chars) |
| full digest | local | chunks (≤ 8) + 1 reduce | whole text in ≤ 21k-char chunks, then verified notes |
| gaps | premium (fallback local) | 1 | digest + verified author statements + intro/conclusions + bibliography keys |
| brief | premium (fallback local) | 1 per day | top-12 digests of the day |

* Escalation is selective (relevance thresholds, `PREMIUM_DAILY_PAPERS`, stars, explicit requests) and, as in AXIOM,
  evidence-based for retries: invalid JSON or failed validation is retried with the error.
* Every call is cached by (model, prompt version, exact input, decoding). Unchanged chunks of a revision cost nothing.
* Revisions are re-digested only if relevant, starred, or the deterministic diff says the change is major.
* Every call is metered (`llm_calls`, `/api/usage`, Prometheus) with the model that actually answered.

## Evidence checks (`analysis/grounding.py`)

These checks verify source matches, not entailment or scientific correctness. Inferred gaps and novelty suggestions
are hypotheses to check, not validated conclusions.

1. "Stated by the authors" items need a quote found in the paper (normalised exact match; corrected block reference;
   ≥ 0.88 fuzzy similarity shown as "≈ verbatim"); otherwise they are dropped and counted (`dropped_unverified`).
2. A phrase detector finds sentences such as "left for future work", "remains an open question" that no model call
   reported; they are added verbatim, labelled "detected" (recall backstop).
3. Block / equation / section ids that do not exist are removed; key equations are the paper's LaTeX by id.
4. Citation keys must exist in the paper's bibliography; models may never introduce outside literature.
5. Numbers in results that do not occur in the paper are flagged in the UI ("unverified").
6. AI-inferred gaps are visually and textually separate ("AI-inferred · not author claims"), state their reasoning,
   their grounding (verified ids/quotes or the author statement they build on), confidence and a novelty check; an
   item whose grounding fails verification is marked `ungrounded`.
7. Paper text is data: it is only ever placed in prompt data positions; CLI agents run with all tools disabled.

## Relevance

`config/profile.toml` (editable in Settings; DB copy wins): reader description, weighted interests with keywords
(whole-word, `/regex/`), followed authors (+1.5), category boosts. Keyword score = `10·(1 − Π(1 − wᵢ·fᵢ))`,
`fᵢ = 0.92·(1 − e^{−hits/7})`, title hits ×3. After triage: `0.4·keyword + 0.6·model + boosts`. The paper view shows
every ingredient ("why it's ranked here").

## HTTP API (`/api`)

| Method | Path | |
|---|---|---|
| GET | `/health`, `/metrics` | status; Prometheus text (papers, jobs, model calls/tokens/time, harvest age, workers) |
| GET | `/calendar?month=YYYY-MM` | per-day new / revisions / relevant / digested / brief |
| GET | `/days/{day}` | ranked paper cards + brief |
| GET | `/papers?q=&topic=&start=&end=&min_relevance=&starred=&sort=` | FTS search / listing |
| GET | `/papers/{id}` · `/papers/{id}/content[?version]` | detail with all analyses, provenance, jobs · parsed text |
| POST | `/papers` `{id}` | add a paper by id (arXiv API) |
| POST | `/papers/{id}/actions` `{action: star\|unstar\|hide\|unhide\|read\|unread\|note}` | |
| POST | `/papers/{id}/process` `{what: digest\|gaps\|refetch\|triage, model?}` | queue with priority |
| GET | `/gaps?start=&end=&topic=&source=all\|author\|ai&min_relevance=` | research-gap feed |
| POST | `/briefs/{day}` | (re)write the brief |
| GET/POST/DELETE | `/jobs`, `/jobs/{id}/retry`, `/jobs/retry-failed`, `/jobs/{id}` | queue |
| GET/POST | `/harvests`, `/harvest`, `/backfill {start,end}`, `/sweep` | ingestion control |
| GET/PUT | `/models`, `/routing`, `/profile`, `/settings`, `/usage`, `/workers`, `/topics` | |
