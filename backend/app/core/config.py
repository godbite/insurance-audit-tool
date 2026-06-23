"""
Application configuration via pydantic-settings.
Loaded once at startup — never call os.environ.get() in business code.
"""
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Application ─────────────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    secret_key: str = "change-me-in-production"

    # ─── Database ────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://plum:plum_secret@localhost:5432/plum_claims"

    # ─── Redis ───────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ─── LLM Provider Layer ──────────────────────────────────────────────────
    # Comma-separated provider names in priority order.
    # Currently only "gemini" is live. Add "openai" or "anthropic" to expand.
    provider_order: str = "gemini"
    # OpenRouter
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "openrouter/free"

    provider_timeout_s: int = 45

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # OpenAI (optional fallback)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Groq
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Anthropic (optional third provider)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # ─── Langfuse ────────────────────────────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ─── MinIO ───────────────────────────────────────────────────────────────
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "plum-claims-docs"
    minio_secure: bool = False

    # ─── Policy ──────────────────────────────────────────────────────────────
    policy_file_path: str = "./policy_terms.json"

    # ─── Feature Flags ───────────────────────────────────────────────────────
    # Wire fault injection for TC011 testing
    simulate_component_failure: bool = False

    # Below this average field confidence, DOCUMENT_UNREADABLE is triggered (TC002)
    doc_quality_threshold: float = 0.4

    @field_validator("provider_order")
    @classmethod
    def validate_provider_order(cls, v: str) -> str:
        known = {"gemini", "openai", "anthropic", "stub", "groq", "openrouter"}
        names = [p.strip().lower() for p in v.split(",") if p.strip()]
        for name in names:
            if name not in known:
                raise ValueError(f"Unknown provider '{name}'. Known: {known}")
        return ",".join(names)

    def get_provider_list(self) -> list[str]:
        """Return ordered list of provider names from config."""
        return [p.strip().lower() for p in self.provider_order.split(",") if p.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings object. Use FastAPI Depends(get_settings)."""
    return Settings()
