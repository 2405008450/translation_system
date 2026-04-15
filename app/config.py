from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Translation Memory Demo"
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/tm_demo" 
    )
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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
