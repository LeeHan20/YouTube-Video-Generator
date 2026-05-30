from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings


@dataclass
class AiResponse:
    text: str
    provider: str
    model: str


class AiClient:
    def generate_text(self, prompt: str, system: str = "", max_tokens: int = 2048) -> AiResponse:
        raise NotImplementedError

    def generate_json(self, prompt: str, system: str = "", max_tokens: int = 2048) -> dict[str, Any]:
        response = self.generate_text(prompt=prompt, system=system, max_tokens=max_tokens)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        return json.loads(text)


class GeminiClient(AiClient):
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.gemini_api_key
        self.model = settings.gemini_model

    def generate_text(self, prompt: str, system: str = "", max_tokens: int = 2048) -> AiResponse:
        if not self.api_key:
            return FallbackAiClient("gemini", self.model).generate_text(prompt, system, max_tokens)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        with httpx.Client(timeout=60) as client:
            result = client.post(url, params={"key": self.api_key}, json=payload)
            result.raise_for_status()
        data = result.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts)
        return AiResponse(text=text, provider="gemini", model=self.model)


class ClaudeClient(AiClient):
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.anthropic_api_key
        self.model = settings.anthropic_model

    def generate_text(self, prompt: str, system: str = "", max_tokens: int = 2048) -> AiResponse:
        if not self.api_key:
            return FallbackAiClient("claude", self.model).generate_text(prompt, system, max_tokens)
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        with httpx.Client(timeout=60) as client:
            result = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            result.raise_for_status()
        data = result.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return AiResponse(text=text, provider="claude", model=self.model)


class FallbackAiClient(AiClient):
    def __init__(self, provider: str = "fallback", model: str = "local-template") -> None:
        self.provider = provider
        self.model = model

    def generate_text(self, prompt: str, system: str = "", max_tokens: int = 2048) -> AiResponse:
        del system, max_tokens
        return AiResponse(text=self._fallback_text(prompt), provider=self.provider, model=self.model)

    @staticmethod
    def _fallback_text(prompt: str) -> str:
        return (
            "AI API 키가 설정되지 않아 기본 템플릿 응답을 사용합니다.\n\n"
            f"요청 요약: {prompt[:500]}\n\n"
            "50대 이상 시청자가 이해하기 쉬운 짧은 문장과 차분한 흐름을 우선합니다."
        )


def get_ai_client(provider: str | None = None) -> AiClient:
    settings = get_settings()
    selected = (provider or settings.ai_provider).lower()
    if selected in {"claude", "anthropic"}:
        return ClaudeClient()
    if selected == "fallback":
        return FallbackAiClient()
    return GeminiClient()
