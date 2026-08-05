"""Typed application settings.

Configuration is read once at startup from the environment. Nothing in the
application reads ``os.environ`` directly, and nothing reads a mutable file at
runtime — which is what makes a deployment reproducible.
"""

from __future__ import annotations

import secrets
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET_KEY = "dev-only-insecure-secret-change-me"  # noqa: S105 - sentinel, not a secret


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class AIProvider(StrEnum):
    FAKE = "fake"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


class StorageBackend(StrEnum):
    LOCAL = "local"
    S3 = "s3"
    SUPABASE = "supabase"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -------------------------------------------------------------- runtime
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"

    # -------------------------------------------------------------- security
    secret_key: str = DEV_SECRET_KEY
    access_token_ttl_minutes: Annotated[int, Field(ge=1, le=120)] = 15
    refresh_token_ttl_days: Annotated[int, Field(ge=1, le=365)] = 30
    cors_origins: list[str] = Field(default_factory=list)

    # -------------------------------------------------------------- datastores
    database_url: str = "postgresql+asyncpg://handyai:handyai@localhost:5432/handyai"
    database_pool_size: Annotated[int, Field(ge=1, le=100)] = 10
    database_max_overflow: Annotated[int, Field(ge=0, le=100)] = 5
    redis_url: str = "redis://localhost:6379/0"

    # -------------------------------------------------------------- ai
    ai_provider: AIProvider = AIProvider.FAKE
    ai_fallback_providers: list[AIProvider] = Field(default_factory=list)
    ai_request_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 90.0
    ai_max_retries: Annotated[int, Field(ge=0, le=5)] = 2
    ai_cost_ceiling_usd: Annotated[float, Field(gt=0)] = 1.00
    ai_vision_max_images: Annotated[int, Field(ge=1, le=20)] = 8
    ai_vision_max_edge_px: Annotated[int, Field(ge=256, le=4096)] = 1568

    openai_api_key: str | None = None
    openai_vision_model: str | None = None
    openai_text_model: str | None = None
    openai_embedding_model: str | None = None

    anthropic_api_key: str | None = None
    anthropic_model: str | None = None

    gemini_api_key: str | None = None
    gemini_model: str | None = None

    # -------------------------------------------------------------- storage
    storage_backend: StorageBackend = StorageBackend.LOCAL
    storage_local_path: str = ".local_storage"
    storage_signed_url_ttl_seconds: Annotated[int, Field(ge=30, le=86400)] = 600
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    supabase_url: str | None = None
    supabase_service_key: str | None = None
    supabase_bucket: str | None = None

    # -------------------------------------------------------------- media
    media_max_image_bytes: int = 12 * 1024 * 1024
    media_max_video_bytes: int = 60 * 1024 * 1024
    media_retention_months: Annotated[int, Field(ge=1, le=120)] = 24

    # -------------------------------------------------------------- jobs
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # -------------------------------------------------------------- monitoring
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 0.1

    # -------------------------------------------------------------- privacy
    account_deletion_grace_days: Annotated[int, Field(ge=0, le=90)] = 30
    consent_version: str = "2026-08-01"

    # -------------------------------------------------------------- validators
    @field_validator("cors_origins", "ai_fallback_providers", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept comma-separated strings from the environment."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return upper

    @model_validator(mode="after")
    def _enforce_production_invariants(self) -> Self:
        """Fail fast rather than run production in an unsafe configuration."""
        if self.environment is not Environment.PRODUCTION:
            return self

        problems: list[str] = []
        if self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be a unique value of at least 32 characters")
        if self.ai_provider is AIProvider.FAKE:
            problems.append("AI_PROVIDER=fake serves fixtures and must not run in production")
        if self.storage_backend is StorageBackend.LOCAL:
            problems.append("STORAGE_BACKEND=local is not durable; use s3 or supabase")
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS must not contain a wildcard")
        if not self.database_url.startswith("postgresql+asyncpg://"):
            problems.append("DATABASE_URL must be an asyncpg Postgres DSN")
        problems.extend(self._missing_provider_keys())

        if problems:
            raise ValueError("Invalid production configuration:\n  - " + "\n  - ".join(problems))
        return self

    def _missing_provider_keys(self) -> list[str]:
        required: dict[AIProvider, tuple[str, object]] = {
            AIProvider.OPENAI: ("OPENAI_API_KEY", self.openai_api_key),
            AIProvider.ANTHROPIC: ("ANTHROPIC_API_KEY", self.anthropic_api_key),
            AIProvider.GEMINI: ("GEMINI_API_KEY", self.gemini_api_key),
        }
        problems: list[str] = []
        for provider in [self.ai_provider, *self.ai_fallback_providers]:
            entry = required.get(provider)
            if entry is not None and not entry[1]:
                problems.append(f"{entry[0]} is required for AI_PROVIDER={provider}")
        return problems

    # -------------------------------------------------------------- derived
    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def is_testing(self) -> bool:
        return self.environment is Environment.TEST

    @property
    def docs_url(self) -> str | None:
        """Interactive docs are never exposed in production."""
        return None if self.is_production else "/docs"

    @property
    def openapi_url(self) -> str | None:
        return None if self.is_production else "/openapi.json"

    @property
    def enabled_providers(self) -> tuple[AIProvider, ...]:
        """Primary provider first, then fallbacks, de-duplicated."""
        ordered = [self.ai_provider, *self.ai_fallback_providers]
        return tuple(dict.fromkeys(ordered))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so the environment is read exactly once. Tests call
    ``get_settings.cache_clear()`` after mutating the environment.
    """
    return Settings()


def generate_secret_key() -> str:
    """Helper for operators bootstrapping a new environment."""
    return secrets.token_urlsafe(48)
