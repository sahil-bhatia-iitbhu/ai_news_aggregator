"""Application settings. Load from environment or .env."""
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root (directory containing app/ and pyproject.toml)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """App settings. Loaded from .env in project root or environment."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE if _ENV_FILE.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # YouTube Data API v3 (required for listing channel videos)
    youtube_api_key: Optional[str] = None

    # Max videos to consider per channel per run (fetched from API, then filtered by lookback)
    youtube_max_videos_per_channel: int = 50

    # Only include videos uploaded in the last N days (weekly pipeline)
    youtube_lookback_days: int = 7

    # OpenAI research scraper: only include items from last N days
    openai_research_lookback_days: int = 7


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
