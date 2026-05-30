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

    ai_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_image_model: str = "gemini-2.5-flash-image"
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    gemini_tts_voice_name: str = "Kore"
    gemini_tts_timeout_seconds: int = 20
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    image_provider: str = "placeholder"
    tts_provider: str = "placeholder"
    narration_provider: str = "gemini"
    narration_allow_system_fallback: bool = True
    narration_speaking_style: str = "very slow, warm, kind Korean announcer tone"
    video_clip_provider: str = "placeholder"
    media_source_mode: str = "crawl_image"
    media_crawl_provider: str = "wikimedia"
    media_crawl_max_results: int = 6
    media_crawl_allowed_licenses: str = "cc0,public domain,cc-by,cc-by-sa"
    media_crawl_timeout_seconds: int = 20

    google_oauth_client_secrets: str = "./client_secret.json"
    youtube_oauth_redirect_uri: str = "http://localhost:8000/admin/youtube/oauth/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
