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

    # --- Model tiers (see app/routing.py) ---
    # The "cheapest capable model per task" mechanism. Each tier is a LiteLLM
    # provider/model string; each task maps to a tier via `task_tiers`. Unset
    # tiers fall back: quality -> standard -> cheap -> `model` (the legacy
    # single-model setting), so existing configs keep working unchanged.
    tier_cheap: str | None = None
    tier_standard: str | None = None
    tier_quality: str | None = None
    # task=tier assignments, comma-separated. Tasks: extraction, reply, answer,
    # research, planner, reviewer, judge. See routing._DEFAULT_TASK_TIERS.
    task_tiers: str = (
        "extraction=cheap,reply=cheap,answer=standard,research=cheap,"
        "planner=quality,reviewer=quality,judge=standard"
    )

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
    # SQLAlchemy pool sizing, per process. With WEB_CONCURRENCY uvicorn workers
    # each holding their own pool, total base connections = WEB_CONCURRENCY *
    # db_pool_size (defaults: 2 * 5 = 10) against Postgres's default
    # max_connections=100 — leaves headroom for the worker + migrations/psql.
    # Raise pool_size only in lockstep with Postgres's max_connections.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- Redis / job queue (arq) ---
    # Live progress + queue backend. Postgres remains the source of truth for
    # job rows and results; Redis holds only ephemeral progress streams.
    redis_url: str = "redis://localhost:6379/0"
    # In-process job concurrency for the worker. On a 4 GB box this is 2 —
    # two kickoffs share one torch/Chroma load. See docker-compose worker.
    worker_max_jobs: int = 2
    # Hard ceiling on a single crew run before the worker aborts it (seconds).
    job_timeout: float = 900.0

    # --- Auth ---
    # HS256 signing secret for short-lived access tokens. MUST be overridden in
    # any non-local environment. A fixed dev default keeps localhost zero-config.
    jwt_secret: str = "dev-insecure-change-me"
    access_token_ttl_seconds: int = 600          # 10 minutes
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days
    # Set True behind TLS (production). Controls the Secure cookie flag; keep
    # False for plain-http localhost or the browser drops the cookie.
    cookies_secure: bool = False
    # Frontend origin the OAuth callback redirects back to.
    frontend_url: str = "http://localhost:5173"

    # --- Google OAuth (optional; unset disables the Google button) ---
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    # Where Google redirects after consent; must be registered in the console.
    google_oauth_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # --- Delivery / integrations (Phase 4; unset = disabled) ---
    resend_api_key: str | None = None
    email_from: str = "AI Travel Concierge <onboarding@resend.dev>"
    telegram_bot_token: str | None = None
    # Registration OTP controls. Keep the secret separate in production when
    # possible; falling back to JWT_SECRET keeps local development simple.
    email_verification_secret: str | None = None
    otp_ttl_seconds: int = 600
    otp_resend_cooldown_seconds: int = 60
    otp_max_attempts: int = 5
    # Avatar storage. "local" is safe for development; production selects R2
    # explicitly and fails requests cleanly when its credentials are missing.
    avatar_storage_backend: str = "local"
    avatar_local_dir: str = str(BACKEND_DIR / "data" / "avatars")
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None

    # --- Grounded data sources (Phase 2; each is independently optional — an
    # unset key just makes that tool report itself unavailable and the agent
    # falls back to prior knowledge / DuckDuckGo, same resilience pattern as
    # every other tool in crew/tools.py) ---
    # Tavily — LLM-oriented web search that returns real URLs (unlike the
    # DuckDuckGo fallback, whose LangChain wrapper returns snippets only).
    # Get a key at https://tavily.com (free tier available).
    tavily_api_key: str | None = None
    # Google Places (legacy Text Search API — maps.googleapis.com/maps/api/place).
    # Verifies a named restaurant/hotel/attraction is real and returns its
    # address, rating, and price level. Get a key at
    # https://console.cloud.google.com/google/maps-apis — enable "Places API"
    # (the classic one, not only "Places API (New)") on that key.
    google_places_api_key: str | None = None
    # Travelpayouts Data API (cached Aviasales/Jetradar search-price data, NOT
    # live booking search — the live search API now requires a 50k+ MAU
    # affiliate project). Free: register at https://www.travelpayouts.com and
    # connect the Aviasales program to get a token.
    travelpayouts_token: str | None = None
    # Attached to outbound ticket links per Travelpayouts' affiliate terms.
    # Falls back to their public demo marker if unset.
    travelpayouts_marker: str = "621166"

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
    # /auth/login has no other brute-force/enumeration defense (Argon2 itself
    # is the only cost an attacker pays), so it needs its own tight ceiling.
    rate_limit_login: str = "5/minute"
    # Reverse geocoding is a real (if cheap) external API call per hit —
    # capped independently of rate_limit_generate since it's not LLM cost.
    rate_limit_geocode: str = "10/minute"

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
