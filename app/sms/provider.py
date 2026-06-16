from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from time import time

import httpx

from app.core.config import get_settings


@dataclass
class SmsResult:
    success: bool
    provider_message_id: str = ""
    error_message: str = ""


class SmsProvider:
    def send(self, phone_number: str, message: str) -> SmsResult:
        raise NotImplementedError


class MockSmsProvider(SmsProvider):
    def send(self, phone_number: str, message: str) -> SmsResult:
        print(f"[mock-sms] to={phone_number} message={message}", flush=True)
        return SmsResult(success=True, provider_message_id="mock")


class NaverSensSmsProvider(SmsProvider):
    def __init__(self) -> None:
        self.settings = get_settings()
        missing = [
            name
            for name, value in {
                "NAVER_SENS_ACCESS_KEY": self.settings.naver_sens_access_key,
                "NAVER_SENS_SECRET_KEY": self.settings.naver_sens_secret_key,
                "NAVER_SENS_SERVICE_ID": self.settings.naver_sens_service_id,
                "NAVER_SENS_FROM_NUMBER": self.settings.naver_sens_from_number,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"Missing Naver SENS SMS settings: {', '.join(missing)}")

    def send(self, phone_number: str, message: str) -> SmsResult:
        message_bytes = len(message.encode("utf-8"))
        if message_bytes > self.settings.sms_max_bytes:
            return SmsResult(
                success=False,
                error_message=f"SMS message is {message_bytes} bytes; max is {self.settings.sms_max_bytes} bytes",
            )
        timestamp = str(int(time() * 1000))
        path = f"/sms/v2/services/{self.settings.naver_sens_service_id}/messages"
        url = f"https://sens.apigw.ntruss.com{path}"
        body = {
            "type": "SMS",
            "contentType": "COMM",
            "countryCode": "82",
            "from": self._digits(self.settings.naver_sens_from_number),
            "content": message,
            "messages": [{"to": self._digits(phone_number), "content": message}],
        }
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "x-ncp-apigw-timestamp": timestamp,
            "x-ncp-iam-access-key": self.settings.naver_sens_access_key,
            "x-ncp-apigw-signature-v2": self._signature(timestamp, path),
        }
        try:
            with httpx.Client(timeout=self.settings.naver_sens_timeout_seconds) as client:
                response = client.post(url, headers=headers, json=body)
                response.raise_for_status()
            payload = response.json()
            return SmsResult(success=True, provider_message_id=str(payload.get("requestId", "")))
        except Exception as exc:
            return SmsResult(success=False, error_message=str(exc))

    def _signature(self, timestamp: str, path: str) -> str:
        message = f"POST {path}\n{timestamp}\n{self.settings.naver_sens_access_key}"
        digest = hmac.new(
            self.settings.naver_sens_secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    @staticmethod
    def _digits(value: str) -> str:
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits.startswith("82") and len(digits) > 10:
            return "0" + digits[2:]
        if len(digits) == 10 and digits.startswith("10"):
            return "0" + digits
        return digits


def sms_provider_from_settings() -> SmsProvider:
    provider = get_settings().sms_provider.strip().lower()
    if provider in {"mock", ""}:
        return MockSmsProvider()
    if provider in {"naver_sens", "sens", "naver"}:
        return NaverSensSmsProvider()
    raise ValueError(f"Unsupported SMS_PROVIDER: {provider}")
