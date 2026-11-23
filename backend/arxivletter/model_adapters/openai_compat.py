# Adapted from AXIOM (../../Axiom/backend/axiom/model_adapters).
"""OpenAI-compatible chat adapter. Used for the local llama.cpp server (and optionally OpenAI itself)."""

from __future__ import annotations

import base64
import re
import time

import httpx

from arxivletter.model_adapters.base import AdapterUnavailable, ChatRequest, ChatResult, ModelClass
from arxivletter.model_adapters.gbnf import DECODING, UnsupportedSchema, schema_to_gbnf


class OpenAICompatAdapter:
    def __init__(self, base_url: str, model: str, model_class: ModelClass, api_key: str | None = None,
                 supports_vision: bool = True, timeout: float = 600, local_llamacpp: bool = True):
        self.base_url = base_url.rstrip("/")
        self.name = model
        self.model_class = model_class
        self.supports_vision = supports_vision
        self.local_llamacpp = local_llamacpp
        # llama.cpp gets our LaTeX-tolerant grammar instead of its json_schema mode (see gbnf.py)
        self.decoding = DECODING if local_llamacpp else ""
        self._client = httpx.Client(timeout=timeout, headers=(
            {"Authorization": f"Bearer {api_key}"} if api_key else {}))

    def health(self) -> bool:
        try:
            return self._client.get(self.base_url + "/models", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def chat(self, req: ChatRequest) -> ChatResult:
        content: list[dict] | str
        if req.images:
            content = [{"type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + base64.b64encode(i.png).decode()}}
                       for i in req.images]
            content.append({"type": "text", "text": req.user_text})
        else:
            content = req.user_text
        messages = ([{"role": "system", "content": req.system}] if req.system else []) + [
            {"role": "user", "content": content}]
        body: dict = {"model": self.name, "messages": messages, "max_tokens": req.max_tokens,
                      "temperature": req.temperature}
        if self.local_llamacpp:
            body["chat_template_kwargs"] = {"enable_thinking": bool(req.thinking)}
        if req.json_schema is not None:
            grammar = None
            if self.local_llamacpp:
                try:
                    grammar = schema_to_gbnf(req.json_schema, reasoning=bool(req.thinking))
                except UnsupportedSchema:
                    grammar = None
            if grammar:
                body["grammar"] = grammar
            else:
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": "out", "schema": req.json_schema, "strict": True}}
        t0 = time.monotonic()
        try:
            r = self._client.post(self.base_url + "/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise AdapterUnavailable(f"{self.name}: {e}") from e
        if r.status_code >= 400:
            raise RuntimeError(f"{self.name} HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return ChatResult(
            # llama.cpp returns reasoning separately (reasoning_content); strip inline <think> blocks if a
            # server is configured to leave them in the content
            text=re.sub(r"<think>.*?</think>", "", choice["message"].get("content") or "", flags=re.S).strip(),
            model=data.get("model") or self.name,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            seconds=time.monotonic() - t0,
            finish_reason=choice.get("finish_reason"),
        )
