"""Central configuration, loaded once from the environment / .env file.

Uses pydantic-settings so every tunable (LLM model id, API keys, database URL)
lives in one place and can be overridden without touching code.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path to the `backend/` directory, used for the .env file and the
# persisted Chroma vector store so paths work regardless of the CWD.
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings resolved from environment variables / .env."""

    # --- LLM ---
    # LiteLLM-style "provider/model" string. Gemini is the default because its
    # free tier has the best rate-limit headroom for a chatty multi-agent crew.
    # gemini-3.1-flash-lite is Google's current cost-efficient, high-volume model.
    model: str = "gemini/gemini-3.1-flash-lite"
    judge_model: str = "gemini/gemini-3.1-flash-lite"

    # Optional stronger model for the quality-critical agents (Planner +
    # Reviewer) only, leaving the four research agents on the fast `model`.
    # Leave unset to run every agent on `model` (keeps the crew single-model,
    # so the multi-agent-vs-baseline comparison stays unconfounded).
    # Example: vertex_ai/gemini-3.5-flash
    quality_model: str | None = None

    # Optional model for the manager agent when CREW_PROCESS=hierarchical.
    # Falls back to `model` when unset.
    manager_model: str | None = None

    # --- Crew topology (experiment toggles; defaults reproduce the report) ---
    # Process: "sequential" (default, deterministic, fewest LLM calls) or
    # "hierarchical" (a manager agent delegates to the six specialists — more
    # calls, non-deterministic ordering). See report §6.3 / §11.
    crew_process: str = "sequential"
    # Persist CrewAI's built-in long-term memory across runs using a LOCAL
    # embedder (no external key). Off by default so the demo stays dependency-
    # light and each generation is independent (report §11).
    crew_memory: bool = False
    # Attach the RAG knowledge_base tool to the agents. Set False for the
    # RAG-ablation control condition (crew with retrieval disabled) that
    # isolates the knowledge base's contribution (report §8/§11).
    rag_enabled: bool = True

    # Provider keys (only the one matching `model` needs to be set).
    google_api_key: str | None = None
    groq_api_key: str | None = None
    ollama_api_base: str | None = None

    # Vertex AI (used when MODEL starts with `vertex_ai/`). Auth is via Google
    # Application Default Credentials (gcloud auth application-default login);
    # no API key. Billed to the project's Cloud account (e.g. $300 free trial),
    # and NOT subject to the Developer-API free-tier 15 req/min limit.
    vertex_project: str | None = None
    vertex_location: str = "us-central1"

    # --- Database ---
    database_url: str = (
        "postgresql+psycopg2://concierge:concierge@localhost:5432/concierge"
    )

    # --- RAG ---
    chroma_dir: str = str(BACKEND_DIR / "app" / "rag" / "chroma_store")
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # --- Rate limiting ---
    # Free Gemini Flash-Lite allows ~15 requests/min. A crew run bursts many
    # calls, so we throttle the crew well below that ceiling to leave headroom
    # for the baseline + judge calls that share the same per-minute budget
    # during an evaluation sweep.
    max_rpm: int = 8

    # --- Reliability ---
    # Per-LLM-call request timeout (seconds). Without it a single stalled
    # provider connection hangs the WHOLE run indefinitely (litellm reports
    # "timed out after None seconds"); with it the call fails fast and is
    # retried instead. Applies on LiteLLM-routed providers (vertex_ai/, groq/,
    # ollama/). Set to 0 to disable.
    request_timeout: float = 120.0
    # LiteLLM call-level retries on transient failures (timeouts, 5xx) before
    # the error propagates to the crew's own whole-kickoff retry.
    llm_num_retries: int = 2

    # --- Evaluation ---
    # Delay between successive (scenario, system) runs so the per-minute request
    # window clears before the next burst. Raise this if you still see 429s.
    eval_request_delay: float = 12.0

    # --- API security (off by default; local coursework demo) ---
    # If set, every route except /health requires a matching `X-API-Key`
    # header. Left unset for local development so the demo keeps working with
    # zero configuration; set this before exposing the API beyond localhost.
    api_key: str | None = None
    # Comma-separated list of origins allowed to call the API from a browser.
    # Defaults to the Next.js dev server port (see frontend/package.json).
    # Deliberately NOT "*" — a wildcard would let any website's JS call this
    # API using the visiting browser's network access.
    allowed_origins: str = (
        "http://localhost:3000,http://localhost:3001,http://localhost:3002,"
        "http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:3002,"
        "http://localhost:5173,http://127.0.0.1:5173"
    )
    # Per-client-IP throttle on the LLM-backed generation endpoints
    # (/chat, /chat/stream, /itineraries) — each one triggers a multi-call
    # (or, for the crew, multi-agent) LLM run, so without a limit here a
    # single client can drive unbounded API cost / load with no server-side
    # ceiling (the existing `max_rpm` only throttles the crew's own calls
    # against the *provider's* limit, not a client against *this server*).
    rate_limit_generate: str = "20/minute"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read the environment only once)."""
    return Settings()
