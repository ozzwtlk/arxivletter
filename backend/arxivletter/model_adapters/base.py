# Adapted from AXIOM (../../Axiom/backend/axiom/model_adapters).
"""Provider-neutral chat interface. Adapters: local OpenAI-compatible (llama.cpp / vLLM / Ollama /v1),
Anthropic (premium, opt-in), OpenAI (premium, opt-in)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

ModelClass = Literal["local_vlm", "local_text", "premium"]


@dataclass
class ImagePart:
    png: bytes


@dataclass
class ChatRequest:
    system: str | None
    user_text: str
    images: list[ImagePart] = field(default_factory=list)
    json_schema: dict[str, Any] | None = None  # constrained decoding where supported
    max_tokens: int = 2048
    temperature: float = 0.0
    thinking: bool = False


@dataclass
class ChatResult:
    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    seconds: float
    finish_reason: str | None = None
    model_id: str | None = None  # model id that produced it (set by llm.call)


class ChatAdapter(Protocol):
    name: str
    model_class: ModelClass
    supports_vision: bool

    def chat(self, req: ChatRequest) -> ChatResult: ...


class AdapterUnavailable(RuntimeError):
    pass
