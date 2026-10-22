from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://hangil:hangil@localhost:54329/hangil"
    # Maps live in the oneload-data repo; locally it is cloned next to this one. The server sets MAP_DIR.
    map_dir: Path = ROOT.parent / "oneload-data" / "maps"
    cors_origins: list[str] = ["http://localhost:5173"]

    # Values proposed in API spec v0.3 §3; R4/R6 confirm them after measurement.
    session_idle_seconds: int = 2 * 60 * 60
    localization_ttl_seconds: int = 5 * 60
    idempotency_ttl_seconds: int = 24 * 60 * 60
    max_image_bytes: int = 2 * 1024 * 1024
    recent_messages: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
