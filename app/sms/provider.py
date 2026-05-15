from __future__ import annotations

from dataclasses import dataclass


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
