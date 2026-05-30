from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.core.config import get_settings

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]


class EncryptedTokenStore:
    """Server-side encrypted token storage. OAuth refresh tokens never go to Google Sheets."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.token_encryption_key:
            raise ValueError("TOKEN_ENCRYPTION_KEY is required before storing OAuth tokens")
        self.directory = settings.encrypted_token_dir
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fernet = Fernet(settings.token_encryption_key.encode("utf-8"))

    def write_channel_token(self, channel_id: str, token_json: str) -> Path:
        path = self.directory / f"{channel_id}.json.enc"
        path.write_bytes(self.fernet.encrypt(token_json.encode("utf-8")))
        return path

    def read_channel_token(self, channel_id: str) -> str:
        path = self.directory / f"{channel_id}.json.enc"
        return self.fernet.decrypt(path.read_bytes()).decode("utf-8")

    def read_credentials(self, channel_id: str) -> Credentials:
        return Credentials.from_authorized_user_info(__import__("json").loads(self.read_channel_token(channel_id)), YOUTUBE_SCOPES)

    def has_token(self, channel_id: str) -> bool:
        return (self.directory / f"{channel_id}.json.enc").exists()


class YouTubeOAuthService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.store = EncryptedTokenStore()

    def authorization_url(self, channel_id: str) -> str:
        flow = Flow.from_client_secrets_file(
            self.settings.google_oauth_client_secrets,
            scopes=YOUTUBE_SCOPES,
            redirect_uri=self.settings.youtube_oauth_redirect_uri,
        )
        url, _state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=channel_id,
        )
        return url

    def save_callback(self, authorization_response: str, channel_id: str) -> Path:
        flow = Flow.from_client_secrets_file(
            self.settings.google_oauth_client_secrets,
            scopes=YOUTUBE_SCOPES,
            redirect_uri=self.settings.youtube_oauth_redirect_uri,
        )
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        return self.store.write_channel_token(channel_id, credentials.to_json())
