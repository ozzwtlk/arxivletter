"""Which model does which job (adapted from AXIOM's orchestration/models.py).

Model ids
  local                  the local model (llama.cpp / any OpenAI-compatible server) — default for token-heavy work
  claude-code:<alias>    Claude Code CLI on the user's Claude subscription (opus, sonnet, haiku)
  codex:<model|default>  Codex CLI on the user's ChatGPT subscription
  api:anthropic          Anthropic API (ANTHROPIC_API_KEY; billed per token)
  api:openai             OpenAI API (OPENAI_API_KEY; billed per token)

Task groups ("auto" policy)
  bulk (local):    triage (title+abstract), extract (map over full-text chunks), digest (reduce → paper digest)
  premium:         gaps (AI-inferred research gaps / ideas), brief (daily cross-paper synthesis)
Premium groups fall back to the local model when no premium model is available or its daily cap is reached; the
output then records which model actually answered, and the UI labels it.
Users override any group in Settings (meta key routing.v1) or per request ("Analyze with …").
"""

from __future__ import annotations

import contextvars
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from arxivletter.config import settings
from arxivletter.storage import db

ROUTING_KEY = "routing.v1"
run_model: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_model", default=None)


@dataclass(frozen=True)
class Group:
    id: str
    label: str
    tasks: tuple[str, ...]
    heavy: bool
    help: str = ""


GROUPS: tuple[Group, ...] = (
    Group("triage", "Triage (relevance, topics, one-liner)", ("triage",), heavy=True,
          help="Every paper: title + abstract only. ~1 short call per paper."),
    Group("extract", "Full-text extraction (per chunk)", ("extract", "extract_light"), heavy=True,
          help="Token-heavy: reads the full text in chunks; claims, equations, author-stated open questions."),
    Group("digest", "Paper digests", ("digest",), heavy=True,
          help="Combines the extracted notes into the researcher-level digest."),
    Group("gaps", "Research gaps & ideas (premium)", ("gaps",), heavy=False,
          help="Implicit gaps, research opportunities and novel ideas. Selective: relevant papers only."),
    Group("brief", "Daily synthesis (premium)", ("brief",), heavy=False,
          help="One call per day across the most relevant digests."),
)
GROUP_BY_ID = {g.id: g for g in GROUPS}
TASK_GROUP = {t: g.id for g in GROUPS for t in g.tasks}


def _claude_models() -> list[str]:
    return [m.strip() for m in os.environ.get("ARXIVLETTER_CLAUDE_MODELS", "opus,sonnet,haiku").split(",") if m.strip()]


def _codex_models() -> list[str]:
    return [m.strip() for m in os.environ.get("ARXIVLETTER_CODEX_MODELS", "default").split(",") if m.strip()]


def daily_limit(task: str | None = None) -> int:
    return settings.subscription_daily_limit


def catalog() -> list[dict]:
    out = [{"id": "local", "label": f"Local model ({settings.local_model_name})", "kind": "local"}]
    names = {"sonnet": "Sonnet", "opus": "Opus", "haiku": "Haiku"}
    out += [{"id": f"claude-code:{m}", "label": f"Claude Code · {names.get(m, m)}", "kind": "subscription"}
            for m in _claude_models()]
    out += [{"id": f"codex:{m}", "label": "Codex" + ("" if m == "default" else f" · {m}"), "kind": "subscription"}
            for m in _codex_models()]
    if os.environ.get("ANTHROPIC_API_KEY"):
        out.append({"id": "api:anthropic", "label": f"Anthropic API · {settings.anthropic_model}", "kind": "api"})
    if os.environ.get("OPENAI_API_KEY"):
        out.append({"id": "api:openai", "label": f"OpenAI API · {settings.openai_model}", "kind": "api"})
    return out


_avail_cache: dict[str, tuple[float, bool]] = {}
_avail_lock = threading.Lock()


def available(model_id: str) -> bool:
    """Cheap availability check (binary installed / key present / local server up), cached for 60 s."""
    if model_id in ("local_text",):
        model_id = "local"
    now = time.monotonic()
    with _avail_lock:
        hit = _avail_cache.get(model_id)
        if hit and now - hit[0] < 60:
            return hit[1]
    if model_id == "local":
        from arxivletter.orchestration.llm import get_adapter
        ok = bool(getattr(get_adapter("local"), "health", lambda: True)())
    elif model_id.startswith("claude-code:"):
        ok = shutil.which("claude") is not None and model_id.split(":", 1)[1] in _claude_models()
    elif model_id.startswith("codex:"):
        ok = shutil.which("codex") is not None and model_id.split(":", 1)[1] in _codex_models()
    elif model_id == "api:anthropic":
        ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
    elif model_id == "api:openai":
        ok = bool(os.environ.get("OPENAI_API_KEY"))
    else:
        ok = False
    with _avail_lock:
        _avail_cache[model_id] = (now, ok)
    return ok


def kind(model_id: str) -> str:
    if model_id in ("local", "local_text"):
        return "local"
    return "api" if model_id.startswith("api:") else "subscription"


def calls_today(model_id: str) -> int:
    from arxivletter.orchestration.llm import model_name
    start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    row = db.q1("SELECT COUNT(*) n FROM llm_calls WHERE model=? AND cached=0 AND ts>=?", (model_name(model_id), start))
    return int(row["n"]) if row else 0


def load_routing() -> dict:
    row = db.q1("SELECT value FROM meta WHERE key=?", (ROUTING_KEY,))
    d = json.loads(row["value"]) if row and row["value"] else {}
    return {"groups": {g.id: d.get("groups", {}).get(g.id, "auto") for g in GROUPS},
            "premium_default": d.get("premium_default", "auto"),
            # arXiv papers are public, so premium use is on by default (unlike AXIOM's private books)
            "cloud_enabled": bool(d.get("cloud_enabled", settings.cloud_enabled_default))}


def save_routing(groups: dict[str, str] | None = None, premium_default: str | None = None,
                 cloud_enabled: bool | None = None) -> dict:
    cur = load_routing()
    ids = {m["id"] for m in catalog()} | {"auto"}
    for gid, mid in (groups or {}).items():
        if gid not in GROUP_BY_ID:
            raise ValueError(f"unknown task group {gid!r}")
        if mid not in ids:
            raise ValueError(f"unknown model {mid!r}")
        cur["groups"][gid] = mid
    if premium_default is not None:
        if premium_default not in ids or premium_default == "local":
            raise ValueError(f"unknown premium model {premium_default!r}")
        cur["premium_default"] = premium_default
    if cloud_enabled is not None:
        cur["cloud_enabled"] = bool(cloud_enabled)
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (ROUTING_KEY, json.dumps(cur)))
    return cur


@dataclass(frozen=True)
class Route:
    model: str
    fallback: str | None
    reason: str


def best_premium(exclude: tuple[str, ...] = ()) -> str | None:
    pref = load_routing()["premium_default"]
    order = ((pref,) if pref != "auto" else ()) + tuple(settings.premium_order)
    ids = {m["id"] for m in catalog()}
    for mid in order:
        if mid not in exclude and mid in ids and available(mid) and (
                kind(mid) != "subscription" or calls_today(mid) < daily_limit()):
            return mid
    return None


def resolve(group: str, override: str | None = None) -> Route:
    g = GROUP_BY_ID[group]
    if override and override != "auto":
        return Route(override, None, "chosen for this request")
    run = run_model.get()
    if run and run != "auto":
        return Route(run, None if run == "local" else "local", "chosen for this job")
    choice = load_routing()["groups"].get(group, "auto")
    if choice != "auto":
        return Route(choice, None if choice == "local" else "local", "set in Settings")
    if g.heavy:
        return Route("local", None, "auto: token-heavy task → local")
    if load_routing()["cloud_enabled"]:
        p = best_premium()
        if p:
            return Route(p, "local", "auto: reasoning task → premium")
        return Route("local", None, "auto: no premium model available → local")
    return Route("local", None, "auto: premium models are off (Settings)")


def report() -> dict:
    from arxivletter.model_adapters.cli_agents import ClaudeCodeAdapter, CodexAdapter
    status = {"claude-code": ClaudeCodeAdapter().login_status() if shutil.which("claude") else {},
              "codex": CodexAdapter().login_status() if shutil.which("codex") else {}}
    out = []
    for m in catalog():
        st = status.get(m["id"].split(":", 1)[0], {})
        usable = available(m["id"]) and st.get("logged_in", True)
        sub = m["kind"] == "subscription"
        out.append(dict(m, available=usable, detail=st.get("detail") or ("" if usable else "not reachable"),
                        calls_today=calls_today(m["id"]) if m["kind"] != "local" else None,
                        daily_limit=daily_limit() if sub else None))
    return {"models": out, "groups": [{"id": g.id, "label": g.label, "heavy": g.heavy, "help": g.help,
                                       "auto": resolve(g.id).model} for g in GROUPS]}
