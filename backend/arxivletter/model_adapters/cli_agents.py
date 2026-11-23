# Adapted from AXIOM (../../Axiom/backend/axiom/model_adapters).
"""Subscription agents as model backends: Claude Code (`claude -p`) and Codex (`codex exec`).

These are the vendors' own CLIs, used through their documented non-interactive modes, signed in with the user's
own subscription (claude.ai / ChatGPT login). arXivletter never reads or copies their credentials and never calls private
endpoints. Subscription plans have usage limits, so arXivletter meters every call and enforces a daily cap
(`ARXIVLETTER_SUBSCRIPTION_DAILY_LIMIT`); bulk work (transcription, optimization) stays on the local model by default.

Both CLIs are *tool-using agents*, and the prompts contain text from an untrusted PDF. Every call therefore runs
  * with all tools disabled (Claude Code: `--tools ""`; Codex: shell/exec/browser/computer/apps/plugins/hooks
    features off, web search disabled, read-only sandbox),
  * in a fresh, empty temporary working directory that is deleted afterwards,
  * without the user's MCP servers, settings, hooks, skills or project instructions,
  * without session persistence,
  * with a minimal environment (API keys removed, so the CLI uses the subscription login, never API billing).
The model can only return text; that text is then parsed and validated like any other model output.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from arxivletter.model_adapters.base import AdapterUnavailable, ChatRequest, ChatResult

_ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "XDG_CONFIG_HOME",
             "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "CODEX_HOME",
             "CLAUDE_CONFIG_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
             "https_proxy", "http_proxy", "no_proxy")


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP}
    env["NO_COLOR"] = "1"
    return env


_active: set[subprocess.Popen] = set()
_active_lock = __import__("threading").Lock()


def kill_all() -> int:
    """Terminate running agent processes (on shutdown). They run in their own session, so without this a stopped
    server or worker would leave them running to completion — paying for answers nobody will read."""
    import signal
    with _active_lock:
        procs = list(_active)
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    return len(procs)


def _run(args: list[str], stdin: str, cwd: str, timeout: float) -> subprocess.CompletedProcess:
    try:
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             cwd=cwd, env=_env(), start_new_session=True)
    except FileNotFoundError as e:
        raise AdapterUnavailable(f"{args[0]} is not installed") from e
    with _active_lock:
        _active.add(p)
    try:
        out, err = p.communicate(stdin, timeout=timeout)
        return subprocess.CompletedProcess(args, p.returncode, out, err)
    except subprocess.TimeoutExpired as e:
        p.kill()
        p.communicate()
        raise AdapterUnavailable(f"{args[0]} timed out after {timeout:.0f}s") from e
    finally:
        with _active_lock:
            _active.discard(p)


def _strip_for_openai(schema):
    """OpenAI structured outputs reject some JSON-Schema keywords; our own validator still checks them."""
    if isinstance(schema, dict):
        return {k: _strip_for_openai(v) for k, v in schema.items()
                if k not in ("minItems", "maxItems", "minimum", "maximum", "minLength", "maxLength")}
    if isinstance(schema, list):
        return [_strip_for_openai(x) for x in schema]
    return schema


class ClaudeCodeAdapter:
    """`claude -p` with a model alias (sonnet, opus, haiku, fable) or a full model name. Images (scan crops) are
    sent inline in a stream-json user message, so no file access or tool is needed."""

    model_class = "premium"
    supports_vision = True
    kind = "subscription"

    def __init__(self, model: str = "sonnet", binary: str = "claude", timeout: float = 900):
        self.model = model
        self.binary = binary
        self.timeout = timeout
        self.name = f"claude-code:{model}"

    def installed(self) -> bool:
        return shutil.which(self.binary) is not None

    def login_status(self) -> dict:
        if not self.installed():
            return {"installed": False, "logged_in": False, "detail": "claude CLI not found"}
        try:
            r = subprocess.run([self.binary, "auth", "status"], capture_output=True, text=True, timeout=20, env=_env())
            d = json.loads(r.stdout or "{}")
            # only the non-identifying fields leave this function
            return {"installed": True, "logged_in": bool(d.get("loggedIn")),
                    "detail": f"signed in via {d.get('authMethod', '?')}" if d.get("loggedIn") else "not signed in — run `claude` once"}
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            return {"installed": True, "logged_in": False, "detail": "could not read login status"}

    def chat(self, req: ChatRequest) -> ChatResult:
        stream = bool(req.images)
        args = [self.binary, "-p", "--output-format", "stream-json" if stream else "json", "--tools", "", "--strict-mcp-config",
                "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence",
                "--model", self.model, "--effort", "high" if req.thinking else "low",
                "--system-prompt", req.system or "You are a careful theoretical physicist."]
        if req.json_schema is not None:
            args += ["--json-schema", json.dumps(req.json_schema)]
        stdin = req.user_text
        if stream:
            args += ["--input-format", "stream-json", "--verbose"]
            content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.b64encode(i.png).decode()}} for i in req.images]
            content.append({"type": "text", "text": req.user_text})
            stdin = json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="arxl-claude-") as cwd:
            r = _run(args, stdin, cwd, self.timeout)
        try:
            if stream:  # one JSON event per line; the last "result" event carries the answer
                d = next(json.loads(line) for line in reversed(r.stdout.splitlines())
                         if line.startswith("{") and '"type":"result"' in line.replace(" ", ""))
            else:
                d = json.loads(r.stdout)
        except (json.JSONDecodeError, StopIteration) as e:
            raise AdapterUnavailable(f"{self.name}: unexpected output ({(r.stderr or r.stdout)[-300:]})") from e
        if d.get("is_error") or d.get("subtype") != "success":
            raise AdapterUnavailable(f"{self.name}: {str(d.get('result') or d.get('subtype'))[:300]}")
        text = json.dumps(d["structured_output"]) if d.get("structured_output") is not None else d.get("result", "")
        u = d.get("usage") or {}
        prompt = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        return ChatResult(text, self.name, prompt or None, u.get("output_tokens"), time.monotonic() - t0, "stop")


class CodexAdapter:
    """`codex exec`, signed in with ChatGPT. `model=None` uses the CLI's default model."""

    model_class = "premium"
    supports_vision = False
    kind = "subscription"
    _DISABLE = ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "browser_use", "browser_use_external",
                "in_app_browser", "computer_use", "tool_suggest", "skill_mcp_dependency_install")

    def __init__(self, model: str | None = None, binary: str = "codex", timeout: float = 900):
        self.model = model
        self.binary = binary
        self.timeout = timeout
        self.name = f"codex:{model or 'default'}"

    def installed(self) -> bool:
        return shutil.which(self.binary) is not None

    def login_status(self) -> dict:
        if not self.installed():
            return {"installed": False, "logged_in": False, "detail": "codex CLI not found"}
        try:
            r = subprocess.run([self.binary, "login", "status"], capture_output=True, text=True, timeout=20, env=_env())
            out = (r.stdout + r.stderr).strip()
            ok = r.returncode == 0 and "logged in" in out.lower() and "not" not in out.lower().split("logged in")[0]
            return {"installed": True, "logged_in": ok, "detail": out.splitlines()[0][:80] if out else ""}
        except (subprocess.TimeoutExpired, OSError):
            return {"installed": True, "logged_in": False, "detail": "could not read login status"}

    def chat(self, req: ChatRequest) -> ChatResult:
        if req.images:
            raise AdapterUnavailable(f"{self.name} cannot read images here; transcription stays on the local model")
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="arxl-codex-") as cwd:
            out = Path(cwd) / ".last.txt"
            args = [self.binary, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                    "--skip-git-repo-check", "--sandbox", "read-only", "-C", cwd, "--color", "never",
                    "-c", "web_search=disabled",
                    "-c", f'model_reasoning_effort="{"high" if req.thinking else "low"}"', "-o", str(out)]
            for f in self._DISABLE:
                args += ["--disable", f]
            if self.model:
                args += ["-m", self.model]
            if req.json_schema is not None:
                sp = Path(cwd) / ".schema.json"
                sp.write_text(json.dumps(_strip_for_openai(req.json_schema)))
                args += ["--output-schema", str(sp)]
            prompt = (("Instructions:\n" + req.system + "\n\n") if req.system else "") + req.user_text
            r = _run(args + ["-"], prompt, cwd, self.timeout)
            text = out.read_text() if out.exists() else ""
        if r.returncode != 0 or not text.strip():
            tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-4:])
            raise AdapterUnavailable(f"{self.name}: {tail[:300] or 'no output'}")
        return ChatResult(text.strip(), self.name, None, None, time.monotonic() - t0, "stop")
