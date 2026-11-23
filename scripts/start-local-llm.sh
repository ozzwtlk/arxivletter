#!/usr/bin/env bash
# Launch the local Qwen3.6-35B-A3B model (same as AXIOM: one server can serve both apps) via llama.cpp (Vulkan) as an OpenAI-compatible server.
# Override with env vars: ARXIVLETTER_LLAMA_DIR, ARXIVLETTER_MODEL, ARXIVLETTER_LLM_PORT, ARXIVLETTER_LLM_CTX, ARXIVLETTER_LLM_PARALLEL
set -euo pipefail
LLAMA_DIR="${ARXIVLETTER_LLAMA_DIR:-$HOME/.local/opt/llama.cpp-b12490/llama-b12490}"
MODEL="${ARXIVLETTER_MODEL:-$HOME/.local/share/llm/models/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf}"
PORT="${ARXIVLETTER_LLM_PORT:-8081}"
CTX="${ARXIVLETTER_LLM_CTX:-32768}"
PAR="${ARXIVLETTER_LLM_PARALLEL:-2}"
MMPROJ="${ARXIVLETTER_MMPROJ:-$HOME/.local/share/llm/models/mmproj-Qwen3.6-35B-A3B-F16.gguf}"
# --check: is a local model installed on this machine? (used by `./axiom worker`; a laptop without one skips it)
if [[ "${1:-}" == "--check" ]]; then
  [[ -x "$LLAMA_DIR/llama-server" && -f "$MODEL" ]]
  exit $?
fi
EXTRA=()
[ -f "$MMPROJ" ] && EXTRA+=(--mmproj "$MMPROJ")
exec "$LLAMA_DIR/llama-server" -m "$MODEL" --host 127.0.0.1 --port "$PORT" \
  -c "$CTX" -np "$PAR" --fit on -fa on --jinja --alias qwen3.6-35b-a3b-q4kxl "${EXTRA[@]}" "$@"
