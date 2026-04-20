from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET_KEY = "change-me-in-production"


class Settings(BaseSettings):
    app_name: str = "Translation Memory Demo"
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/tm_demo" 
    )
    file_storage_dir: str = "data/file_records"
    upload_max_size_mb: int = 10
    default_similarity_threshold: float = 0.60
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-3-flash-preview"
    llm_timeout_seconds: float = 60.0
    llm_max_concurrency: int = 5
    llm_temperature: float = 0.2
    llm_retry_attempts_per_provider: int = 2
    redis_url: str | None = None
    tm_vector_enabled: bool = True
    tm_vector_dimensions: int = 128
    tm_vector_candidate_limit: int = 6
    tm_vector_similarity_floor: float = 0.45
    tm_vector_weight: float = 0.35
    jwt_secret_key: str = DEFAULT_JWT_SECRET_KEY
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    log_level: str = "INFO"
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:19003",
            "http://localhost:19003",
        ]
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_runtime_settings(settings: Settings) -> None:
    if settings.jwt_secret_key == DEFAULT_JWT_SECRET_KEY:
        raise RuntimeError(
            "JWT_SECRET_KEY 仍为默认值，请在 .env 或环境变量中设置独立密钥后再启动服务。"
        )
