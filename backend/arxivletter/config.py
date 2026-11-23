"""Runtime configuration. Every value is overridable by an environment variable (ARXIVLETTER_*), which is how the
Docker server, the launcher and the tests configure the app. Research interests live in a TOML profile
(config/profile.toml, editable in the UI) — see analysis/relevance.py."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get("ARXIVLETTER_" + name, default)


def _bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in _env(name, default).split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(REPO_ROOT / "data"))))
    profile_path: Path = field(default_factory=lambda: Path(_env("PROFILE", str(REPO_ROOT / "config" / "profile.toml"))))

    # ---- arXiv access (be polite: one connection, ≥3 s between requests, identify yourself)
    categories: tuple[str, ...] = field(default_factory=lambda: _list("CATEGORIES", "hep-ph,hep-th"))
    contact_email: str = field(default_factory=lambda: _env("CONTACT_EMAIL", ""))
    oai_url: str = field(default_factory=lambda: _env("OAI_URL", "https://oaipmh.arxiv.org/oai"))
    api_url: str = field(default_factory=lambda: _env("API_URL", "https://export.arxiv.org/api/query"))
    eprint_url: str = field(default_factory=lambda: _env("EPRINT_URL", "https://export.arxiv.org/e-print/"))
    html_url: str = field(default_factory=lambda: _env("HTML_URL", "https://arxiv.org/html/"))
    pdf_url: str = field(default_factory=lambda: _env("PDF_URL", "https://export.arxiv.org/pdf/"))
    request_interval_s: float = field(default_factory=lambda: float(_env("REQUEST_INTERVAL", "3.1")))
    http_timeout_s: float = field(default_factory=lambda: float(_env("HTTP_TIMEOUT", "90")))
    max_source_mb: float = field(default_factory=lambda: float(_env("MAX_SOURCE_MB", "60")))
    keep_source_archives: bool = field(default_factory=lambda: _bool("KEEP_SOURCE", False))  # full e-print (figures too)
    # gzipped copy of the source text files (tex/bbl/bib, ~50 kB/paper): `arxivletter reparse` after parser upgrades
    keep_source_text: bool = field(default_factory=lambda: _bool("KEEP_SOURCE_TEXT", True))

    # ---- scheduling
    harvest_every_min: float = field(default_factory=lambda: float(_env("HARVEST_EVERY_MIN", "60")))
    harvest_lookback_days: int = field(default_factory=lambda: int(_env("HARVEST_LOOKBACK_DAYS", "3")))
    # the scheduler only runs in roles that own the queue (all / server); off for tests and one-shot CLI runs
    scheduler: bool = field(default_factory=lambda: _bool("SCHEDULER", True))
    # the server (no model) may download and parse sources itself, so full texts are current when the PC is off
    fetch_on_server: bool = field(default_factory=lambda: _bool("FETCH_ON_SERVER", True))

    # ---- processing policy (selective escalation; see docs/ARCHITECTURE.md "Cost control")
    full_digest_min_relevance: float = field(default_factory=lambda: float(_env("FULL_DIGEST_MIN_RELEVANCE", "4.5")))
    digest_min_relevance: float = field(default_factory=lambda: float(_env("DIGEST_MIN_RELEVANCE", "5")))
    premium_min_relevance: float = field(default_factory=lambda: float(_env("PREMIUM_MIN_RELEVANCE", "6.5")))
    premium_daily_papers: int = field(default_factory=lambda: int(_env("PREMIUM_DAILY_PAPERS", "12")))
    brief_enabled: bool = field(default_factory=lambda: _bool("BRIEF", True))
    brief_top_n: int = field(default_factory=lambda: int(_env("BRIEF_TOP_N", "12")))
    reprocess_revisions_min_relevance: float = field(default_factory=lambda: float(_env("REVISION_MIN_RELEVANCE", "5")))

    # ---- models (same conventions as AXIOM: local llama.cpp server, subscription CLIs, optional paid APIs)
    local_llm_url: str = field(default_factory=lambda: _env("LOCAL_LLM_URL", "http://127.0.0.1:8081/v1"))
    local_model_name: str = field(default_factory=lambda: _env("LOCAL_MODEL", "qwen3.6-35b-a3b-q4kxl"))
    local_ctx_tokens: int = field(default_factory=lambda: int(_env("LOCAL_CTX", "16384")))  # per llama.cpp slot
    local_concurrency: int = field(default_factory=lambda: int(_env("LOCAL_CONCURRENCY", "2")))
    local_is_llamacpp: bool = field(default_factory=lambda: _bool("LOCAL_LLAMACPP", True))
    premium_order: tuple[str, ...] = field(default_factory=lambda: _list(
        "PREMIUM_ORDER", "claude-code:opus,claude-code:sonnet,codex:default,api:anthropic,api:openai"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-opus-4-5"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-5"))
    premium_concurrency: int = field(default_factory=lambda: int(_env("PREMIUM_CONCURRENCY", "2")))
    subscription_daily_limit: int = field(default_factory=lambda: int(_env("SUBSCRIPTION_DAILY_LIMIT", "60")))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT", "1800")))
    cloud_enabled_default: bool = field(default_factory=lambda: _bool("CLOUD", True))

    # ---- job runner
    job_lease_s: int = field(default_factory=lambda: int(_env("JOB_LEASE", "900")))
    llm_workers: int = field(default_factory=lambda: int(_env("LLM_WORKERS", "2")))
    premium_workers: int = field(default_factory=lambda: int(_env("PREMIUM_WORKERS", "1")))
    codex_digest_daily_ceiling: int = field(default_factory=lambda: int(_env("CODEX_DIGEST_DAILY_CEILING", "0")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "arxivletter.db"

    def paper_dir(self, paper_id: str) -> Path:
        return self.data_dir / "papers" / paper_id.replace("/", "_")

    @property
    def user_agent(self) -> str:
        contact = f"; mailto:{self.contact_email}" if self.contact_email else ""
        return f"arXivletter/0.1 (personal research digest{contact})"


settings = Settings()
