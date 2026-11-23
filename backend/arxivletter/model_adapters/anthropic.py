# Adapted from AXIOM (../../Axiom/backend/axiom/model_adapters).
"""Anthropic Messages API adapter (premium). Only constructed when ANTHROPIC_API_KEY is set.
Uses the documented public API; it never piggybacks on a Claude Code subscription."""

from __future__ import annotations

import base64
import json
import time

import httpx

from arxivletter.model_adapters.base import AdapterUnavailable, ChatRequest, ChatResult


class AnthropicAdapter:
    model_class = "premium"
    supports_vision = True

    def __init__(self, api_key: str, model: str, timeout: float = 600):
        self.name = model
        self._client = httpx.Client(timeout=timeout, headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})

    def chat(self, req: ChatRequest) -> ChatResult:
        content: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(i.png).decode()}} for i in req.images]
        text = req.user_text
        if req.json_schema is not None:
            text += "\n\nRespond with only a JSON object matching this JSON Schema:\n" + json.dumps(req.json_schema)
        content.append({"type": "text", "text": text})
        body: dict = {"model": self.name, "max_tokens": req.max_tokens, "messages": [{"role": "user", "content": content}]}
        if req.system:
            body["system"] = req.system
        t0 = time.monotonic()
        try:
            r = self._client.post("https://api.anthropic.com/v1/messages", json=body)
        except httpx.HTTPError as e:
            raise AdapterUnavailable(str(e)) from e
        if r.status_code >= 400:
            raise RuntimeError(f"anthropic HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage") or {}
        return ChatResult(out, data.get("model", self.name), usage.get("input_tokens"),
                          usage.get("output_tokens"), time.monotonic() - t0, data.get("stop_reason"))
