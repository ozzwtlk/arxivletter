# Deployment

Same split as AXIOM: an always-on **server** stores everything, harvests arXiv and serves the reader; **computing
machines** (your main PC with the local Qwen model, optionally a laptop with Claude Code / Codex) connect out to it
and run the model jobs.

```
 browser (phone/laptop) ──►  server: Raspberry Pi / NAS (docker)        ◄── main PC: ./arxivletter worker
                             harvest (OAI-PMH) · fetch + parse sources       local Qwen (llama.cpp :8081): triage, digests
                             SQLite + sources · scheduler · API + UI          Claude Code / Codex: gaps, daily brief
                             computes no model                            ◄── laptop (optional): premium jobs only
```

The server never needs your PC to be on: harvesting and full texts stay current; digests queue up and are processed
when the PC connects (most relevant first). Nothing is stored on the PC except the model cache directory.

## 0. Single machine (simplest)

```bash
./arxivletter            # builds the UI if needed, starts/reuses the local model on :8081, serves http://127.0.0.1:8010
./arxivletter status | logs | stop
```

`./arxivletter` reuses a model server that is already running on the port (e.g. AXIOM's) and never stops what it
did not start. Both apps share the same llama.cpp slots; requests simply queue.

## 1. Server

### Option A — build on the PC, run on the Pi (recommended)

One-time on the PC: `sudo pacman -S docker-buildx qemu-user-static qemu-user-static-binfmt`, and an ssh key for the Pi
(the Pi user must be in the `docker` group).

```bash
./arxivletter ship-server pi@<pi-address>     # builds the arm64 image, docker-loads it on the Pi, copies the compose file
```

On the Pi, once: `cd ~/arxivletter && cp .env.example .env && nano .env` — set `ARXIVLETTER_TOKEN` (`./arxivletter
new-token` on the PC prints one) and `ARXIVLETTER_CONTACT_EMAIL`. Prepare the [data directory](#prepare-the-data-directory)
before `docker compose up -d`. For updates, rerun `./arxivletter ship-server pi@<pi-address>` from your PC.

### Option B — build where it runs

```bash
cp .env.example .env            # Set ARXIVLETTER_TOKEN and ARXIVLETTER_CONTACT_EMAIL
sudo install -d -m 0750 -o 1000 -g 1000 ./server-data
docker compose up -d --build    # http://<server>:8282
```

### Prepare the data directory

The container runs as UID/GID `1000:1000`. Create its dedicated host directory before starting Compose:

```bash
sudo install -d -m 0750 -o 1000 -g 1000 ./server-data
```

Use the configured host path instead if `ARXIVLETTER_DATA` is set to something else. Docker may create a missing
bind mount as root, which causes `sqlite3.OperationalError: unable to open database file`. If the default directory
already exists, repair ownership without deleting any database files:

```bash
docker compose stop arxivletter
sudo chown -R 1000:1000 ./server-data  # Only on arXivletter's dedicated data directory
docker compose up -d
curl -fsS http://localhost:8282/api/health
```

Data lives in `./server-data` (`ARXIVLETTER_DATA`): the SQLite database (~1–2 MB per 100 papers incl. analyses) and
stored source text (~50 kB per paper version; `ARXIVLETTER_KEEP_SOURCE_TEXT=0` turns it off). Use an SSD on a Pi.
Back up the database with `sqlite3 server-data/arxivletter.db ".backup backup.db"` (safe while running).
Also back up `server-data/papers/` for retained source files and securely copy your deployment configuration.
For a coordinated snapshot or restore, stop the server and workers first. Restore the database and `papers/`
to the data directory, ensure ownership matches the service, then restart and check health and a paper's source.

Automatic digests require a relevance score of at least 5/10 by default. A paper's **Request full digest**
button bypasses the cutoff. For existing deployments, set `ARXIVLETTER_DIGEST_MIN_RELEVANCE=5` in the server's
`.env` if it still specifies `0`. Keep the cutoff consistent on the server and workers.

The server harvests immediately on start and then every `ARXIVLETTER_HARVEST_EVERY_MIN` minutes. Backfill a range in
*Operations → Backfill* or `curl -X POST <server>/api/backfill -H 'content-type: application/json' -d
'{"start":"2026-11-01","end":"2026-11-20"}'`.

## Move an existing PC library to the server

`ship-server` copies the image and Compose configuration only. To reuse the PC's papers, versions, analyses,
reading state, queue and model-call cache, migrate the database **and** retained source files. This replaces the
server library rather than merging it. Stop the PC app first if you want to capture its most recent completed work
and prevent changes to retained source files during the copy.

On the PC, from the repository root:

```bash
umask 077
stage=$(mktemp -d)
sqlite3 data/arxivletter.db ".backup '$stage/arxivletter.db'"
sqlite3 -readonly "$stage/arxivletter.db" 'PRAGMA quick_check;'
tar -czf "$stage/arxivletter-pc-data.tar.gz" -C "$stage" arxivletter.db -C "$PWD/data" papers
scp "$stage/arxivletter-pc-data.tar.gz" pi@<pi-address>:~/
```

On the server, from the Compose directory (substitute your dedicated host path if `ARXIVLETTER_DATA` is customized):

```bash
docker compose stop arxivletter
sudo mv ./server-data "./server-data.before-import-$(date +%Y%m%d-%H%M%S)"
sudo install -d -m 0750 -o 1000 -g 1000 ./server-data
sudo tar --no-same-owner -xzf ~/arxivletter-pc-data.tar.gz -C ./server-data
sudo chown -R 1000:1000 ./server-data
docker compose up -d
curl -fsS http://localhost:8282/api/health
```

The `mv` is for an existing server data directory; skip it on a fresh install. Keep that backup until you've verified
an imported digest. Do not copy a live SQLite main file by itself or unpack it over an existing WAL. Jobs still
running at snapshot time can be reclaimed after lease expiry. Run the PC in worker mode after migration so new
results go to the server library.

## 2. Main PC (local AI)

```bash
scripts/start-local-llm.sh                                 # or let ./arxivletter start it; or reuse AXIOM's
./arxivletter setup-worker http://<pi-address>:8282 main-pc   # asks for the token, writes .arxivletter-worker.env
./arxivletter                                              # = ./arxivletter worker once set up
```

The worker reports its models (local model reachable? `claude` / `codex` signed in?) every minute and only claims jobs
whose model it has. Triage and digests need the local model (or whatever you route them to in Settings); gaps and
briefs use the best premium model (`ARXIVLETTER_PREMIUM_ORDER`) and fall back to the local model.

* To run two local jobs alongside up to ten premium calls, set `ARXIVLETTER_LLM_WORKERS=2`,
  `ARXIVLETTER_LOCAL_CONCURRENCY=2`, `ARXIVLETTER_PREMIUM_WORKERS=10`, and `ARXIVLETTER_PREMIUM_CONCURRENCY=10`
  in `.arxivletter-worker.env` and restart the worker. Choose the premium provider in Settings. These are
  per-machine limits, not guarantees: queue depth, provider limits, and daily budgets still apply.
  With extraction and digest routing set to Local, set `ARXIVLETTER_CODEX_DIGEST_DAILY_CEILING=40` to let
  idle premium slots process unassigned digests via Codex. Premium slots claim gaps and briefs first. The
  ceiling counts all Codex calls (including in-flight calls and retries) per UTC day, not papers; dedicated
  premium jobs can still use the normal daily limit after digest overflow stops.
* llama.cpp slot size matters: with `-c 32768 -np 2` each slot has 16k tokens; set `ARXIVLETTER_LOCAL_CTX` to the
  per-slot context if you change it (chunk sizes follow).
* Other local servers (vLLM, Ollama `/v1`): `ARXIVLETTER_LOCAL_LLM_URL`, `ARXIVLETTER_LOCAL_MODEL`,
  `ARXIVLETTER_LOCAL_LLAMACPP=0` (uses `response_format: json_schema` instead of the GBNF grammar).
* A containerised worker (local model only — the subscription CLIs need the native worker):
  `docker compose --profile worker up -d worker` with `ARXIVLETTER_WORKER_SERVER_URL=http://<pi>:8282` in `.env`.
* Stopping the worker is always safe: running jobs go back to the queue (on shutdown, or when the 15-minute lease
  expires) and resume; every model call is cached.

## 3. Laptop (optional, premium only)

Same `setup-worker`; set `ARXIVLETTER_WORKER_LOCAL_MODEL=off` in `.arxivletter-worker.env`. Sign in once with `claude`
and/or `codex login`. It will take gap analyses and briefs.

## Observability

* **Operations** page: server health, connected machines and what they run, queue by kind/status, failed jobs with
  errors and retry, harvest history, model usage today.
* `GET /api/metrics` (Prometheus): `arxivletter_jobs{kind,status}`, `arxivletter_papers{status}`,
  `arxivletter_llm_calls_total|prompt_tokens|completion_tokens|seconds{model,class,cached,ok}` (24 h),
  `arxivletter_harvest_age_seconds`, `arxivletter_workers_online`. Example scrape config and alert ideas:
  `deploy/monitoring/prometheus.yml`.
* Logs: `docker logs arxivletter`; workers `data/logs/worker.log`. `ARXIVLETTER_LOG_FORMAT=json` for log shippers.
* Docker healthcheck on `/api/health`; compose rotates logs (5 × 10 MB).

## Security

* `/internal/*` (remote SQL, heartbeat) requires `ARXIVLETTER_TOKEN`; keep it secret.
* The reader and `/api` (including write operations) have no login. Restrict server and metrics access to
  trusted clients via firewall or a private network such as WireGuard/Tailscale. Do not expose the port publicly;
  use HTTPS or an encrypted private network for remote workers so their token is not sent over plaintext on untrusted links.
* Paper text is untrusted data: models only ever see it in data positions; Claude Code / Codex run with all tools
  disabled in an empty temporary directory, without MCP servers, settings, hooks or API keys.
