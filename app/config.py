"""Backend configuration, read from environment variables or `.env` (see `.env.example`)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"


def _default_sample_dir() -> str:
    """The workspace's shared sample-data folder when present, else the copy bundled with this repo."""
    shared = BACKEND_DIR.parent / "sample-data"
    return str(shared if shared.is_dir() else BACKEND_DIR / "demo_data")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AgriEvidence API"
    database_url: str = f"sqlite:///{(DATA_DIR / 'agritrial.db').as_posix()}"
    # Signs the login tokens. When empty, a random key is generated once and kept in data/.secret_key.
    secret_key: str = ""
    access_token_expire_minutes: int = 720
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Demo mode seeds the demo accounts and the sample data, and lists the accounts on the login page.
    demo_mode: bool = True
    sample_data_dir: str = Field(default_factory=_default_sample_dir)
    # Creates the first admin when the database has no users (use this outside demo mode).
    initial_admin_email: str = ""
    initial_admin_password: str = ""

    llm_service_url: str = "http://127.0.0.1:8100"
    llm_service_api_key: str = ""
    llm_service_timeout_s: float = 300.0

    upload_max_bytes: int = 5_000_000

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
