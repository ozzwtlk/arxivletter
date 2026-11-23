# Validation status

## Live validation

- A two-category arXiv OAI-PMH test day yielded 175 deduplicated hep-ph/hep-th papers. All 175 sources were parsed: 174 LaTeX and one HTML fallback. KaTeX rendered 18,153 of 18,513 display equations (98.06%); the rest retain their raw TeX.
- The all-in-one instance at `http://127.0.0.1:8010` reached 327 fetched papers. Four full Qwen digests completed, including dark-matter papers `2611.25931` and `2611.25872`, strong-field paper `2611.25507`, and the Euler–Heisenberg revision `2601.21143v3`. Author quotes resolve to source blocks; a regression test distinguishes in-paper signposting from future work.
- Claude Code Opus completed real premium research-gap analyses. The November 19 daily brief completed using the local Qwen fallback after the premium attempt failed. Routing, actual model used, and failures are visible in Operations.
- An independent Docker server at `http://127.0.0.1:8282` harvested and fetched 112 hep-th papers. A native worker connected outbound and completed real Qwen triage; stopping it released its in-flight lease and marked it offline.

## Checks

- `cd backend && .venv/bin/ruff check arxivletter && .venv/bin/python -m pytest -q`: 27 passed.
- `cd frontend && pnpm test && pnpm build`: 6 passed; production build succeeded.
- From `frontend/`, `node e2e/smoke.mjs http://127.0.0.1:8010 /tmp/axl-shots`: 15 checks passed on the seeded live instance, including the source drawer and math reader. No raw fallback among the 375 formulas on the inspected paper.
- `bash -n arxivletter` and `docker compose config --quiet` passed; the Docker image was built and the split deployment exercised on the development host.

## Rollout limits

The remaining model queue was not drained. Sustained throughput depends on worker uptime and available llama.cpp slots; the live tests shared a two-slot model with Axiom. The Raspberry Pi rollout and multi-day unattended operation were not tested. PDF parsing is a low-math-fidelity fallback, and a source match does not validate the scientific interpretation or novelty of an AI-inferred gap. Run behind a trusted private network as described in [DEPLOY.md](DEPLOY.md).
