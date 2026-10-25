from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    disha_env: Literal["dev", "test", "staging", "production"] = "dev"

    database_url_admin: str = Field(
        default="postgresql+asyncpg://disha_admin:disha_admin_dev@localhost:5432/disha"
    )
    database_url_app: str = Field(
        default="postgresql+asyncpg://disha_app:disha_app_dev@localhost:5432/disha"
    )

    disha_fernet_key: SecretStr = Field(default=SecretStr(""))

    disha_log_level: str = "INFO"
    disha_log_fmt: Literal["text", "json"] = "text"

    disha_ui_token: SecretStr = Field(default=SecretStr(""))

    gemini_api_key: SecretStr = Field(default=SecretStr(""))
    anthropic_api_key: SecretStr = Field(default=SecretStr(""))
    openai_api_key: SecretStr = Field(default=SecretStr(""))

    # Web-research sourcing provider
    serper_api_key: SecretStr = Field(default=SecretStr(""))   # serper.dev — Google Search API
    hunter_api_key: SecretStr = Field(default=SecretStr(""))   # hunter.io  — email finder (optional)

    # Unipile — LinkedIn outreach automation (optional). DSN is the per-account
    # base URL Unipile assigns, e.g. https://api12.unipile.com:13443
    unipile_api_key: SecretStr = Field(default=SecretStr(""))
    unipile_dsn: str = ""

    # Gmail OAuth app (Google Cloud Console → Credentials → OAuth client ID)
    gmail_client_id: str = ""
    gmail_client_secret: SecretStr = Field(default=SecretStr(""))
    # Public URLs used by the OAuth web flow: API builds its own callback URL,
    # then bounces the browser back to the UI's settings page when done.
    disha_api_url: str = "http://localhost:8000"
    disha_ui_url: str = "http://localhost:3000"

    # Worker loop intervals (seconds)
    worker_sequence_interval: int = 900   # process due cadence steps
    worker_poll_interval: int = 300       # poll Gmail for replies

    postgres_user: str = "postgres"
    postgres_password: SecretStr = SecretStr("postgres")
    postgres_db: str = "disha"
    postgres_port: int = 5432

    project_root: Path = Path(__file__).resolve().parent.parent.parent

    @property
    def seeds_dir(self) -> Path:
        return Path(__file__).resolve().parent / "seeds"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
