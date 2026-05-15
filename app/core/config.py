from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    admin_username: str = "admin"
    admin_password: str = "change-me"

    google_application_credentials: str = Field(default="./service-account.json")
    google_sheets_spreadsheet_id: str = ""

    token_encryption_key: str = ""
    encrypted_token_dir: Path = Path("./encrypted_tokens")

    public_base_url: str = "http://localhost:8000"
    local_storage_dir: Path = Path("./storage")
    s3_bucket: str = ""
    s3_region: str = ""

    scheduler_interval_seconds: int = 300
    server_instance_id: str = "local-dev"
    sms_provider: str = "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
