from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet

from app.core.config import get_settings


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
