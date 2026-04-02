from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Translation Memory Demo"
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/tm_demo"
    )
    upload_max_size_mb: int = 10
    default_similarity_threshold: float = 0.90

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
