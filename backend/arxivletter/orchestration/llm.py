"""Model calls: caching, metering, budgets and evidence-based escalation (adapted from AXIOM).

Every model call in arXivletter goes through `call()` / `call_json()`:
  * cache key = sha256(model, prompt_version, task, system, user text, image hashes, schema, thinking)
  * per-class concurrency limits (local GPU slots, premium rate limits)
  * daily caps for subscription CLIs and paid APIs (ARXIVLETTER_SUBSCRIPTION_DAILY_LIMIT)
  * every call (hit or miss) is metered in `llm_calls`
Escalation is triggered by *evidence* (schema/validation failure after retries), not by model
self-reported confidence.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable

from arxivletter.config import settings
from arxivletter.model_adapters.base import AdapterUnavailable, ChatAdapter, ChatRequest, ChatResult
from arxivletter.orchestration import models  # noqa: F401  (type hints)
from arxivletter.model_adapters.openai_compat import OpenAICompatAdapter
from arxivletter.storage import db

log = logging.getLogger("arxivletter.llm")

_adapters: dict[str, ChatAdapter] = {}
_sems: dict[str, threading.Semaphore] = {}
_lock = threading.Lock()
job_checkpoint: ContextVar[Callable[[], None] | None] = ContextVar("job_checkpoint", default=None)
call_ceiling: ContextVar[int | None] = ContextVar("call_ceiling", default=None)


def check_job() -> None:
    checkpoint = job_checkpoint.get()
    if checkpoint:
        checkpoint()


class BudgetExceeded(RuntimeError):
    pass


class ValidationFailed(RuntimeError):
    pass


def premium_available() -> bool:
    """True when any premium model (subscription agent or API key) can be used."""
    from arxivletter.orchestration import models
    return models.best_premium() is not None


def _canon(model: str) -> str:
    """Model id for an adapter: the local classes share one adapter; "premium" means the best available one."""
    if model in ("local", "local_text", "local_vlm"):
        return "local"
    if model == "premium":
        from arxivletter.orchestration import models
        best = models.best_premium()
        if not best:
            raise BudgetExceeded("premium model requested but none is available (no subscription CLI or API key)")
        return best
    return model


def _metering_class(model: str, requested: str) -> str:
    """`llm_calls.model_class`: local_text / local_vlm for the local model, premium for everything else."""
    return requested if requested in ("local_text", "local_vlm") else ("local_text" if model == "local" else "premium")


def model_name(model: str) -> str:
    """The name recorded in llm_calls / cache keys for a model id."""
    return get_adapter(model).name


def get_adapter(model: str) -> ChatAdapter:
    mid = _canon(model)
    with _lock:
        if mid in _adapters:
            return _adapters[mid]
        if mid == "local":
            ad: ChatAdapter = OpenAICompatAdapter(settings.local_llm_url, settings.local_model_name, "local_text",
                                                  timeout=settings.llm_timeout_s,
                                                  local_llamacpp=settings.local_is_llamacpp,
                                                  api_key=os.environ.get("ARXIVLETTER_LOCAL_API_KEY") or None)
            _sems["local"] = _sems.get("local") or threading.Semaphore(settings.local_concurrency)
        elif mid.startswith("claude-code:"):
            from arxivletter.model_adapters.cli_agents import ClaudeCodeAdapter
            ad = ClaudeCodeAdapter(mid.split(":", 1)[1], timeout=settings.llm_timeout_s)
            _sems[mid] = threading.Semaphore(settings.premium_concurrency)
        elif mid.startswith("codex:"):
            from arxivletter.model_adapters.cli_agents import CodexAdapter
            m = mid.split(":", 1)[1]
            ad = CodexAdapter(None if m == "default" else m, timeout=settings.llm_timeout_s)
            _sems[mid] = threading.Semaphore(settings.premium_concurrency)
        elif mid == "api:anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
            from arxivletter.model_adapters.anthropic import AnthropicAdapter
            ad = AnthropicAdapter(os.environ["ANTHROPIC_API_KEY"], settings.anthropic_model)
            _sems[mid] = threading.Semaphore(settings.premium_concurrency)
        elif mid == "api:openai" and os.environ.get("OPENAI_API_KEY"):
            ad = OpenAICompatAdapter("https://api.openai.com/v1", settings.openai_model,
                                     "premium", api_key=os.environ["OPENAI_API_KEY"], local_llamacpp=False)
            _sems[mid] = threading.Semaphore(settings.premium_concurrency)
        else:
            raise BudgetExceeded(f"model {model!r} is not available")
        _adapters[mid] = ad
        return ad


def _sem(model: str) -> threading.Semaphore:
    mid = _canon(model)
    return _sems["local"] if mid == "local" else _sems[mid]


def _cache_key(model: str, task: str, prompt_version: str, req: ChatRequest, decoding: str = "") -> str:
    h = hashlib.sha256()
    for part in (model, task, prompt_version, req.system or "", req.user_text,
                 json.dumps(req.json_schema, sort_keys=True) if req.json_schema else "", str(req.thinking),
                 str(req.max_tokens)) + ((decoding,) if decoding and req.json_schema else ()):
        h.update(part.encode())
        h.update(b"\x00")
    for im in req.images:
        h.update(hashlib.sha256(im.png).digest())
    return h.hexdigest()


def _record(paper_id: str | None, task: str, model: str, mclass: str, cached: bool, ok: bool, res: ChatResult | None):
    with db.connect() as c:
        c.execute(
            "INSERT INTO llm_calls(paper_id,task,model,model_class,cached,ok,seconds,prompt_tokens,completion_tokens,ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (paper_id, task, model, mclass, int(cached), int(ok), res.seconds if (res and not cached) else 0.0,
             res.prompt_tokens if res else None, res.completion_tokens if res else None,
             datetime.now(timezone.utc).isoformat()))


def _reserve_call(paper_id: str | None, task: str, model: str, mclass: str, limit: int) -> int:
    """Count attempts at dispatch, atomically across workers; concurrent calls cannot overspend the ceiling."""
    now = datetime.now(timezone.utc)
    with db.connect(immediate=True) as c:
        used = c.execute("SELECT COUNT(*) n FROM llm_calls WHERE model=? AND cached=0 AND ts>=?",
                         (model, now.strftime("%Y-%m-%dT00:00:00"))).fetchone()["n"]
        if used >= limit:
            raise BudgetExceeded(f"daily limit for {model} reached ({limit} calls)")
        return c.execute(
            "INSERT INTO llm_calls(paper_id,task,model,model_class,cached,ok,seconds,ts) VALUES (?,?,?,?,0,0,0,?)",
            (paper_id, task, model, mclass, now.isoformat()),
        ).lastrowid


def call(task: str, model: "str | models.Route", req: ChatRequest, prompt_version: str, *,
         paper_id: str | None = None, use_cache: bool = True) -> tuple[ChatResult, bool]:
    """Returns (result, cached). `model` is a model id ("local_text", "claude-code:sonnet", ...) or a Route;
    a Route falls back to its fallback model when the first one is unavailable, over its cap, or errors."""
    from arxivletter.orchestration import models
    if isinstance(model, models.Route):
        try:
            return call(task, model.model, req, prompt_version, paper_id=paper_id, use_cache=use_cache)
        except (AdapterUnavailable, BudgetExceeded) as e:
            fb = model.fallback
            # never fall back to a model this machine cannot reach (a laptop has no Qwen): its "connection
            # refused" would replace the real error of the model that was actually chosen
            if not fb or not models.available("local" if models.kind(fb) == "local" else fb):
                raise
            log.warning("%s: %s unavailable (%s); falling back to %s", task, model.model, e, model.fallback)
            return call(task, fb, req, prompt_version, paper_id=paper_id, use_cache=use_cache)
    check_job()
    mid = _canon(model)
    mclass = _metering_class(mid, model)
    ad = get_adapter(mid)
    if req.images and not ad.supports_vision:
        raise AdapterUnavailable(f"{ad.name} cannot read images")
    key = _cache_key(ad.name, task, prompt_version, req, getattr(ad, "decoding", ""))
    if use_cache:
        row = db.q1("SELECT response FROM llm_cache WHERE key=?", (key,))
        if row:
            d = json.loads(row["response"])
            res = ChatResult(d["text"], d["model"], d.get("pt"), d.get("ct"), d.get("s", 0.0), d.get("fr"), mid)
            _record(paper_id, task, ad.name, mclass, True, True, res)
            return res, True
    with _sem(mid):
        check_job()
        reserved = None
        if models.kind(mid) in ("api", "subscription"):
            limit = models.daily_limit(task)
            ceiling = call_ceiling.get()
            reserved = _reserve_call(paper_id, task, ad.name, mclass, limit if ceiling is None else min(limit, ceiling))
        try:
            res = ad.chat(req)
        except Exception:
            if reserved is None:
                _record(paper_id, task, ad.name, mclass, False, False, None)
            raise
    res.model_id = mid
    if res.finish_reason == "length":
        log.warning("%s: output truncated (max_tokens=%s)", task, req.max_tokens)
    if reserved is None:
        _record(paper_id, task, ad.name, mclass, False, True, res)
    else:
        with db.connect() as c:
            c.execute("UPDATE llm_calls SET ok=1, seconds=?, prompt_tokens=?, completion_tokens=? WHERE id=?",
                      (res.seconds, res.prompt_tokens, res.completion_tokens, reserved))
    if use_cache and res.finish_reason != "length":
        with db.connect() as c:
            c.execute("INSERT OR REPLACE INTO llm_cache(key,task,model,prompt_version,response,created_at) VALUES (?,?,?,?,?,?)",
                      (key, task, res.model, prompt_version,
                       db.jdump({"text": res.text, "model": res.model, "pt": res.prompt_tokens,
                                 "ct": res.completion_tokens, "s": res.seconds, "fr": res.finish_reason}),
                       datetime.now(timezone.utc).isoformat()))
    check_job()
    return res, False


def _invalidate(task: str, model: str, req: ChatRequest, prompt_version: str):
    ad = get_adapter(model)
    with db.connect() as c:
        c.execute("DELETE FROM llm_cache WHERE key=?", (_cache_key(ad.name, task, prompt_version, req,
                                                                getattr(ad, "decoding", "")),))


# LaTeX commands whose leading letter collides with a JSON escape (\t \b \f \n \r). Models often emit
# "\frac" inside a JSON string with a single backslash, which JSON decodes to FORMFEED + "rac".
_LATEX_JSON_COLLISIONS = re.compile(
    # \n and \t + letters may be a real newline / tab-indented code ("\nThe", "\tconst"), so only known
    # commands are repaired; BS/FF/CR directly before a letter never occurs, so \b \f \r + letters is always
    # LaTeX (\rightarrow, \frak, \bigl, ...)
    r"(?<!\\)\\("
    r"n(?:abla|u|eq|e|ot|otin|i|eg|leq|geq|ewline|olimits|onumber|parallel|subseteq|warrow|earrow|atural)(?![A-Za-z])"
    r"|t(?:ext|extbf|extit|extrm|extsf|exttt|extstyle|au|an|anh|heta|imes|ilde|o|op|riangle|frac|ensor|ag"
    r"|hinspace|riangleq)(?![A-Za-z])"
    r"|[bfr][A-Za-z]+"
    r")"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0d\x0e-\x1f]")


def repair_latex_escapes(raw: str) -> str:
    raw = _LATEX_JSON_COLLISIONS.sub(lambda m: "\\\\" + m.group(1), raw)
    # "\u" not followed by 4 hex digits is LaTeX (\upsilon, \underline, \uparrow), not a JSON unicode escape
    raw = re.sub(r'(?<!\\)((?:\\\\)*)\\u(?![0-9a-fA-F]{4})', lambda m: m.group(1) + "\\\\u", raw)
    # any other backslash that is not a valid JSON escape (e.g. "\mu") is a literal LaTeX backslash
    return re.sub(r'(?<!\\)((?:\\\\)*)\\(?=[^"\\/bfnrtu])', lambda m: m.group(1) + "\\\\", raw)


def _check_no_control(o: Any) -> None:
    if isinstance(o, str):
        if _CONTROL.search(o):
            raise ValidationFailed("string contains a control character (probably a broken LaTeX backslash escape "
                                   "such as \\t in \\text or \\f in \\frac); write backslashes as \\\\ in JSON")
    elif isinstance(o, dict):
        for v in o.values():
            _check_no_control(v)
    elif isinstance(o, list):
        for v in o:
            _check_no_control(v)


def parse_json(text: str) -> Any:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    data = json.loads(repair_latex_escapes(t))
    _check_no_control(data)
    return data


def call_json(task: str, model: "str | models.Route", req: ChatRequest, prompt_version: str,
              validate: Callable[[Any], Any], *, paper_id: str | None = None, attempts: int = 2,
              escalate: bool = False) -> tuple[Any, dict]:
    """Call, parse JSON, run `validate` (raises ValidationFailed / ValueError on bad output).
    Retries with the error message appended. A local first choice escalates to premium after repeated invalid
    output (evidence-based) when the profile allows it; a Route falls back to its fallback model when the first
    model is unavailable or over its cap. Returns (validated_value, meta) with the model that actually answered."""
    from arxivletter.orchestration import models
    route = model if isinstance(model, models.Route) else models.Route(model, None, "fixed")
    chain: list[str] = [route.model] * attempts
    if escalate and _canon(route.model) == "local" and models.load_routing()["cloud_enabled"]:
        best = models.best_premium()
        if best:
            chain.append(best)
    errors: list[str] = []
    fell_back = False
    i = 0
    while i < len(chain):
        mid = chain[i]
        r = req
        if errors:
            r = ChatRequest(req.system, req.user_text + "\n\nYour previous answer was rejected by the validator: "
                            + errors[-1][:800] + "\nReturn a corrected answer.", req.images, req.json_schema,
                            req.max_tokens, 0.2 if _canon(mid) == "local" else req.temperature, req.thinking)
        try:
            res, cached = call(task, mid, r, prompt_version, paper_id=paper_id)
        except (BudgetExceeded, AdapterUnavailable) as e:
            if isinstance(e, BudgetExceeded) and call_ceiling.get() is not None:
                raise  # Return an interrupted overflow digest to the normal queue.
            errors.append(f"{type(e).__name__}: {e}")
            fb_ok = bool(route.fallback) and models.available("local" if models.kind(route.fallback) == "local"
                                                               else route.fallback)  # see call(): no unreachable fallback
            if fb_ok and not fell_back and _canon(mid) != _canon(route.fallback):
                log.warning("%s: %s unavailable (%s); falling back to %s", task, mid, e, route.fallback)
                fell_back = True
                chain = chain[:i] + [route.fallback] * attempts
                continue
            if isinstance(e, BudgetExceeded) or i == len(chain) - 1:
                break
            i += 1
            continue
        try:
            value = validate(parse_json(res.text))
            return value, {"model": res.model, "model_id": _canon(mid), "model_class": _metering_class(_canon(mid), mid),
                           "attempt": i + 1, "cached": cached, "fallback": fell_back,
                           "escalated": _canon(mid) != _canon(route.model) and not fell_back, "errors": errors}
        except (ValueError, ValidationFailed, KeyError, TypeError) as e:
            errors.append(f"{type(e).__name__}: {e}")
            _invalidate(task, mid, r, prompt_version)  # never keep a rejected answer in cache
        i += 1
    raise ValidationFailed(f"{task}: all attempts failed: {errors}")
