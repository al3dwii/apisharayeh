from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    # App
    ENV: str = Field(default="dev")
    LOG_LEVEL: str = "INFO"
    PORT: int = 8080

    # URLs / links
    # Used to build absolute links to things like /artifacts/*
    # e.g. "http://localhost:8080" in dev, or your public domain in prod
    PUBLIC_BASE_URL: str = ""

    # Storage
    ARTIFACTS_DIR: str = "/data/artifacts"  # must exist or be creatable
    S3_BUCKET: str | None = None
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    AWS_REGION: str = "us-east-1"

    # Infra
    DATABASE_URL: str
    REDIS_URL: str
    REDIS_URL_QUEUE: str

    # LLMs
    OPENAI_API_KEY: str | None = None
    LOCAL_OPENAI_BASE_URL: str | None = None
    LOCAL_OPENAI_API_KEY: str | None = None

    # Observability
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None
    OTEL_SERVICE_NAME: str = "agentic-backend"

    # Auth/limits
    JWT_PUBLIC_KEY: str | None = None
    TOKEN_BUDGET_PER_DAY: int = 200_000

    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()

# from __future__ import annotations

# from pydantic_settings import BaseSettings, SettingsConfigDict
# from pydantic import Field


# class Settings(BaseSettings):
#     # ---- Core ----
#     ENV: str = Field(default="dev")
#     LOG_LEVEL: str = "INFO"
#     PORT: int = 8080

#     # ---- DB / Redis ----
#     # Allow either REDIS_URL or REDIS_URL_QUEUE; prefer *_QUEUE if both exist.
#     REDIS_URL: str | None = None
#     REDIS_URL_QUEUE: str | None = None
#     DATABASE_URL: str = Field(
#         default="postgresql+asyncpg://postgres:postgres@db:5432/agentic"
#     )

#     # ---- Auth / Quotas ----
#     TOKEN_BUDGET_PER_DAY: int = 200_000
#     JWT_PUBLIC_KEY: str | None = None

#     # ---- Observability ----
#     OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None
#     OTEL_SERVICE_NAME: str = "agentic-backend"

#     # ---- Integrations ----
#     OPENAI_API_KEY: str | None = None
#     SERPAPI_KEY: str | None = None

#     # ---- Storage / Artifacts (used by doc2deck) ----
#     ARTIFACTS_DIR: str = "/data/artifacts"  # local fallback when S3 is not set
#     S3_BUCKET: str | None = None
#     AWS_ACCESS_KEY_ID: str | None = None
#     AWS_SECRET_ACCESS_KEY: str | None = None
#     AWS_REGION: str | None = None

#     # Pydantic v2 settings
#     model_config = SettingsConfigDict(
#         env_file=".env",
#         extra="ignore",         # ignore unexpected env vars instead of erroring
#         case_sensitive=False,   # env var keys are not case-sensitive
#     )

#     @property
#     def REDIS_URL_EFFECTIVE(self) -> str:
#         """Preferred Redis URL for brokers/backends."""
#         return self.REDIS_URL_QUEUE or self.REDIS_URL or "redis://redis:6379/0"


# settings = Settings()
