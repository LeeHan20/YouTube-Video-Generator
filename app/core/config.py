from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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
    google_api_timeout_seconds: int = 30

    token_encryption_key: str = ""
    encrypted_token_dir: Path = Path("./encrypted_tokens")

    public_base_url: str = "http://localhost:8000"
    local_storage_dir: Path = Path("./storage")
    s3_bucket: str = ""
    s3_region: str = ""

    scheduler_interval_seconds: int = 300
    server_instance_id: str = "local-dev"
    video_source_retention_days: int = 14
    sms_provider: str = "mock"
    sms_format_path: Path = Path("./sys_prompts/sms_format.md")
    sms_max_bytes: int = 90
    naver_sens_access_key: str = ""
    naver_sens_secret_key: str = ""
    naver_sens_service_id: str = ""
    naver_sens_from_number: str = ""
    naver_sens_timeout_seconds: int = 10

    ai_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_image_model: str = "gemini-2.5-flash-image"
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    gemini_tts_voice_name: str = "Kore"
    gemini_tts_timeout_seconds: int = 20
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    supertone_api_key: str = ""
    supertone_voice_id: str = ""
    supertone_model: str = "sona_speech_2"
    supertone_language: str = "ko"
    supertone_style: str = "neutral"
    supertone_output_format: str = "wav"
    supertone_timeout_seconds: int = 30
    supertone_speed: float = 0.9

    image_provider: str = "placeholder"
    tts_provider: str = "placeholder"
    narration_provider: str = "supertone"
    narration_allow_system_fallback: bool = True
    narration_speaking_style: str = "very slow, warm, kind Korean announcer tone"
    aeneas_python_path: str = "./aeneas/venv/bin/python"
    aeneas_repo_path: str = "./aeneas"
    aeneas_language: str = "kor"
    aeneas_runtime_config: str = "tts=macos"
    aeneas_task_extra_config: str = (
        "task_adjust_boundary_algorithm=rateaggressive"
        "|task_adjust_boundary_rate_value=14.0"
        "|is_audio_file_detect_head_max=0.500"
        "|is_audio_file_detect_tail_max=0.500"
    )
    aeneas_timeout_seconds: int = 60
    video_clip_provider: str = "placeholder"
    media_source_mode: str = "crawl_image"
    media_crawl_provider: str = "wikimedia"
    media_crawl_max_results: int = 6
    media_crawl_allowed_licenses: str = "cc0,public domain,cc-by,cc-by-sa"
    media_crawl_timeout_seconds: int = 20
    google_image_search_api_key: str = ""
    google_image_search_cx: str = ""
    google_image_search_rights: str = ""
    unsplash_access_key: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    topic_generation_multiplier: int = 3

    google_oauth_client_secrets: str = "./client_secret.json"
    youtube_oauth_redirect_uri: str = "http://localhost:8000/admin/youtube/oauth/callback"

    @field_validator(
        "app_port",
        "google_api_timeout_seconds",
        "scheduler_interval_seconds",
        "video_source_retention_days",
        "sms_max_bytes",
        "naver_sens_timeout_seconds",
        "gemini_tts_timeout_seconds",
        "supertone_timeout_seconds",
        "aeneas_timeout_seconds",
        "media_crawl_max_results",
        "media_crawl_timeout_seconds",
        "topic_generation_multiplier",
        mode="before",
    )
    @classmethod
    def _clean_int_env_value(cls, value):
        if isinstance(value, str):
            return value.strip().rstrip("\\").strip()
        return value

    @field_validator("supertone_speed", mode="before")
    @classmethod
    def _clean_float_env_value(cls, value):
        if isinstance(value, str):
            return value.strip().rstrip("\\").strip()
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
