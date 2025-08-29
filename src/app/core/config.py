from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional

class Settings(BaseSettings):
    # App/Env
    ENV: str = "dev"
    APP_NAME: str = "agenticBE"
    PORT: int = 8080
    PUBLIC_BASE_URL: str = "http://localhost:8081"  # Caddy or API port you expose

    # Paths / Plugins / Models
    ARTIFACTS_DIR: str = "/srv/artifacts"
    PLUGINS_DIR: str = "/srv/plugins"
    MODELS_CONFIG: str = "/srv/config/models.yaml"
    SERVICE_FLAGS: str = "*"  # comma list of service_ids to allow (or "*")

    # DB / Redis
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@db:5432/agentic"
    # sync URL used only by alembic migrate container
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@db:5432/agentic"
    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_URL_QUEUE: str = "redis://redis:6379/1"

    # Auth / limits
    JWT_PUBLIC_KEY: Optional[str] = None
    RATE_LIMIT_USER_PER_MIN: int = 120
    RATE_LIMIT_TENANT_PER_MIN: int = 240

        # Speech / ASR / OCR providers
    AZURE_SPEECH_KEY: Optional[str] = None
    AZURE_SPEECH_REGION: Optional[str] = None
    ASR_DEVICE: str = "auto"  # "cpu" | "cuda" | "auto"
    PADDLE_OCR_BASE: Optional[str] = None


    # Optional S3 (unused in local)
    S3_ENDPOINT: Optional[str] = None
    S3_BUCKET: Optional[str] = None
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None

    # DB session tuning (ms)
    DB_STATEMENT_TIMEOUT_MS: int = 30000
    DB_IDLE_TX_TIMEOUT_MS: int = 60000

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

settings = Settings()
