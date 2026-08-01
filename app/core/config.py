"""
Centralized, typed application settings.

Rule enforced here: nothing else in the codebase reads os.environ directly.
Every config value is declared, typed, and validated exactly once, in this file.
This is what makes "is there a wildcard CORS origin" or "is the secret key
long enough" an answerable, testable question instead of a grep exercise.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # This app's domain vocabulary genuinely needs fields prefixed
        # "model_" (model_extraction, model_judge_primary, ...) since this
        # is a model-governance tool. Pydantic reserves that prefix for its
        # own internals by default — disabling the protection here is
        # correct, not a suppressed warning papering over a real conflict.
        protected_namespaces=(),
    )

    env: Literal["local", "staging", "production"] = "local"

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://attestor:attestor_local_dev_only@localhost:5432/attestor"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # --- Redis / job queue ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_regulatory: str = "attestor_regulatory_corpus"

    # --- Object storage ---
    minio_endpoint: str = "localhost:9000"
    minio_root_user: str = "attestor_admin"
    minio_root_password: str = "attestor_local_dev_only"
    minio_secure: bool = False
    evidence_bucket: str = "attestor-evidence"

    # --- Auth / Keycloak ---
    keycloak_url: str = "http://localhost:8080"
    keycloak_realm: str = "attestor"
    keycloak_client_id: str = "attestor-api"
    keycloak_audience: str = "attestor-api"

    # --- LLM providers ---
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    judge_provider: Literal["groq", "ollama", "openai"] = "groq"
    ollama_base_url: str = "http://localhost:11434"

    # Pinned model identifiers — never aliased. A governance tool cannot let
    # its own evidence shift underneath a signed report because a vendor
    # rolled a default model pointer forward.
    model_extraction: str = "claude-haiku-4-5"
    model_judge_primary: str = "claude-sonnet-5"
    model_judge_secondary_groq: str = "llama-3.1-8b-instant"

    # --- Rate limiting ---
    rate_limit_per_minute: int = 60

    # --- CORS ---
    cors_allowed_origins: list[str] = ["http://localhost:3000"]

    # --- Observability ---
    logfire_token: str = ""
    langsmith_api_key: str = ""
    langsmith_project: str = "attestor"

    @field_validator("cors_allowed_origins")
    @classmethod
    def no_wildcard_cors(cls, v: list[str]) -> list[str]:
        if "*" in v:
            raise ValueError(
                "Wildcard CORS origin is not permitted. List explicit origins."
            )
        return v

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
